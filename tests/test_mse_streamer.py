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

    import lsc.core.services.mse_streamer as mse_mod
    from unittest.mock import patch

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
