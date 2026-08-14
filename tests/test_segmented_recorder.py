from __future__ import annotations

from types import SimpleNamespace

from lsc.recorder.segmented import SegmentedRecorder


def test_segmented_command_uses_mkv_segments_and_headers(tmp_path):
    recorder = SegmentedRecorder(
        SimpleNamespace(ffmpeg_path="ffmpeg"),
        room_id="room-1",
        platform_id="bilibili",
        segment_seconds=60,
    )
    command = recorder.build_command(
        "https://cdn.example/live?token=SECRET",
        str(tmp_path / "segments"),
        headers={"Referer": "https://room.example"},
    )
    assert "-f" in command
    assert command[command.index("-f") + 1] == "segment"
    assert "-segment_time" in command
    assert "matroska" in command
    assert command[-1].endswith("%06d.partial.mkv")


def test_segmented_recorder_start_stop_with_fake_process(monkeypatch, tmp_path):
    class FakeProcess:
        def __init__(self):
            self.stdin = self
            self.returncode = None

        def poll(self):
            return self.returncode

        def write(self, value):
            self.returncode = 0

        def flush(self):
            return None

        def wait(self, timeout=None):
            self.returncode = 0

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = -9

    process = FakeProcess()
    monkeypatch.setattr(
        "lsc.recorder.segmented.subprocess.Popen",
        lambda *args, **kwargs: process,
    )
    recorder = SegmentedRecorder(
        SimpleNamespace(ffmpeg_path="ffmpeg"),
        room_id="room-1",
        platform_id="direct",
    )
    started = recorder.start("https://cdn.example/live", str(tmp_path))
    assert started.success
    segment = tmp_path.joinpath(
        started.session_dir.split(str(tmp_path))[1].lstrip("/\\"),
        "segments",
        "000001.partial.mkv",
    )
    segment.write_bytes(b"segment")
    stopped = recorder.stop()
    assert stopped.success
    assert stopped.segment_count == 1


def test_segmented_recorder_reuses_proxy_context(tmp_path):
    recorder = SegmentedRecorder(
        SimpleNamespace(ffmpeg_path="ffmpeg"),
        room_id="room-1",
        platform_id="direct",
        network_context={"proxy_url": "http://proxy.example:8080"},
    )
    command = recorder.build_command(
        "https://cdn.example/live.flv",
        str(tmp_path / "segments"),
    )
    assert "-http_proxy" in command
    assert "http://proxy.example:8080" in command
