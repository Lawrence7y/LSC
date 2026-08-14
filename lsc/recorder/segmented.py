"""Optional MKV segmented recorder with crash-safe manifest persistence."""
from __future__ import annotations

import glob
import os
import subprocess
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from lsc.config import LscConfig
from lsc.platforms.base import headers_to_ffmpeg_input_args
from lsc.recorder.manifest import ManifestStore, RecordingManifest
from lsc.utils.process_launcher import prepare_launch


@dataclass(frozen=True, slots=True)
class SegmentedStartResult:
    success: bool
    session_dir: str = ""
    manifest_path: str = ""
    error: str = ""


@dataclass(frozen=True, slots=True)
class SegmentedStopResult:
    success: bool
    manifest_path: str = ""
    segment_count: int = 0
    error: str = ""


class SegmentedRecorder:
    """Write one room into short MKV segments and a versioned manifest."""

    def __init__(
        self,
        config: LscConfig,
        *,
        room_id: str,
        platform_id: str,
        canonical_room_id: str = "",
        segment_seconds: int = 60,
        network_context: Mapping[str, object] | None = None,
    ) -> None:
        self.config = config
        self.room_id = room_id
        self.platform_id = platform_id
        self.canonical_room_id = canonical_room_id
        self.segment_seconds = max(5, int(segment_seconds))
        self.network_context = dict(network_context or {})
        self._process: subprocess.Popen | None = None
        self._manifest_store: ManifestStore | None = None
        self._manifest: RecordingManifest | None = None
        self._session_dir = ""
        self._stderr_thread: threading.Thread | None = None

    @property
    def is_recording(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def manifest_path(self) -> str:
        return str(self._manifest_store.path) if self._manifest_store else ""

    def build_command(
        self,
        url: str,
        segments_dir: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> list[str]:
        ffmpeg = self.config.ffmpeg_path or "ffmpeg"
        # FFmpeg writes an explicit partial suffix; completed files are
        # atomically renamed during stop/recovery and never look complete
        # while they may still have an open muxer.
        pattern = str(Path(segments_dir) / "%06d.partial.mkv")
        command = [ffmpeg, "-y", "-loglevel", "warning"]
        if url.startswith(("http://", "https://")):
            command += [
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "3",
                "-timeout", "20000000",
            ]
            proxy = str(
                self.network_context.get("proxy_url")
                or self.network_context.get("http_proxy")
                or self.network_context.get("https_proxy")
                or ""
            ).strip()
            if proxy:
                command.extend(["-http_proxy", proxy])
        command.extend(headers_to_ffmpeg_input_args(headers or {}))
        command += [
            "-i", url,
            "-map", "0",
            "-c", "copy",
            "-f", "segment",
            "-segment_time", str(self.segment_seconds),
            "-reset_timestamps", "1",
            "-segment_format", "matroska",
            pattern,
        ]
        return command

    def start(
        self,
        url: str,
        output_dir: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> SegmentedStartResult:
        if self.is_recording:
            return SegmentedStartResult(False, error="already recording")
        root = Path(output_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        session_dir = root / f"recording_session_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        segments_dir = session_dir / "segments"
        segments_dir.mkdir(parents=True, exist_ok=False)
        manifest = RecordingManifest.create(
            self.room_id,
            self.platform_id,
            self.canonical_room_id,
        )
        # Register the first partial before FFmpeg starts so a process crash
        # leaves an explicit WRITING entry for recovery rather than relying
        # only on a directory scan.
        manifest.add_segment("segments/000001.partial.mkv")
        store = ManifestStore(session_dir / "manifest.json")
        store.save(manifest)
        command = self.build_command(url, str(segments_dir), headers=headers)
        try:
            env, creation_flags, cwd = prepare_launch(self.config.ffmpeg_path or "ffmpeg")
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=cwd,
                creationflags=creation_flags,
            )
        except Exception as exc:
            manifest.close(state="FAILED", unclean=True)
            store.save(manifest)
            return SegmentedStartResult(
                False,
                session_dir=str(session_dir),
                manifest_path=str(store.path),
                error=str(exc),
            )
        self._process = process
        self._manifest_store = store
        self._manifest = manifest
        self._session_dir = str(session_dir)
        stderr = getattr(process, "stderr", None)
        if stderr is not None and hasattr(stderr, "readline"):
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr,
                args=(stderr,),
                name=f"segmented-recorder-stderr-{self.room_id}",
                daemon=True,
            )
            self._stderr_thread.start()
        return SegmentedStartResult(
            True,
            session_dir=str(session_dir),
            manifest_path=str(store.path),
        )

    def stop(self, *, unclean: bool = False) -> SegmentedStopResult:
        process = self._process
        store = self._manifest_store
        manifest = self._manifest
        if process is None or store is None or manifest is None:
            return SegmentedStopResult(False, error="not recording")
        if process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.write("q")
                    process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                    unclean = True
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1)
            self._stderr_thread = None
        segments_dir = Path(self._session_dir) / "segments"
        existing = sorted(set(
            glob.glob(str(segments_dir / "*.mkv"))
            + glob.glob(str(segments_dir / "*.partial.mkv"))
        ))
        known = {item.path for item in manifest.segments}
        for path in existing:
            source = Path(path)
            if source.name.endswith(".partial.mkv"):
                final = source.with_name(source.name.removesuffix(".partial.mkv") + ".mkv")
                try:
                    if not final.exists():
                        source.replace(final)
                    source = final
                except OSError:
                    # Keep the partial evidence; recovery can retry later.
                    pass
            relative = source.relative_to(Path(self._session_dir)).as_posix()
            partial_relative = f"{relative.removesuffix('.mkv')}.partial.mkv"
            existing_entry = next(
                (
                    item
                    for item in manifest.segments
                    if item.path in {relative, partial_relative}
                ),
                None,
            )
            if existing_entry is not None:
                existing_entry.path = relative
                existing_entry.state = "WRITING"
            if relative in known or partial_relative in known:
                entry = existing_entry
                if entry is None:
                    continue
            else:
                entry = manifest.add_segment(relative)
            if entry is not None:
                size = os.path.getsize(source)
                manifest.complete_segment(
                    entry,
                    duration_ms=0,
                    size_bytes=size,
                    validation={"size_positive": size > 0},
                )
                continue
        manifest.close(
            state="PARTIAL" if unclean else "COMPLETE",
            unclean=unclean,
        )
        store.save(manifest)
        self._process = None
        return SegmentedStopResult(
            True,
            manifest_path=str(store.path),
            segment_count=len(manifest.segments),
        )

    @staticmethod
    def _drain_stderr(stderr: object) -> None:
        """Continuously consume FFmpeg diagnostics to avoid pipe deadlocks."""
        readline = getattr(stderr, "readline", None)
        if not callable(readline):
            return
        while True:
            try:
                line = readline()
            except (OSError, ValueError):
                return
            if not line:
                return

    @staticmethod
    def recover(manifest_path: str) -> RecordingManifest:
        return ManifestStore(manifest_path).recover()


__all__ = [
    "SegmentedRecorder",
    "SegmentedStartResult",
    "SegmentedStopResult",
]
