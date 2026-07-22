"""MSE WebSocket 二进制帧编解码（去 base64）。

帧布局（big-endian）::

    magic(3) = b'MSE'
    kind(1)  = 1 init | 2 segment
    rid_len(2)
    room_id (utf-8, rid_len bytes)
    payload (fMP4 bytes)
"""
from __future__ import annotations

MSE_MAGIC = b"MSE"
KIND_INIT = 1
KIND_SEGMENT = 2

_KIND_TO_TYPE = {
    KIND_INIT: "mse_init",
    KIND_SEGMENT: "mse_segment",
}
_TYPE_TO_KIND = {
    "mse_init": KIND_INIT,
    "mse_segment": KIND_SEGMENT,
    "init": KIND_INIT,
    "segment": KIND_SEGMENT,
    "media": KIND_SEGMENT,
}


def pack_mse_frame(kind: str | int, room_id: str, payload: bytes) -> bytes:
    """打包 MSE 二进制帧。kind 可为类型名或 1/2。"""
    if isinstance(kind, str):
        kind_byte = _TYPE_TO_KIND.get(kind)
        if kind_byte is None:
            raise ValueError(f"unsupported mse kind: {kind}")
    else:
        kind_byte = int(kind)
        if kind_byte not in _KIND_TO_TYPE:
            raise ValueError(f"unsupported mse kind byte: {kind_byte}")
    rid = room_id.encode("utf-8")
    if len(rid) > 0xFFFF:
        raise ValueError("room_id too long for mse frame header")
    return MSE_MAGIC + bytes([kind_byte]) + len(rid).to_bytes(2, "big") + rid + payload


def unpack_mse_frame(data: bytes | memoryview) -> tuple[str, str, bytes] | None:
    """解包 MSE 二进制帧。

    Returns:
        (message_type, room_id, payload) 或 None（非 MSE 帧）。
    """
    buf = bytes(data) if not isinstance(data, (bytes, bytearray)) else data
    if len(buf) < 6 or buf[:3] != MSE_MAGIC:
        return None
    kind_byte = buf[3]
    msg_type = _KIND_TO_TYPE.get(kind_byte)
    if msg_type is None:
        return None
    rid_len = int.from_bytes(buf[4:6], "big")
    header_end = 6 + rid_len
    if rid_len <= 0 or len(buf) < header_end:
        return None
    try:
        room_id = buf[6:header_end].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return msg_type, room_id, bytes(buf[header_end:])
