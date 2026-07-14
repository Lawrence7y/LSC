from __future__ import annotations

import threading
from typing import Any, Protocol

from lsc.config import load_config
from lsc.core.services.shared_ingest import SharedPreviewHandle, SharedRoomIngest


class PreviewStreamHandle(Protocol):
    @property
    def is_running(self) -> bool: ...

    def replay_init(self) -> bool: ...

    def stop(self) -> None: ...


class PreviewStreamRegistry:
    def __init__(
        self,
        backing: dict[str, PreviewStreamHandle] | None = None,
        lock: Any | None = None,
    ):
        self._streams = backing if backing is not None else {}
        self._lock = lock or threading.RLock()

    def get(self, room_id: str) -> PreviewStreamHandle | None:
        with self._lock:
            return self._streams.get(room_id)

    def set_legacy(self, room_id: str, handle: PreviewStreamHandle) -> None:
        with self._lock:
            self._streams[room_id] = handle

    def attach_shared(
        self,
        room_id: str,
        ingest: SharedRoomIngest,
        on_init_segment,
        on_media_segment,
        on_error=None,
        pump_interval_sec: float = 0.05,
    ) -> SharedPreviewHandle:
        handle = SharedPreviewHandle(
            ingest,
            on_init_segment=on_init_segment,
            on_media_segment=on_media_segment,
            on_error=on_error,
            pump_interval_sec=pump_interval_sec,
            auto_start=True,
        )
        with self._lock:
            self._streams[room_id] = handle
        return handle

    def pop(self, room_id: str) -> PreviewStreamHandle | None:
        with self._lock:
            return self._streams.pop(room_id, None)

    def clear_items(self) -> list[tuple[str, PreviewStreamHandle]]:
        with self._lock:
            items = list(self._streams.items())
            self._streams.clear()
            return items

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for stream in self._streams.values() if stream.is_running)

    def stop_room(self, room_id: str, reason: str = "") -> PreviewStreamHandle | None:
        handle = self.pop(room_id)
        if handle is not None:
            handle.stop()
        return handle


class SharedIngestRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._ingests: dict[str, SharedRoomIngest] = {}

    def get(self, room_id: str) -> SharedRoomIngest | None:
        with self._lock:
            return self._ingests.get(room_id)

    def get_or_create(
        self,
        room_id: str,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> SharedRoomIngest:
        with self._lock:
            ingest = self._ingests.get(room_id)
            if ingest is None:
                try:
                    cfg = load_config()
                    queue_bytes = cfg.shared_ingest_preview_queue_bytes
                    drop_policy = cfg.shared_ingest_preview_drop_policy
                except Exception:
                    queue_bytes = 2 * 1024 * 1024
                    drop_policy = "drop_oldest"
                ingest = SharedRoomIngest(
                    room_id=room_id,
                    url=url,
                    headers=headers,
                    preview_queue_bytes=queue_bytes,
                    preview_drop_policy=drop_policy,
                )
                self._ingests[room_id] = ingest
            elif not ingest.recording_active:
                ingest.url = url
                ingest.headers = dict(headers or {})
            return ingest

    def stop_room(self, room_id: str, reason: str = "") -> None:
        with self._lock:
            ingest = self._ingests.pop(room_id, None)
        if ingest is not None:
            ingest.stop(reason=reason)

    def stop_all(self, reason: str = "") -> int:
        with self._lock:
            ingests = list(self._ingests.values())
            self._ingests.clear()
        for ingest in ingests:
            ingest.stop(reason=reason)
        return len(ingests)

    def snapshot_counts(self) -> dict[str, int]:
        with self._lock:
            ingests = list(self._ingests.values())
        return {
            "shared_ingests": len(ingests),
            "recording_sinks": sum(1 for ingest in ingests if ingest.recording_active),
            "preview_subscribers": sum(ingest.preview_subscribers for ingest in ingests),
            "preview_dropped_bytes": sum(ingest.preview_dropped_bytes for ingest in ingests),
            "preview_dropped_batches": sum(ingest.preview_dropped_batches for ingest in ingests),
        }


_PROCESS_SHARED_INGEST_REGISTRY = SharedIngestRegistry()


def get_shared_ingest_registry() -> SharedIngestRegistry:
    return _PROCESS_SHARED_INGEST_REGISTRY


__all__ = [
    "PreviewStreamHandle",
    "PreviewStreamRegistry",
    "SharedIngestRegistry",
    "get_shared_ingest_registry",
]
