from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

_backend_dir = os.path.join(os.path.dirname(__file__), '..', 'python-backend')
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from handlers import room_handler
from lsc.core.services.shared_ingest import (
    SharedIngestStartResult,
    SharedPreviewHandle,
    SharedRoomIngest,
)

ROOT = Path(__file__).resolve().parents[1]


class _DummyTask:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _DummyStreamer:
    def __init__(self) -> None:
        self.stopped = False
        self.replay_count = 0

    @property
    def is_running(self) -> bool:
        return not self.stopped

    def replay_init(self) -> bool:
        self.replay_count += 1
        return True

    def stop(self) -> None:
        self.stopped = True


class _DummyExecutor:
    def __init__(self) -> None:
        self.shutdown_calls: list[dict] = []

    def shutdown(self, **kwargs) -> None:
        self.shutdown_calls.append(kwargs)


class _FakeSignal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)


class _FakeServer:
    def __init__(self) -> None:
        self.handlers = {}
        self.broadcasts = []
        self.connect_handler = None

    def on(self, name):
        def decorator(handler):
            self.handlers[name] = handler
            return handler

        return decorator

    def on_connect(self, handler):
        self.connect_handler = handler
        return handler

    async def broadcast(self, name, data):
        self.broadcasts.append((name, data))


class _FakeBridge:
    def __init__(self, manager) -> None:
        self.manager = manager
        self.broadcasts = []

    def queue_broadcast(self, message) -> None:
        self.broadcasts.append(message)

    def call(self, func, *args, **kwargs):
        return func(*args, **kwargs)


class _FakeManager:
    def __init__(self, rooms) -> None:
        self._rooms = {room.room_id: room for room in rooms}
        self.room_connect_finished = _FakeSignal()
        self.batch_record_progress = _FakeSignal()
        self.batch_record_finished = _FakeSignal()
        self.medium_tick = _FakeSignal()
        self.low_tick = _FakeSignal()
        self.refresh_calls = []

    def get_room(self, room_id: str):
        return self._rooms.get(room_id)

    def list_rooms(self):
        return list(self._rooms.values())

    def refresh_stream_url(self, room_id: str, force: bool = False) -> bool:
        self.refresh_calls.append((room_id, force))
        return True

    def start_recording(
        self,
        room_id: str,
        output_dir: str,
        encoder: str,
        crf: int,
        **_kwargs,
    ) -> bool:
        room = self.get_room(room_id)
        if room is None:
            return False
        room.is_recording = True
        room.record_output_path = os.path.join(output_dir, f"{room_id}.mp4")
        return True

    def connect_room(self, room_id: str, *, async_mode: bool = False, quality_preset: str = "原画") -> bool:
        self.connect_calls = getattr(self, "connect_calls", [])
        self.connect_calls.append((room_id, async_mode, quality_preset))
        if getattr(self, "connect_result", True) is False:
            return False
        room = self.get_room(room_id)
        if room is None:
            return False
        if async_mode:
            room.is_connecting = True
        return True


def _make_connect_room(room_id: str = "room-1") -> SimpleNamespace:
    return SimpleNamespace(
        room_id=room_id,
        streamer_name="Streamer",
        platform="test",
        platform_name="Test",
        room_url="https://example/room",
        stream_title="Title",
        is_connecting=False,
        is_connected=False,
        is_recording=False,
        is_reconnecting=False,
        is_muted=False,
        record_output_path="",
        record_started_at=None,
        record_size_mb=0.0,
        last_error="",
        preview_enabled=False,
        preview_paused=False,
        preview_muted=False,
        mark_in=None,
        mark_out=None,
        mark_in_wallclock=None,
        mark_out_wallclock=None,
        recording_start_mono=0.0,
        recording_media_start_mono=0.0,
        preview_latency=0.0,
        content_offset=0.0,
        align_group_id="",
        category="",
        stream_info=None,
        stream_url_cached="",
        controller=None,
    )


def test_handle_connect_room_async_returns_accepted_contract(monkeypatch) -> None:
    """async connect_room 受理成功时必须显式返回 accepted/async，不得伪装成连接完成。"""
    room = _make_connect_room()
    manager = _FakeManager([room])
    server = _FakeServer()
    bridge = _FakeBridge(manager)
    monkeypatch.setattr(room_handler, "load_settings", lambda: {"quality": "原画"})

    async def scenario():
        room_handler.register_room_handlers(server, bridge)
        return await server.handlers["connect_room"]({"room_id": "room-1"})

    result = asyncio.run(scenario())

    assert result["success"] is True
    assert result["accepted"] is True
    assert result["async"] is True
    assert result["room_id"] == "room-1"
    assert room.is_connecting is True


def test_handle_connect_room_rejected_when_already_connecting(monkeypatch) -> None:
    """已在连接中 / 启动失败时 accepted=False，并带 error 供前端回滚。"""
    room = _make_connect_room()
    manager = _FakeManager([room])
    manager.connect_result = False
    server = _FakeServer()
    bridge = _FakeBridge(manager)
    monkeypatch.setattr(room_handler, "load_settings", lambda: {"quality": "原画"})

    async def scenario():
        room_handler.register_room_handlers(server, bridge)
        return await server.handlers["connect_room"]({"room_id": "room-1"})

    result = asyncio.run(scenario())

    assert result["success"] is False
    assert result["accepted"] is False
    assert result["room_id"] == "room-1"
    assert result.get("error")


def test_preview_refresh_failure_keeps_connected_when_cache_exists() -> None:
    """预览刷新失败时，_mark_disconnected_if_no_stream 在有流缓存时必须保留连接。"""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "def _mark_disconnected_if_no_stream():" in source
    body = source.split("def _mark_disconnected_if_no_stream():", 1)[1].split(
        "await asyncio.get_running_loop().run_in_executor(", 1
    )[0]
    assert "has_url" in body
    assert "stream_url_cached" in body
    assert "if has_url:" in body
    assert "return" in body.split("if has_url:", 1)[1]
    # 仅在无缓存时才清连接
    assert "room.is_connected = False" in body
    clear_idx = body.index("room.is_connected = False")
    has_url_idx = body.index("if has_url:")
    assert has_url_idx < clear_idx


def test_shutdown_room_handlers_cancels_tasks_streamers_and_executors(monkeypatch) -> None:
    task = _DummyTask()
    streamer = _DummyStreamer()
    rec_executor = _DummyExecutor()
    bridge_executor = _DummyExecutor()
    ai_executor = _DummyExecutor()

    monkeypatch.setattr(room_handler, "_continuous_tasks", {
        "room-1": {"task": task, "cancelled": False},
    })
    monkeypatch.setattr(room_handler, "_mse_streamers", {"room-1": streamer})
    monkeypatch.setattr(room_handler, "_mse_reconnect_state", {"room-1": {"attempts": 1}})
    monkeypatch.setattr(room_handler, "_mse_starting", {"room-1"})
    monkeypatch.setattr(room_handler, "_recording_starting", {"room-1"})
    monkeypatch.setattr(room_handler, "_recording_executor", rec_executor)
    monkeypatch.setattr(room_handler, "_bridge_executor", bridge_executor)
    monkeypatch.setattr(room_handler, "_ai_executor", ai_executor)

    result = room_handler.shutdown_room_handlers(timeout_sec=0.1)
    second = room_handler.shutdown_room_handlers(timeout_sec=0.1)

    assert result["continuous_tasks_cancelled"] == 1
    assert result["mse_streamers_stopped"] == 1
    assert task.cancelled is True
    assert streamer.stopped is True
    assert room_handler._continuous_tasks == {}
    assert room_handler._mse_streamers == {}
    assert room_handler._mse_reconnect_state == {}
    assert room_handler._mse_starting == set()
    assert room_handler._recording_starting == set()
    assert rec_executor.shutdown_calls
    assert bridge_executor.shutdown_calls
    assert ai_executor.shutdown_calls
    assert second["continuous_tasks_cancelled"] == 0
    assert second["mse_streamers_stopped"] == 0


def test_shutdown_room_handlers_stops_shared_ingests(monkeypatch) -> None:
    from lsc.core.services.ingest_registry import SharedIngestRegistry

    registry = SharedIngestRegistry()
    ingest = registry.get_or_create("room-1", url="http://example/live.flv", headers={})
    ingest.recording_active = True

    monkeypatch.setattr(room_handler, "_shared_ingests", registry)
    monkeypatch.setattr(room_handler, "_mse_streamers", {})
    monkeypatch.setattr(room_handler, "_continuous_tasks", {})
    monkeypatch.setattr(room_handler, "_mse_reconnect_state", {})
    monkeypatch.setattr(room_handler, "_mse_starting", set())
    monkeypatch.setattr(room_handler, "_recording_starting", set())
    monkeypatch.setattr(room_handler, "_recording_executor", _DummyExecutor())
    monkeypatch.setattr(room_handler, "_bridge_executor", _DummyExecutor())
    monkeypatch.setattr(room_handler, "_ai_executor", _DummyExecutor())

    result = room_handler.shutdown_room_handlers(timeout_sec=0.1)

    assert result["shared_ingests_stopped"] == 1
    assert registry.get("room-1") is None
    assert ingest.is_stopped is True


def test_mse_preview_attaches_existing_shared_ingest_when_enabled(monkeypatch) -> None:
    room = SimpleNamespace(
        room_id="room-1",
        streamer_name="Streamer",
        platform="test",
        platform_name="Test",
        room_url="https://example/room",
        stream_title="Title",
        is_connecting=False,
        is_connected=True,
        is_recording=True,
        is_reconnecting=False,
        is_muted=False,
        record_output_path="",
        record_started_at=None,
        record_size_mb=0.0,
        last_error="",
        preview_enabled=False,
        preview_paused=False,
        preview_muted=False,
        mark_in=None,
        mark_out=None,
        mark_in_wallclock=None,
        mark_out_wallclock=None,
        recording_start_mono=0.0,
        recording_media_start_mono=0.0,
        preview_latency=0.0,
        content_offset=0.0,
        align_group_id="",
        category="",
        stream_info=SimpleNamespace(
            stream_url="http://example/live.flv",
            headers={},
            quality_urls={},
        ),
    )
    manager = _FakeManager([room])
    server = _FakeServer()
    bridge = _FakeBridge(manager)
    ingest = SharedRoomIngest("room-1", "http://example/live.flv")
    ingest.recording_active = True
    ingest.publish_preview_segment(b"init-data", kind="init")

    class _FakeSharedRegistry:
        def get(self, room_id):
            return ingest if room_id == "room-1" else None

    monkeypatch.setattr(room_handler, "_mse_streamers", {})
    monkeypatch.setattr(room_handler, "_shared_ingests", _FakeSharedRegistry())
    monkeypatch.setattr(
        room_handler,
        "load_config",
        lambda: SimpleNamespace(shared_ingest_enabled=True),
    )

    async def scenario():
        room_handler.register_room_handlers(server, bridge)
        result = await server.handlers["enable_preview"]({
            "room_id": "room-1",
            "enabled": True,
            "mode": "mse",
        })
        await asyncio.sleep(0)
        ingest.publish_preview_segment(b"media-data", kind="media")
        for _ in range(20):
            if any(event == "mse_segment" for event, _payload in server.broadcasts):
                break
            await asyncio.sleep(0.02)
        ingest.handle_upstream_error("source failed")
        for _ in range(20):
            if any(event == "mse_error" for event, _payload in server.broadcasts):
                break
            await asyncio.sleep(0.02)
        return result

    result = asyncio.run(scenario())

    handle = room_handler._preview_stream_registry().get("room-1")
    assert result["success"] is True
    assert result["note"] == "shared ingest preview attached"
    assert isinstance(handle, SharedPreviewHandle)
    assert room.preview_enabled is True
    assert manager.refresh_calls == []
    assert server.broadcasts[0][0] == "mse_init"
    assert any(event == "mse_segment" for event, _payload in server.broadcasts)
    assert any(
        event == "mse_error" and payload["room_id"] == "room-1" and payload["error"] == "source failed"
        for event, payload in server.broadcasts
    )


def test_ingest_diagnostics_reports_shared_registry_counts(monkeypatch) -> None:
    from lsc.core.services.ingest_registry import SharedIngestRegistry

    registry = SharedIngestRegistry()
    ingest = registry.get_or_create("room-1", url="http://example/live.flv", headers={})
    ingest.recording_active = True
    ingest.attach_preview_subscriber()

    monkeypatch.setattr(room_handler, "_shared_ingests", registry)
    monkeypatch.setattr(room_handler, "_mse_streamers", {})

    stats = room_handler._ingest_diagnostics()

    assert stats["shared_ingests"] == 1
    assert stats["recording_sinks"] == 1
    assert stats["preview_subscribers"] == 1
    assert stats["legacy_mse_streamers"] == 0


def test_stopping_idle_shared_preview_cleans_shared_ingest(monkeypatch) -> None:
    from lsc.core.services.ingest_registry import SharedIngestRegistry

    room = SimpleNamespace(
        room_id="room-1",
        streamer_name="Streamer",
        platform="test",
        platform_name="Test",
        room_url="https://example/room",
        stream_title="Title",
        is_connecting=False,
        is_connected=True,
        is_recording=False,
        is_reconnecting=False,
        is_muted=False,
        record_output_path="",
        record_started_at=None,
        record_size_mb=0.0,
        last_error="",
        preview_enabled=True,
        preview_paused=False,
        preview_muted=False,
        mark_in=None,
        mark_out=None,
        mark_in_wallclock=None,
        mark_out_wallclock=None,
        recording_start_mono=0.0,
        recording_media_start_mono=0.0,
        preview_latency=0.0,
        content_offset=0.0,
        align_group_id="",
        category="",
        stream_info=SimpleNamespace(
            stream_url="http://example/live.flv",
            headers={},
            quality_urls={},
        ),
    )
    manager = _FakeManager([room])
    server = _FakeServer()
    bridge = _FakeBridge(manager)
    registry = SharedIngestRegistry()
    ingest = registry.get_or_create("room-1", url="http://example/live.flv", headers={})
    handle = SharedPreviewHandle(
        ingest,
        on_init_segment=lambda _data: None,
        on_media_segment=lambda _data: None,
    )

    monkeypatch.setattr(room_handler, "_mse_streamers", {"room-1": handle})
    monkeypatch.setattr(room_handler, "_shared_ingests", registry)

    async def scenario():
        room_handler.register_room_handlers(server, bridge)
        return await server.handlers["enable_preview"]({
            "room_id": "room-1",
            "enabled": False,
            "mode": "mse",
        })

    result = asyncio.run(scenario())

    assert result["success"] is True
    assert registry.get("room-1") is None
    assert ingest.is_stopped is True
    assert ingest.preview_subscribers == 0


def test_start_recording_reattaches_existing_preview_to_shared(monkeypatch, tmp_path) -> None:
    from lsc.core.services.ingest_registry import SharedIngestRegistry

    room = SimpleNamespace(
        room_id="room-1",
        streamer_name="Streamer",
        platform="test",
        platform_name="Test",
        room_url="https://example/room",
        stream_title="Title",
        is_connecting=False,
        is_connected=True,
        is_recording=False,
        is_reconnecting=False,
        is_muted=False,
        record_output_path="",
        record_started_at=None,
        record_size_mb=0.0,
        last_error="",
        preview_enabled=True,
        preview_paused=False,
        preview_muted=False,
        mark_in=None,
        mark_out=None,
        mark_in_wallclock=None,
        mark_out_wallclock=None,
        recording_start_mono=0.0,
        recording_media_start_mono=0.0,
        preview_latency=0.0,
        content_offset=0.0,
        align_group_id="",
        category="",
        stream_info=SimpleNamespace(
            stream_url="http://example/live.flv",
            headers={},
            quality_urls={},
        ),
    )
    manager = _FakeManager([room])
    server = _FakeServer()
    bridge = _FakeBridge(manager)
    legacy_preview = _DummyStreamer()
    registry = SharedIngestRegistry()
    ingest = registry.get_or_create("room-1", url="http://example/live.flv", headers={})
    ingest.recording_active = True
    ingest.publish_preview_segment(b"init-data", kind="init")

    monkeypatch.setattr(room_handler, "_mse_streamers", {"room-1": legacy_preview})
    monkeypatch.setattr(room_handler, "_shared_ingests", registry)
    monkeypatch.setattr(
        room_handler,
        "load_config",
        lambda: SimpleNamespace(shared_ingest_enabled=True),
    )
    monkeypatch.setattr(
        room_handler,
        "load_settings",
        lambda: {
            "output_dir": str(tmp_path),
            "encoder": "Copy",
            "crf": 23,
            "param_mode": "CRF 质量",
            "bitrate": 8000,
            "bitrate_unit": "kbps",
            "resolution": "原画",
            "framerate": "原画",
            "audio_bitrate": "128k",
        },
    )
    monkeypatch.setattr(room_handler, "recording_history", [])

    async def scenario():
        room_handler.register_room_handlers(server, bridge)
        return await server.handlers["start_recording"]({"room_id": "room-1"})

    result = asyncio.run(scenario())

    handle = room_handler._preview_stream_registry().get("room-1")
    assert result["success"] is True
    assert legacy_preview.stopped is True
    assert isinstance(handle, SharedPreviewHandle)
    assert room.preview_enabled is True
    assert any(event == "mse_init" for event, _payload in server.broadcasts)


def test_mse_preview_starts_shared_preview_only_when_enabled(monkeypatch) -> None:
    from lsc.core.services.ingest_registry import SharedIngestRegistry
    import lsc.core.services.mse_streamer as mse_streamer

    room = SimpleNamespace(
        room_id="room-1",
        streamer_name="Streamer",
        platform="test",
        platform_name="Test",
        room_url="https://example/room",
        stream_title="Title",
        is_connecting=False,
        is_connected=True,
        is_recording=False,
        is_reconnecting=False,
        is_muted=False,
        record_output_path="",
        record_started_at=None,
        record_size_mb=0.0,
        last_error="",
        preview_enabled=False,
        preview_paused=False,
        preview_muted=False,
        mark_in=None,
        mark_out=None,
        mark_in_wallclock=None,
        mark_out_wallclock=None,
        recording_start_mono=0.0,
        recording_media_start_mono=0.0,
        preview_latency=0.0,
        content_offset=0.0,
        align_group_id="",
        category="",
        stream_info=SimpleNamespace(
            stream_url="http://example/live.flv",
            headers={},
            quality_urls={},
        ),
    )
    manager = _FakeManager([room])
    server = _FakeServer()
    bridge = _FakeBridge(manager)
    registry = SharedIngestRegistry()
    start_calls: list[str] = []

    def fake_start_preview(self, width=0, height=0, use_nvenc=False, video_bitrate="", crf_value=0, fps=0, preview_pipe="pipe:1"):
        start_calls.append(self.room_id)
        self.publish_preview_segment(b"init-data", kind="init")
        return SharedIngestStartResult(ok=True)

    class _UnexpectedLegacyStreamer:
        is_running = True

        def __init__(self, *args, **kwargs) -> None:
            pass

        def start(self, *args, **kwargs) -> bool:
            return True

        def replay_init(self) -> bool:
            return True

        def stop(self) -> None:
            pass

    monkeypatch.setattr(room_handler, "_mse_streamers", {})
    monkeypatch.setattr(room_handler, "_shared_ingests", registry)
    monkeypatch.setattr(
        room_handler,
        "load_config",
        lambda: SimpleNamespace(shared_ingest_enabled=True),
    )
    monkeypatch.setattr(room_handler.SharedRoomIngest if hasattr(room_handler, "SharedRoomIngest") else SharedRoomIngest, "start_preview", fake_start_preview)
    monkeypatch.setattr(mse_streamer, "_check_nvenc", lambda: False)
    monkeypatch.setattr(mse_streamer, "MseStreamer", _UnexpectedLegacyStreamer)

    async def scenario():
        room_handler.register_room_handlers(server, bridge)
        return await server.handlers["enable_preview"]({
            "room_id": "room-1",
            "enabled": True,
            "mode": "mse",
        })

    result = asyncio.run(scenario())

    handle = room_handler._preview_stream_registry().get("room-1")
    assert result["success"] is True
    assert result["note"] == "shared ingest preview-only started"
    assert start_calls == ["room-1"]
    assert isinstance(handle, SharedPreviewHandle)
    assert registry.get("room-1") is not None
    assert room.preview_enabled is True
    assert manager.refresh_calls == [("room-1", False)]
    assert any(event == "mse_init" for event, _payload in server.broadcasts)


# ── #18 regression: recording_history cap ──────────────────────────

def test_recording_history_capped_on_load():
    """_load_recording_history must slice to _MAX_RECORDING_HISTORY (#18)."""
    from handlers import room_handler

    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "return data[-_MAX_RECORDING_HISTORY:]" in source, \
        "load must trim to max"


def test_recording_history_capped_on_append():
    """Append site must trim when exceeding _MAX_RECORDING_HISTORY (#18)."""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    snippet = source.split("recording_history.append", 1)[1].split("\n", 15)
    joined = "\n".join(snippet)
    assert "_MAX_RECORDING_HISTORY" in joined
    assert "del recording_history[" in joined


# ── #17 regression: recording_history lock ─────────────────────────

def test_recording_history_has_lock():
    """recording_history must be protected by _recording_history_lock (#17)."""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "_recording_history_lock" in source


# ── #103 regression: throttle flush cancellation ───────────────────

def test_broadcast_rooms_cancels_pending_flush_on_force():
    """_broadcast_rooms must cancel pending _flush task before force sending (#103)."""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "_rooms_throttle_task.cancel()" in source, \
        "force and immediate paths must cancel pending flush task to prevent double broadcast"


# ── #16 regression: semaphore guarded replacement ──────────────────

def test_export_semaphore_not_replaced_mid_flight():
    """_ensure_export_queue must not replace semaphore when queue is non-empty (#16)."""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    # The guard checks _export_queue.empty() before replacing
    assert "_export_queue.empty()" in source, \
        "semaphore must only be replaced when the export queue is empty"
    assert "延迟" in source, \
        "must log a delay warning when queue is non-empty"
