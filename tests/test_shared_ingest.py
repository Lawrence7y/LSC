from __future__ import annotations

import io
import time

import pytest

from lsc.config import ExportProfile
from lsc.core.services.ingest_registry import PreviewStreamRegistry, SharedIngestRegistry
from lsc.core.services.shared_ingest import (
    PreviewSubscriber,
    SharedIngestStartResult,
    SharedPreviewHandle,
    SharedRoomIngest,
)


TS_PACKET_SIZE = 188


class _PartialWriter:
    def __init__(self, max_write: int | None = None):
        self.max_write = max_write
        self.data = bytearray()
        self.closed = False

    def write(self, data) -> int:
        if self.closed:
            raise ValueError("closed")
        raw = bytes(data)
        count = len(raw) if self.max_write is None else min(len(raw), self.max_write)
        self.data.extend(raw[:count])
        return count

    def flush(self) -> None:
        if self.closed:
            raise ValueError("closed")

    def close(self) -> None:
        self.closed = True


class _ChunkReader:
    def __init__(self, chunks: list[bytes] | None = None):
        self.chunks = list(chunks or [])
        self.closed = False

    def read(self, _size: int = -1) -> bytes:
        if self.closed or not self.chunks:
            return b""
        return self.chunks.pop(0)

    def readline(self) -> bytes:
        return self.read()

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(
        self,
        returncode: int | None = None,
        pid: int = 4242,
        stdout_chunks: list[bytes] | None = None,
        max_write: int | None = None,
    ):
        self.pid = pid
        self.returncode = returncode
        self.stdin = _PartialWriter(max_write=max_write)
        self.stdout = _ChunkReader(stdout_chunks)
        self.stderr = _ChunkReader()
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0


class _ProcessFactory:
    def __init__(self, url: str, recording_path: str = "out.mp4"):
        self.url = url
        self.recording_path = recording_path
        self.upstream = _FakeProcess(pid=1001)
        self.recording = _FakeProcess(pid=1002)
        self.preview = _FakeProcess(pid=1003)
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]):
        self.commands.append(list(command))
        if self.url in command:
            return self.upstream
        if self.recording_path in command:
            return self.recording
        return self.preview


def _box(name: bytes, payload: bytes) -> bytes:
    return (len(payload) + 8).to_bytes(4, "big") + name + payload


def _sample_fmp4() -> bytes:
    return (
        _box(b"ftyp", b"a" * 8)
        + _box(b"moov", b"b" * 8)
        + _box(b"moof", b"c" * 8)
        + _box(b"mdat", b"d" * 8)
    )


def _wait_until(predicate, timeout_sec: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _disable_preview_start(ingest: SharedRoomIngest, monkeypatch) -> None:
    monkeypatch.setattr(
        ingest,
        "start_preview",
        lambda **_kwargs: SharedIngestStartResult(ok=True),
    )


def _configure_lifecycle(ingest: SharedRoomIngest, factory: _ProcessFactory, monkeypatch) -> None:
    monkeypatch.setattr(ingest, "_launch_process", factory)
    monkeypatch.setattr(ingest, "_wait_for_startup_data", lambda _path: True)


def test_registry_returns_same_ingest_and_updates_only_when_idle():
    registry = SharedIngestRegistry()
    first = registry.get_or_create("room-a", url="http://example/old.flv", headers={})
    second = registry.get_or_create(
        "room-a",
        url="http://example/new.flv",
        headers={"Referer": "https://example"},
    )

    assert second is first
    assert second.url == "http://example/new.flv"

    first.recording_active = True
    registry.get_or_create("room-a", url="http://example/ignored.flv", headers={})
    assert first.url == "http://example/new.flv"


def test_registry_stop_room_removes_and_stops_ingest():
    registry = SharedIngestRegistry()
    ingest = registry.get_or_create("room-a", url="http://example/live.flv", headers={})

    registry.stop_room("room-a", reason="test")

    assert registry.get("room-a") is None
    assert ingest.is_stopped is True


def test_three_commands_keep_network_input_only_in_upstream(tmp_path):
    url = "http://example/live.flv"
    output = tmp_path / "recording.mp4"
    ingest = SharedRoomIngest("room-a", url, headers={"Referer": "https://example"})

    upstream = ingest.build_upstream_command()
    recording = ingest.build_recording_command(str(output), ExportProfile(codec="copy"))
    preview = ingest.build_preview_command()

    assert upstream.count("-i") == 1
    assert upstream[upstream.index("-i") + 1] == url
    assert "-reconnect" in upstream
    assert "-headers" in upstream
    assert "-f" in upstream and "mpegts" in upstream
    assert "-mpegts_flags" in upstream and "+resend_headers" in upstream
    assert upstream[-1] == "pipe:1"

    for child in (recording, preview):
        assert child[child.index("-i") + 1] == "pipe:0"
        assert "mpegts" in child
        assert url not in child
        assert "-reconnect" not in child
        assert "-headers" not in child

    assert str(output) in recording
    assert preview[-1] == "pipe:1"


def test_upstream_omits_network_options_for_file_input(tmp_path):
    source = tmp_path / "source.ts"
    command = SharedRoomIngest("room-a", str(source)).build_upstream_command()

    for option in (
        "-timeout",
        "-rw_timeout",
        "-reconnect",
        "-reconnect_streamed",
        "-reconnect_delay_max",
    ):
        assert option not in command


@pytest.mark.parametrize(
    ("codec", "quality_option"),
    [
        ("libx264", "-crf"),
        ("libx265", "-crf"),
        ("h264_nvenc", "-cq"),
        ("hevc_nvenc", "-cq"),
    ],
)
def test_recording_command_reuses_export_profile(codec, quality_option):
    profile = ExportProfile(
        codec=codec,
        crf=21,
        preset="fast",
        audio_bitrate="192k",
        resolution="1280x720",
        fps=30,
    )

    command = SharedRoomIngest("room-a", "http://example/live.flv").build_recording_command(
        "out.mp4",
        profile,
    )

    assert codec in command
    assert quality_option in command
    assert "192k" in command
    assert "scale=1280:720,fps=30" in command
    assert profile._hardware_preset() in command if profile.is_hardware else "fast" in command


def test_recording_copy_profile_keeps_copy_without_filters():
    command = SharedRoomIngest("room-a", "http://example/live.flv").build_recording_command(
        "out.mp4",
        ExportProfile(codec="copy"),
    )

    assert command.count("copy") == 2
    assert "-vf" not in command


def test_recording_copy_profile_with_filter_explicitly_reencodes(monkeypatch):
    monkeypatch.setattr(
        "lsc.core.services.shared_ingest.preferred_hw_video_codec",
        lambda: "h264_nvenc",
    )
    command = SharedRoomIngest("room-a", "http://example/live.flv").build_recording_command(
        "out.mp4",
        ExportProfile(codec="copy", resolution="1280x720", fps=25, audio_bitrate="160k"),
    )

    assert command[command.index("-c:v") + 1] == "h264_nvenc"
    assert command[command.index("-c:a") + 1] == "aac"
    assert "scale=1280:720,fps=25" in command
    assert "160k" in command


def test_recording_copy_profile_with_filter_falls_back_to_libx264_without_nvenc(monkeypatch):
    monkeypatch.setattr(
        "lsc.core.services.shared_ingest.preferred_hw_video_codec",
        lambda: "libx264",
    )
    command = SharedRoomIngest("room-a", "http://example/live.flv").build_recording_command(
        "out.mp4",
        ExportProfile(codec="copy", resolution="1280x720", fps=25, audio_bitrate="160k"),
    )
    assert command[command.index("-c:v") + 1] == "libx264"


def test_preview_command_matches_mse_streamer_software_parameters():
    command = SharedRoomIngest("room-a", "http://example/live.flv").build_preview_command(
        width=960,
        height=540,
        use_nvenc=False,
        video_bitrate="1800k",
        crf_value=26,
    )

    assert "scale=960:540:force_original_aspect_ratio=decrease" in command
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-crf") + 1] == "26"
    assert command[command.index("-b:v") + 1] == "1800k"
    assert command[command.index("-maxrate") + 1] == "2400k"
    assert command[command.index("-bufsize") + 1] == "3600k"
    assert "frag_keyframe+empty_moov+default_base_moof" in command
    assert command[-1] == "pipe:1"


def test_preview_command_matches_mse_streamer_nvenc_parameters():
    command = SharedRoomIngest("room-a", "http://example/live.flv").build_preview_command(
        use_nvenc=True,
        video_bitrate="2500k",
    )

    assert command[command.index("-c:v") + 1] == "h264_nvenc"
    assert command[command.index("-preset") + 1] == "p4"
    assert command[command.index("-rc") + 1] == "cbr"
    assert command[command.index("-maxrate") + 1] == "3000k"
    assert command[command.index("-bufsize") + 1] == "5000k"


def test_upstream_reader_dispatches_only_complete_ts_packets_and_keeps_order():
    first = b"a" * 100
    second = b"b" * 100
    process = _FakeProcess(stdout_chunks=[first, second])
    recording = _FakeProcess(max_write=17)
    ingest = SharedRoomIngest("room-a", "http://example/live.flv")
    ingest._process = process
    ingest._recording_process = recording
    ingest.recording_active = True

    ingest._read_upstream_stdout_loop(process)

    assert bytes(recording.stdin.data) == (first + second)[:TS_PACKET_SIZE]
    assert len(recording.stdin.data) % TS_PACKET_SIZE == 0


@pytest.mark.parametrize(
    ("policy", "expected_preview"),
    [
        ("drop_oldest", b"b" * TS_PACKET_SIZE + b"c" * TS_PACKET_SIZE),
        ("drop_newest", b"a" * TS_PACKET_SIZE + b"b" * TS_PACKET_SIZE),
    ],
)
def test_preview_overflow_drops_whole_batches_while_recording_receives_all(
    policy,
    expected_preview,
):
    batches = [bytes([value]) * TS_PACKET_SIZE for value in (97, 98, 99)]
    upstream = _FakeProcess(stdout_chunks=batches)
    recording = _FakeProcess(max_write=19)
    preview = _FakeProcess()
    ingest = SharedRoomIngest(
        "room-a",
        "http://example/live.flv",
        preview_queue_bytes=TS_PACKET_SIZE * 2,
        preview_drop_policy=policy,
    )
    ingest._process = upstream
    ingest._recording_process = recording
    ingest._preview_process = preview
    ingest.recording_active = True
    ingest._preview_subscribers.append(PreviewSubscriber(1024))

    ingest._read_upstream_stdout_loop(upstream)

    assert bytes(recording.stdin.data) == b"".join(batches)
    assert b"".join(ingest._preview_ts_queue) == expected_preview
    assert all(len(batch) % TS_PACKET_SIZE == 0 for batch in ingest._preview_ts_queue)
    assert ingest.preview_dropped_bytes == TS_PACKET_SIZE
    assert ingest.preview_dropped_batches == 1


def test_preview_then_recording_reuses_same_upstream(monkeypatch):
    url = "http://example/live.flv"
    ingest = SharedRoomIngest("room-a", url)
    factory = _ProcessFactory(url)
    _configure_lifecycle(ingest, factory, monkeypatch)

    assert ingest.start_preview_only().ok is True
    subscriber = ingest.attach_preview_subscriber()
    upstream = ingest._process
    result = ingest.start_recording("out.mp4")

    assert result.ok is True
    assert ingest._process is upstream
    assert ingest.process_id == 1001
    assert ingest.preview_process_id == 1003
    assert ingest.recording_process_id == 1002
    assert sum(url in command for command in factory.commands) == 1

    ingest.detach_preview_subscriber(subscriber)
    ingest.stop_recording_sink()


def test_attach_to_logical_recording_without_process_does_not_launch_ffmpeg(monkeypatch):
    ingest = SharedRoomIngest("room-a", "http://example/live.flv")
    ingest.recording_active = True
    launched: list[list[str]] = []
    monkeypatch.setattr(ingest, "_launch_process", lambda command: launched.append(command))

    subscriber = ingest.attach_preview_subscriber()

    assert launched == []
    assert ingest.preview_subscribers == 1
    ingest.detach_preview_subscriber(subscriber)


def test_recording_then_preview_reuses_same_upstream(monkeypatch):
    url = "http://example/live.flv"
    ingest = SharedRoomIngest("room-a", url)
    factory = _ProcessFactory(url)
    _configure_lifecycle(ingest, factory, monkeypatch)

    assert ingest.start_recording("out.mp4").ok is True
    upstream = ingest._process
    subscriber = ingest.attach_preview_subscriber()

    assert ingest._process is upstream
    assert sum(url in command for command in factory.commands) == 1
    assert ingest.last_command == ingest.build_upstream_command()
    assert "out.mp4" in ingest.last_recording_command
    assert ingest.last_preview_command[-1] == "pipe:1"

    ingest.detach_preview_subscriber(subscriber)
    ingest.stop_recording_sink()


def test_stopping_one_sink_keeps_other_and_last_sink_stops_upstream(monkeypatch):
    url = "http://example/live.flv"
    ingest = SharedRoomIngest("room-a", url)
    factory = _ProcessFactory(url)
    _configure_lifecycle(ingest, factory, monkeypatch)

    assert ingest.start_recording("out.mp4").ok is True
    subscriber = ingest.attach_preview_subscriber()
    upstream = ingest._process

    ingest.stop_recording_sink(reason="recording stopped")

    assert factory.recording.terminated is True
    assert ingest.recording_active is False
    assert ingest._process is upstream
    assert ingest.preview_process_id == factory.preview.pid
    assert ingest.is_stopped is False

    ingest.detach_preview_subscriber(subscriber)

    assert factory.preview.terminated is True
    assert factory.upstream.terminated is True
    assert ingest.process_id is None
    assert ingest.is_stopped is True


def test_stopping_preview_keeps_recording_and_then_stops_upstream(monkeypatch):
    url = "http://example/live.flv"
    ingest = SharedRoomIngest("room-a", url)
    factory = _ProcessFactory(url)
    _configure_lifecycle(ingest, factory, monkeypatch)

    assert ingest.start_recording("out.mp4").ok is True
    subscriber = ingest.attach_preview_subscriber()

    ingest.detach_preview_subscriber(subscriber)

    assert factory.preview.terminated is True
    assert factory.upstream.terminated is False
    assert ingest.recording_process_id == factory.recording.pid

    ingest.stop_recording_sink()

    assert factory.upstream.terminated is True
    assert ingest.process_id is None


def test_preview_only_wrapper_does_not_create_child_without_subscriber(monkeypatch):
    ingest = SharedRoomIngest("room-a", "http://example/live.flv")
    launched: list[list[str]] = []
    monkeypatch.setattr(ingest, "_launch_process", lambda command: launched.append(command))

    result = ingest.start_preview_only()

    assert result.ok is True
    assert result.use_legacy_fallback is False
    assert launched == []
    assert ingest.process_id is None
    assert ingest.preview_process_id is None


def test_recording_and_preview_wrapper_only_delegates_recording(monkeypatch):
    ingest = SharedRoomIngest("room-a", "http://example/live.flv")
    calls: list[tuple[str, ExportProfile | None]] = []

    def start_recording(path: str, profile: ExportProfile | None = None):
        calls.append((path, profile))
        return SharedIngestStartResult(ok=True)

    monkeypatch.setattr(ingest, "start_recording", start_recording)

    result = ingest.start_recording_and_preview("out.mp4")

    assert result.ok is True
    assert calls == [("out.mp4", None)]
    assert ingest.preview_process_id is None


def test_upstream_failure_stops_all_children_and_subscribers():
    upstream = _FakeProcess(returncode=7, pid=1001)
    recording = _FakeProcess(pid=1002)
    preview = _FakeProcess(pid=1003)
    ingest = SharedRoomIngest("room-a", "http://example/live.flv")
    ingest._process = upstream
    ingest._recording_process = recording
    ingest._preview_process = preview
    ingest.recording_active = True
    ingest._preview_subscribers.append(PreviewSubscriber(1024))

    ingest._read_upstream_stdout_loop(upstream)

    assert ingest.is_stopped is True
    assert "code=7" in ingest.upstream_error
    assert ingest.recording_active is False
    assert ingest.preview_subscribers == 0
    assert recording.terminated is True
    assert preview.terminated is True


def test_recording_failure_isolated_from_preview_and_upstream():
    upstream = _FakeProcess(pid=1001)
    recording = _FakeProcess(returncode=8, pid=1002)
    preview = _FakeProcess(pid=1003)
    ingest = SharedRoomIngest("room-a", "http://example/live.flv")
    ingest._process = upstream
    ingest._recording_process = recording
    ingest._preview_process = preview
    ingest.recording_active = True
    ingest._preview_subscribers.append(PreviewSubscriber(1024))

    ingest._watch_recording_process_loop(recording)

    assert "code=8" in ingest.recording_error
    assert ingest.recording_active is False
    assert ingest.recording_process_id is None
    assert ingest._process is upstream
    assert ingest._preview_process is preview
    assert ingest.is_stopped is False


def test_preview_failure_isolated_from_recording_and_upstream():
    upstream = _FakeProcess(pid=1001)
    recording = _FakeProcess(pid=1002)
    preview = _FakeProcess(returncode=9, pid=1003)
    ingest = SharedRoomIngest("room-a", "http://example/live.flv")
    ingest._process = upstream
    ingest._recording_process = recording
    ingest._preview_process = preview
    ingest.recording_active = True
    ingest._preview_subscribers.append(PreviewSubscriber(1024))

    ingest._watch_preview_process_loop(preview)

    assert "code=9" in ingest.preview_error
    assert ingest.preview_process_id is None
    assert ingest._process is upstream
    assert ingest._recording_process is recording
    assert ingest.recording_active is True
    assert ingest.preview_subscribers == 1


def test_stale_process_exit_does_not_change_current_processes():
    current = _FakeProcess(pid=1001)
    stale = _FakeProcess(returncode=10, pid=999)
    ingest = SharedRoomIngest("room-a", "http://example/live.flv")
    ingest._recording_process = current
    ingest.recording_active = True

    ingest._watch_recording_process_loop(stale)

    assert ingest._recording_process is current
    assert ingest.recording_active is True
    assert ingest.recording_error == ""


def test_start_failure_never_requests_legacy_fallback(monkeypatch):
    ingest = SharedRoomIngest("room-a", "http://example/live.flv")
    monkeypatch.setattr(
        ingest,
        "_launch_process",
        lambda _command: (_ for _ in ()).throw(OSError("cannot launch")),
    )

    result = ingest.start_recording("out.mp4")

    assert result.ok is False
    assert result.use_legacy_fallback is False
    assert "cannot launch" in result.error


def test_preview_immediate_exit_is_concrete_failure_without_fallback(monkeypatch):
    ingest = SharedRoomIngest("room-a", "http://example/live.flv")
    preview = _FakeProcess(returncode=3, pid=1003)
    upstream = _FakeProcess(pid=1001)
    commands: list[list[str]] = []

    def launch(command: list[str]):
        commands.append(command)
        return upstream if ingest.url in command else preview

    monkeypatch.setattr(ingest, "_launch_process", launch)
    ingest._preview_subscribers.append(PreviewSubscriber(1024))

    result = ingest.start_preview()

    assert result.ok is False
    assert result.use_legacy_fallback is False
    assert "code=3" in result.error
    assert ingest.preview_process_id is None
    assert ingest.process_id is None


def test_shared_preview_handle_replays_and_drains_segments(monkeypatch):
    ingest = SharedRoomIngest("room-a", "http://example/live.flv")
    _disable_preview_start(ingest, monkeypatch)
    init_segments: list[bytes] = []
    media_segments: list[bytes] = []
    ingest.publish_preview_segment(b"init-data", kind="init")
    handle = SharedPreviewHandle(
        ingest,
        on_init_segment=init_segments.append,
        on_media_segment=media_segments.append,
    )

    ingest.publish_preview_segment(b"seg-1", kind="media")
    assert handle.replay_init() is True
    handle.drain()
    handle.stop()

    assert init_segments == [b"init-data"]
    assert media_segments == [b"seg-1"]
    assert ingest.preview_subscribers == 0


def test_shared_preview_handle_reports_preview_error_once_and_detaches(monkeypatch):
    ingest = SharedRoomIngest("room-a", "http://example/live.flv")
    _disable_preview_start(ingest, monkeypatch)
    errors: list[str] = []
    handle = SharedPreviewHandle(
        ingest,
        on_init_segment=lambda _data: None,
        on_media_segment=lambda _data: None,
        on_error=errors.append,
        pump_interval_sec=0.01,
        auto_start=True,
    )

    ingest.handle_preview_error("preview failed")

    assert _wait_until(lambda: errors == ["preview failed"])
    time.sleep(0.03)
    assert errors == ["preview failed"]
    assert handle.is_running is False
    assert ingest.preview_subscribers == 0


def test_preview_stream_registry_attaches_shared_handle(monkeypatch):
    registry = PreviewStreamRegistry()
    ingest = SharedRoomIngest("room-a", "http://example/live.flv")
    _disable_preview_start(ingest, monkeypatch)

    handle = registry.attach_shared(
        "room-a",
        ingest,
        on_init_segment=lambda _data: None,
        on_media_segment=lambda _data: None,
    )

    assert registry.get("room-a") is handle
    assert ingest.preview_subscribers == 1
    registry.stop_room("room-a")
    assert ingest.preview_subscribers == 0
