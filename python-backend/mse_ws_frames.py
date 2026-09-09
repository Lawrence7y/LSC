"""MSE WebSocket 二进制帧编解码（去 base64）。

帧布局（big-endian）::

    v1 默认 (b'MSE')：
    magic(3) = b'MSE'
    kind(1)  = 1 init | 2 segment
    rid_len(2)
    room_id (utf-8, rid_len bytes)
    payload (fMP4 bytes)

    v2 双通道扩展 (b'MS2')：
    magic(3) = b'MS2'
    kind(1)  = 1 init | 2 segment
    channel(1) = 0 live | 1 review
    sid_len(1)
    stream_id (utf-8, sid_len bytes)
    rid_len(2)
    room_id (utf-8, rid_len bytes)
    payload (fMP4 bytes)
"""
from __future__ import annotations

MSE_MAGIC = b"MSE"
MS2_MAGIC = b"MS2"
KIND_INIT = 1
KIND_SEGMENT = 2

CHANNEL_LIVE = 0
CHANNEL_REVIEW = 1

_CHANNEL_TO_BYTE = {
    "live": CHANNEL_LIVE,
    "review": CHANNEL_REVIEW,
}
_BYTE_TO_CHANNEL = {
    CHANNEL_LIVE: "live",
    CHANNEL_REVIEW: "review",
}

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


def pack_mse_frame(
    kind: str | int,
    room_id: str,
    payload: bytes,
    *,
    channel: str = "live",
    stream_id: str = "",
) -> bytes:
    """打包 MSE 二进制帧。kind 可为类型名或 1/2。

    当 channel == 'live' 且无 stream_id 时保持 v1 格式 (b'MSE')；
    当 channel == 'review' 或指定了 stream_id 时输出 v2 格式 (b'MS2')。
    """
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

    # 默认 live 且无 stream_id 时保持完全向后兼容的 v1 格式
    if channel == "live" and not stream_id:
        return MSE_MAGIC + bytes([kind_byte]) + len(rid).to_bytes(2, "big") + rid + payload

    # v2 双通道扩展格式
    chan_byte = _CHANNEL_TO_BYTE.get(channel, CHANNEL_LIVE)
    sid_bytes = str(stream_id or "").encode("utf-8")[:255]
    sid_len = len(sid_bytes)

    return (
        MS2_MAGIC
        + bytes([kind_byte, chan_byte, sid_len])
        + sid_bytes
        + len(rid).to_bytes(2, "big")
        + rid
        + payload
    )


def unpack_mse_frame(
    data: bytes | memoryview,
    *,
    include_channel: bool = False,
) -> tuple[str, str, bytes] | tuple[str, str, bytes, str, str] | None:
    """解包 MSE 二进制帧（支持 v1 b'MSE' 与 v2 b'MS2'）。

    Returns:
        若 include_channel=False (默认): (message_type, room_id, payload)
        若 include_channel=True: (message_type, room_id, payload, channel, stream_id)
        或 None（非 MSE 帧）。
    """
    buf = bytes(data) if not isinstance(data, (bytes, bytearray)) else data
    if len(buf) < 6:
        return None

    magic = buf[:3]
    if magic == MSE_MAGIC:
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
        payload = bytes(buf[header_end:])
        if include_channel:
            return msg_type, room_id, payload, "live", ""
        return msg_type, room_id, payload

    if magic == MS2_MAGIC:
        if len(buf) < 8:
            return None
        kind_byte = buf[3]
        msg_type = _KIND_TO_TYPE.get(kind_byte)
        if msg_type is None:
            return None
        chan_byte = buf[4]
        channel = _BYTE_TO_CHANNEL.get(chan_byte, "live")
        sid_len = buf[5]
        sid_end = 6 + sid_len
        if len(buf) < sid_end + 2:
            return None
        try:
            stream_id = buf[6:sid_end].decode("utf-8")
        except UnicodeDecodeError:
            return None
        rid_len = int.from_bytes(buf[sid_end:sid_end + 2], "big")
        header_end = sid_end + 2 + rid_len
        if rid_len <= 0 or len(buf) < header_end:
            return None
        try:
            room_id = buf[sid_end + 2:header_end].decode("utf-8")
        except UnicodeDecodeError:
            return None
        payload = bytes(buf[header_end:])
        if include_channel:
            return msg_type, room_id, payload, channel, stream_id
        return msg_type, room_id, payload

    return None

