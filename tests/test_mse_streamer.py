from __future__ import annotations

from lsc.core.services.mse_streamer import MseStreamer


def _mp4_box(kind: bytes, payload: bytes) -> bytes:
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


class _FakeStdout:
    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    def read(self, _size: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _FakeProcess:
    def __init__(self, chunks: list[bytes]):
        self.stdout = _FakeStdout(chunks)
        self.stderr = None

    def poll(self) -> int:
        return 0


def test_mse_streamer_file_mode_skips_network_flags() -> None:
    captured_cmd: list[str] = []

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            captured_cmd.extend(cmd)
            self.stdout = None
            self.stderr = None

        def poll(self):
            return 0

    from unittest.mock import patch

    import lsc.core.services.mse_streamer as mse_mod

    streamer = mse_mod.MseStreamer(
        url=r"C:\recordings\room.mp4",
        is_file=True,
        on_init_segment=lambda _b: None,
        on_media_segment=lambda _b: None,
    )
    with patch.object(mse_mod, "prepare_launch", return_value=(None, 0, None)), patch.object(
        mse_mod, "set_stream_nonblocking"
    ), patch("lsc.core.services.mse_streamer.subprocess.Popen", _FakePopen):
        streamer.start(startup_probe_timeout=0.2)

    assert "-reconnect" not in captured_cmd
    assert "-timeout" not in captured_cmd
    assert "-re" in captured_cmd
    assert r"C:\recordings\room.mp4" in captured_cmd


def test_segment_reader_emits_media_when_moof_starts_current_buffer() -> None:
    init_segment = _mp4_box(b"ftyp", b"isom") + _mp4_box(b"moov", b"init")
    media_segment = _mp4_box(b"moof", b"traf") + _mp4_box(b"mdat", b"frame")
    emitted_init: list[bytes] = []
    emitted_media: list[bytes] = []

    streamer = MseStreamer(
        url="http://example.invalid/live.flv",
        on_init_segment=emitted_init.append,
        on_media_segment=emitted_media.append,
    )
    streamer._process = _FakeProcess([init_segment + media_segment])  # type: ignore[assignment]
    streamer._running = True

    streamer._read_segments()

    assert emitted_init == [init_segment]
    assert emitted_media == [media_segment]


def test_try_start_probe_timeout_with_403_stderr_fails() -> None:
    """探测超时但 stderr 含 403 时判定启动失败，避免 CDN 403 假成功（回归 #4b）。"""
    from unittest.mock import MagicMock, patch

    import lsc.core.services.mse_streamer as mse_mod

    streamer = mse_mod.MseStreamer(
        url="http://example.invalid/live.flv",
        on_init_segment=lambda _b: None,
        on_media_segment=lambda _b: None,
        on_error=lambda _e: None,
    )
    # FFmpeg 进程挂起（poll=None）且不产出 init 段，模拟 403 后进程僵住
    proc = MagicMock()
    proc.poll.return_value = None
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    proc.stderr.readline.return_value = b""

    def _fake_read_stderr() -> None:
        streamer._last_stderr = "Server returned 403 Forbidden for stream request"

    streamer._read_stderr = _fake_read_stderr
    streamer._read_segments = lambda: None

    with patch.object(mse_mod, "prepare_launch", return_value=(None, 0, None)), patch.object(
        mse_mod, "set_stream_nonblocking"
    ), patch.object(mse_mod.subprocess, "Popen", return_value=proc):
        try:
            ok = streamer._try_start(hwaccel_mode="", use_nvenc=False, startup_probe_timeout=0.3)
        finally:
            streamer.stop()

    assert ok is False
    assert streamer._error_reported is True


def test_try_start_probe_timeout_without_error_assumes_success() -> None:
    """探测超时但 stderr 无失败特征时仍假定成功（B站 init 段慢产出，回归 #4b 兼容性）。"""
    from unittest.mock import MagicMock, patch

    import lsc.core.services.mse_streamer as mse_mod

    streamer = mse_mod.MseStreamer(
        url="http://example.invalid/live.flv",
        on_init_segment=lambda _b: None,
        on_media_segment=lambda _b: None,
        on_error=lambda _e: None,
    )
    proc = MagicMock()
    proc.poll.return_value = None
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    proc.stderr.readline.return_value = b""

    streamer._read_segments = lambda: None

    with patch.object(mse_mod, "prepare_launch", return_value=(None, 0, None)), patch.object(
        mse_mod, "set_stream_nonblocking"
    ), patch.object(mse_mod.subprocess, "Popen", return_value=proc):
        try:
            ok = streamer._try_start(hwaccel_mode="", use_nvenc=False, startup_probe_timeout=0.2)
        finally:
            streamer.stop()

    assert ok is True
