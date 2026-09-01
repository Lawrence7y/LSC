"""CDN 拉流 URL 过期时间解析。

只认具名过期参数（expire / expires / wsTime），禁止扫描全部 query。
抖音 FLV 常带 volcTime、短 hex 等「看起来像当前 unix 时间」的字段，
若全部当过期戳，刚解析出的新地址也会被心跳判成即将过期，从而十几秒拆一次录制。
"""
from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

# 与 registry 历史约定对齐：抖音 expire、B 站 expires、虎牙/抖音 wsTime
_EXPIRE_PARAM_KEYS = ("expire", "expires", "wstime")
_DEFAULT_THRESHOLD_SEC = 60
# 刚拿到的地址若已落在「即将过期」窗口，刷新只会拿到同样短 TTL，禁止立刻再拆进程
_FRESH_PARSE_GRACE_SEC = 90.0


def _parse_timestamp(raw: str) -> float | None:
    if not raw:
        return None
    try:
        if raw.isdigit():
            ts = int(raw, 10)
        elif len(raw) >= 6 and all(c in "0123456789abcdefABCDEF" for c in raw):
            ts = int(raw, 16)
        else:
            return None
    except (ValueError, OverflowError):
        return None
    if ts <= 0:
        return None
    return float(ts)


def parse_stream_url_expiry(url: str) -> float | None:
    """返回具名过期参数的 unix 时间戳；没有则 None。"""
    if not url:
        return None
    try:
        params = parse_qs(urlparse(url).query, keep_blank_values=False)
    except Exception:
        return None
    for key, vals in params.items():
        if key.lower() not in _EXPIRE_PARAM_KEYS or not vals:
            continue
        ts = _parse_timestamp(vals[0])
        if ts is not None:
            return ts
    return None


def is_stream_url_expiring(
    url: str,
    *,
    now: float | None = None,
    threshold_sec: int = _DEFAULT_THRESHOLD_SEC,
) -> bool:
    """仅当具名过期戳仍在未来、且剩余时间不超过 threshold 时为 True。

    已过期或 expire≈签发时刻（剩余 ≤ 0）返回 False：正在供流的地址不要主动拆掉，
    真正断流交给 watchdog / 文件停滞检测。
    """
    ts = parse_stream_url_expiry(url)
    if ts is None:
        return False
    current = time.time() if now is None else now
    remaining = ts - current
    return 0 < remaining <= threshold_sec


def should_proactively_refresh_stream(
    url: str,
    parsed_at: float = 0.0,
    *,
    now: float | None = None,
    threshold_sec: int = _DEFAULT_THRESHOLD_SEC,
) -> bool:
    """心跳是否应为主动刷新而重启录制。

    刚解析出的短 TTL 地址即使命中「即将过期」，刷新也改变不了平台签发窗口，
    必须等地址用过一段时间后再考虑刷新；否则会形成无限重连。
    """
    current = time.time() if now is None else now
    if not is_stream_url_expiring(url, now=current, threshold_sec=threshold_sec):
        return False
    return not (parsed_at > 0 and (current - parsed_at) < _FRESH_PARSE_GRACE_SEC)
