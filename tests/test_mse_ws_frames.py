"""MSE 二进制 WebSocket 帧编解码测试。"""
from __future__ import annotations

import os
import sys

_backend = os.path.join(os.path.dirname(__file__), "..", "python-backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from mse_ws_frames import KIND_INIT, KIND_SEGMENT, pack_mse_frame, unpack_mse_frame


def test_pack_unpack_roundtrip_init() -> None:
    payload = b"ftyp" + b"\x00" * 20
    frame = pack_mse_frame("mse_init", "room-abc", payload)
    assert frame[:3] == b"MSE"
    assert frame[3] == KIND_INIT
    parsed = unpack_mse_frame(frame)
    assert parsed is not None
    msg_type, room_id, out = parsed
    assert msg_type == "mse_init"
    assert room_id == "room-abc"
    assert out == payload


def test_pack_unpack_segment_alias() -> None:
    frame = pack_mse_frame("segment", "r1", b"moofmdat")
    parsed = unpack_mse_frame(frame)
    assert parsed == ("mse_segment", "r1", b"moofmdat")
    assert frame[3] == KIND_SEGMENT


def test_unpack_rejects_non_mse() -> None:
    assert unpack_mse_frame(b"JSON{not mse}") is None
    assert unpack_mse_frame(b"MS") is None
