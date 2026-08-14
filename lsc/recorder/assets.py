"""RecordingAsset compatibility view for single-file and segmented media."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from lsc.platforms.redaction import redact_text

from .manifest import ManifestStore, RecordingManifest, SegmentEntry
from .timeline import TimelineMapper


@dataclass(frozen=True, slots=True)
class RecordingAsset:
    """A manifest-backed recording consumable by post-processing services."""

    manifest_path: str
    manifest: RecordingManifest
    timeline: TimelineMapper

    @classmethod
    def load(cls, manifest_path: str) -> RecordingAsset:
        resolved = str(Path(manifest_path).resolve())
        manifest = ManifestStore(resolved).load()
        return cls(resolved, manifest, TimelineMapper(manifest))

    @classmethod
    def recover(cls, manifest_path: str) -> RecordingAsset:
        resolved = str(Path(manifest_path).resolve())
        manifest = ManifestStore(resolved).recover()
        return cls(resolved, manifest, TimelineMapper(manifest))

    @classmethod
    def from_file(
        cls,
        media_path: str,
        *,
        room_session_id: str = "",
        platform_id: str = "legacy",
    ) -> RecordingAsset:
        """Expose an existing MP4/TS file through the manifest contract.

        The compatibility view is in-memory and never overwrites the legacy
        file.  Callers can therefore migrate post-processing independently
        of the recording writer.
        """
        media = Path(media_path).resolve()
        manifest = RecordingManifest.create(
            room_session_id=room_session_id,
            platform_id=platform_id,
        )
        entry = manifest.add_segment(media.name)
        size = media.stat().st_size if media.is_file() else 0
        manifest.complete_segment(
            entry,
            duration_ms=0,
            size_bytes=size,
            validation={"legacy_single_file": True, "size_positive": size > 0},
        )
        manifest.close(state="COMPLETE")
        virtual_manifest = str(media.with_name(f".{media.name}.manifest.json"))
        return cls(virtual_manifest, manifest, TimelineMapper(manifest))

    from_legacy_file = from_file

    @property
    def complete_segments(self) -> tuple[SegmentEntry, ...]:
        return tuple(sorted(
            (
                item
                for item in self.manifest.segments
                if item.state in {"COMPLETE", "RECOVERED"}
            ),
            key=lambda item: item.sequence,
        ))

    @property
    def partial_segments(self) -> tuple[SegmentEntry, ...]:
        return tuple(sorted(
            (
                item
                for item in self.manifest.segments
                if item.state in {"WRITING", "CORRUPT", "MISSING"}
            ),
            key=lambda item: item.sequence,
        ))

    def segment_paths(self, *, include_incomplete: bool = False) -> tuple[str, ...]:
        root = Path(self.manifest_path).parent
        entries = self.manifest.segments if include_incomplete else self.complete_segments
        paths: list[str] = []
        for entry in entries:
            path = (root / entry.path).resolve()
            try:
                path.relative_to(root.resolve())
            except ValueError:
                continue
            if path.is_file() and os.path.getsize(path) > 0:
                paths.append(str(path))
        return tuple(paths)

    @staticmethod
    def _probe_segment(
        path: str,
        *,
        ffprobe_path: str,
        timeout: float,
    ) -> tuple[bool, dict[str, object]]:
        command = [
            ffprobe_path or "ffprobe",
            "-v", "error",
            "-show_streams", "-show_format",
            "-of", "json", path,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            payload = json.loads(completed.stdout or "{}")
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return False, {"readable": False, "error": str(exc)[:240]}
        streams = payload.get("streams", []) if isinstance(payload, dict) else []
        fmt = payload.get("format", {}) if isinstance(payload, dict) else {}
        if not isinstance(streams, list) or not isinstance(fmt, dict):
            return False, {"readable": False, "reason": "invalid_probe_payload"}
        has_video = any(
            isinstance(stream, dict) and stream.get("codec_type") == "video"
            for stream in streams
        )
        try:
            duration = float(fmt.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        valid = completed.returncode == 0 and has_video and duration > 0
        return valid, {
            "readable": valid,
            "has_video": has_video,
            "duration_ms": max(0, int(duration * 1000)),
            "format": str(fmt.get("format_name") or "").split(",")[0],
        }

    def validate_segments(
        self,
        *,
        ffprobe_path: str = "",
        timeout: float = 10.0,
    ) -> dict[str, dict[str, object]]:
        """Run the full media validation required before post-processing.

        When no ffprobe executable is supplied the method returns an empty
        mapping, preserving the lightweight live-recording path.  Consumers
        that require a complete asset must pass their configured ffprobe path.
        """
        if not ffprobe_path:
            return {}
        results: dict[str, dict[str, object]] = {}
        changed = False
        for path in self.segment_paths():
            valid, details = self._probe_segment(
                path,
                ffprobe_path=ffprobe_path,
                timeout=timeout,
            )
            results[path] = details
            for entry in self.complete_segments:
                entry_path = str((Path(self.manifest_path).parent / entry.path).resolve())
                if entry_path != path:
                    continue
                entry.validation = {**entry.validation, **details, "full_probe": True}
                if details.get("duration_ms"):
                    entry.duration_ms = int(details["duration_ms"])
                if not valid:
                    entry.state = "CORRUPT"
                changed = True
                break
        if changed and Path(self.manifest_path).is_file():
            ManifestStore(self.manifest_path).save(self.manifest)
        return results

    def create_concat_descriptor(self) -> str:
        """Create an atomic FFmpeg concat-demuxer descriptor from the manifest.

        The descriptor is intentionally caller-owned and can be removed after
        the consumer process exits.  It never scans the recording directory;
        only manifest-declared, validated segments are included.
        """
        if any(item.state == "CORRUPT" for item in self.manifest.segments):
            raise RuntimeError("recording asset contains unreadable/corrupt segments")
        paths = self.segment_paths()
        if not paths:
            raise FileNotFoundError("manifest contains no consumable segments")
        root = Path(self.manifest_path).parent.resolve()
        fd, temp_path = tempfile.mkstemp(
            prefix=".recording-concat-",
            suffix=".txt",
            dir=str(root),
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                for path in paths:
                    escaped = path.replace("'", "'\\''")
                    handle.write(f"file '{escaped}'\n")
                handle.flush()
                os.fsync(handle.fileno())
            return temp_path
        except BaseException:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    def materialize_persistent(
        self,
        *,
        ffmpeg_path: str = "",
        ffprobe_path: str = "",
        timeout: float = 300.0,
    ) -> str:
        """Return a stable derived media file for tools that need one path.

        The cache is scoped by recording session and lives beside the manifest;
        it is never treated as a source of truth and can be rebuilt safely.
        """
        if any(item.state == "CORRUPT" for item in self.manifest.segments):
            raise RuntimeError("recording asset contains unreadable/corrupt segments")
        paths = self.segment_paths()
        if not paths:
            raise FileNotFoundError("manifest contains no consumable segments")
        if ffprobe_path:
            validation = self.validate_segments(
                ffprobe_path=ffprobe_path,
                timeout=min(timeout, 30.0),
            )
            if any(not item.get("readable") for item in validation.values()):
                raise RuntimeError("recording asset contains unreadable segments")
        if len(paths) == 1:
            return paths[0]
        root = Path(self.manifest_path).parent.resolve()
        cache = root / f".{self.manifest.recording_session_id}.joined.mkv"
        try:
            manifest_mtime = Path(self.manifest_path).stat().st_mtime
            if cache.is_file() and cache.stat().st_mtime >= manifest_mtime and cache.stat().st_size > 0:
                return str(cache)
        except OSError:
            pass
        descriptor = self.create_concat_descriptor()
        fd, temp_path = tempfile.mkstemp(
            prefix=f".{self.manifest.recording_session_id}.",
            suffix=".joined.partial.mkv",
            dir=str(root),
        )
        os.close(fd)
        try:
            executable = ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
            completed = subprocess.run(
                [
                    executable, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "concat", "-safe", "0", "-i", descriptor,
                    "-map", "0", "-c", "copy", temp_path,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            if completed.returncode != 0 or not os.path.isfile(temp_path) or os.path.getsize(temp_path) <= 0:
                raise RuntimeError(
                    f"recording segments concat failed: {redact_text((completed.stderr or '').strip()[-500:])}"
                )
            os.replace(temp_path, cache)
            return str(cache)
        finally:
            self.remove_concat_descriptor(descriptor)
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    @contextmanager
    def materialized_input(
        self,
        *,
        ffmpeg_path: str = "",
        ffprobe_path: str = "",
        timeout: float = 300.0,
        cleanup: bool = True,
    ) -> Iterator[str]:
        """Expose the asset as one media file for legacy analyzers/exporters.

        A single segment is returned directly.  Multiple segments are joined
        through FFmpeg's concat demuxer into a temporary Matroska file and
        removed when the consumer exits.  The manifest remains the sole source
        of input paths; directory scans are deliberately avoided.
        """
        if any(item.state == "CORRUPT" for item in self.manifest.segments):
            raise RuntimeError("recording asset contains unreadable/corrupt segments")
        paths = self.segment_paths()
        if not paths:
            raise FileNotFoundError("manifest contains no consumable segments")
        if ffprobe_path:
            validation = self.validate_segments(
                ffprobe_path=ffprobe_path,
                timeout=min(timeout, 30.0),
            )
            invalid = [
                path for path in paths
                if path in validation and not validation[path].get("readable")
            ]
            if invalid:
                raise RuntimeError(
                    f"recording asset contains unreadable segments: {len(invalid)}"
                )
        if len(paths) == 1:
            yield paths[0]
            return

        if not cleanup:
            yield self.materialize_persistent(
                ffmpeg_path=ffmpeg_path,
                ffprobe_path=ffprobe_path,
                timeout=timeout,
            )
            return

        descriptor = self.create_concat_descriptor()
        root = Path(self.manifest_path).parent.resolve()
        fd, output_path = tempfile.mkstemp(
            prefix=".recording-joined-", suffix=".mkv", dir=str(root)
        )
        os.close(fd)
        try:
            executable = ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
            command = [
                executable, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", descriptor,
                "-map", "0", "-c", "copy", output_path,
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            if completed.returncode != 0 or not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
                detail = redact_text((completed.stderr or "").strip()[-500:])
                raise RuntimeError(f"recording segments concat failed: {detail}")
            yield output_path
        finally:
            self.remove_concat_descriptor(descriptor)
            if cleanup:
                try:
                    os.unlink(output_path)
                except OSError:
                    pass

    @staticmethod
    def remove_concat_descriptor(path: str) -> None:
        """Best-effort cleanup for a descriptor created above."""
        try:
            os.unlink(path)
        except FileNotFoundError:
            return
        except OSError:
            return

    def to_dict(self) -> dict[str, object]:
        return {
            "manifest_path": self.manifest_path,
            "recording_session_id": self.manifest.recording_session_id,
            "room_session_id": self.manifest.room_session_id,
            "platform_id": self.manifest.platform_id,
            "recording_state": self.manifest.recording_state,
            "segment_count": len(self.complete_segments),
            "incomplete_count": len(self.partial_segments),
            "aggregate_duration_ms": self.manifest.aggregate_duration_ms,
            "content_offset": self.manifest.content_offset,
        }


__all__ = ["RecordingAsset"]
