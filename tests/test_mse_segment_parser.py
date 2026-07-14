from __future__ import annotations

from lsc.core.services.fmp4_segments import Fmp4SegmentParser


def _box(kind: bytes, payload: bytes) -> bytes:
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


def test_parser_emits_init_and_media_segments():
    parser = Fmp4SegmentParser()
    emitted = parser.feed(
        _box(b"ftyp", b"a" * 8)
        + _box(b"moov", b"b" * 8)
        + _box(b"moof", b"c" * 8)
        + _box(b"mdat", b"d" * 8)
    )

    assert [segment.kind for segment in emitted] == ["init", "media"]
    assert parser.last_init_segment == emitted[0].data


def test_parser_holds_partial_box_until_complete():
    parser = Fmp4SegmentParser()

    assert parser.feed(b"\x00\x00\x00\x10ft") == []
    emitted = parser.feed(b"yp" + b"a" * 8 + _box(b"moov", b"b" * 8))

    assert len(emitted) == 1
    assert emitted[0].kind == "init"
