"""Crash-safe recording segment manifest and recovery helpers."""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA_VERSION = 1


@dataclass(slots=True)
class SegmentEntry:
    sequence: int
    path: str
    state: str = "WRITING"
    generation: int = 0
    started_at: float = 0.0
    ended_at: float | None = None
    media_start_ms: int | None = None
    media_end_ms: int | None = None
    duration_ms: int = 0
    size_bytes: int = 0
    codecs: dict[str, str] = field(default_factory=dict)
    discontinuity_before: bool = False
    validation: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RecordingManifest:
    recording_session_id: str
    room_session_id: str
    platform_id: str
    canonical_room_id: str = ""
    schema_version: int = MANIFEST_SCHEMA_VERSION
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    recording_state: str = "RECORDING"
    timeline_origin: str = "wall_clock"
    aggregate_duration_ms: int = 0
    content_offset: float = 0.0
    segments: list[SegmentEntry] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    recovery_history: list[dict[str, Any]] = field(default_factory=list)
    unclean_shutdown: bool = False

    @classmethod
    def create(
        cls,
        room_session_id: str,
        platform_id: str,
        canonical_room_id: str = "",
    ) -> RecordingManifest:
        return cls(
            recording_session_id=uuid.uuid4().hex,
            room_session_id=room_session_id,
            platform_id=platform_id,
            canonical_room_id=canonical_room_id,
        )

    def add_segment(self, path: str, *, generation: int = 0) -> SegmentEntry:
        entry = SegmentEntry(
            sequence=len(self.segments) + 1,
            path=path,
            generation=generation,
            started_at=time.time(),
        )
        self.segments.append(entry)
        return entry

    def complete_segment(
        self,
        entry: SegmentEntry,
        *,
        duration_ms: int,
        size_bytes: int,
        validation: dict[str, Any] | None = None,
    ) -> None:
        entry.state = "COMPLETE"
        entry.ended_at = time.time()
        entry.duration_ms = max(0, int(duration_ms))
        entry.size_bytes = max(0, int(size_bytes))
        entry.validation = dict(validation or {})
        self.aggregate_duration_ms = sum(
            item.duration_ms
            for item in self.segments
            if item.state in {"COMPLETE", "RECOVERED"}
        )

    def close(self, *, state: str = "COMPLETE", unclean: bool = False) -> None:
        self.ended_at = time.time()
        self.recording_state = state
        self.unclean_shutdown = bool(unclean)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["segments"] = [asdict(item) for item in self.segments]
        return payload


class ManifestStore:
    """Atomic manifest persistence scoped to one recording directory."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).resolve()
        self.root = self.path.parent

    def save(self, manifest: RecordingManifest) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix=f".{self.path.stem}.",
            suffix=".tmp",
            dir=str(self.root),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    manifest.to_dict(),
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        finally:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass

    def load(self) -> RecordingManifest:
        with self.path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        segments = [
            SegmentEntry(**item)
            for item in payload.get("segments", [])
            if isinstance(item, dict)
        ]
        payload["segments"] = segments
        payload.setdefault("schema_version", MANIFEST_SCHEMA_VERSION)
        return RecordingManifest(**payload)

    def recover(self) -> RecordingManifest:
        """Mark readable existing segment files as recovered.

        Recovery is idempotent and never deletes corrupt evidence.
        """
        manifest = self.load()
        for entry in manifest.segments:
            target = (self.root / entry.path).resolve()
            if (
                not target.is_file()
                and entry.path.endswith(".partial.mkv")
            ):
                recovered_target = target.with_name(
                    target.name.removesuffix(".partial.mkv") + ".mkv"
                )
                if recovered_target.is_file():
                    target = recovered_target
                    entry.path = target.relative_to(self.root).as_posix()
            try:
                target.relative_to(self.root)
            except ValueError:
                entry.state = "MISSING"
                continue
            if not target.is_file() or target.stat().st_size <= 0:
                entry.state = "MISSING"
                continue
            if entry.state == "WRITING":
                entry.state = "RECOVERED"
                entry.size_bytes = target.stat().st_size
                entry.ended_at = entry.ended_at or time.time()
        known_paths = {item.path for item in manifest.segments}
        known_paths.update(
            path.removesuffix(".partial.mkv") + ".mkv"
            for path in tuple(known_paths)
            if path.endswith(".partial.mkv")
        )
        segments_dir = self.root / "segments"
        if segments_dir.is_dir():
            targets = sorted(set(
                segments_dir.glob("*.mkv")
            ).union(segments_dir.glob("*.partial.mkv")))
            for target in targets:
                if target.name.endswith(".partial.mkv"):
                    final = target.with_name(target.name.removesuffix(".partial.mkv") + ".mkv")
                    try:
                        if not final.exists():
                            target.replace(final)
                        target = final
                    except OSError:
                        pass
                relative = target.relative_to(self.root).as_posix()
                if relative in known_paths or target.stat().st_size <= 0:
                    continue
                entry = manifest.add_segment(relative)
                manifest.complete_segment(
                    entry,
                    duration_ms=0,
                    size_bytes=target.stat().st_size,
                    validation={"recovered_from_scan": True},
                )
        manifest.recovery_history.append(
            {"at": time.time(), "action": "scan_segments"}
        )
        manifest.aggregate_duration_ms = sum(
            item.duration_ms
            for item in manifest.segments
            if item.state in {"COMPLETE", "RECOVERED"}
        )
        self.save(manifest)
        return manifest


__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "ManifestStore",
    "RecordingManifest",
    "SegmentEntry",
]
