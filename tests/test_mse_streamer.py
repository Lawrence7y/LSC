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
