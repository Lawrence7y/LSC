"""LSC 录制层。"""
from __future__ import annotations

from .assets import RecordingAsset
from .manifest import (
    MANIFEST_SCHEMA_VERSION,
    ManifestStore,
    RecordingManifest,
    SegmentEntry,
)
from .segmented import (
    SegmentedRecorder,
    SegmentedStartResult,
    SegmentedStopResult,
)
from .timeline import TimelineInterval, TimelineMapper

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "ManifestStore",
    "RecordingManifest",
    "SegmentEntry",
    "SegmentedRecorder",
    "SegmentedStartResult",
    "SegmentedStopResult",
    "RecordingAsset",
    "TimelineInterval",
    "TimelineMapper",
]
