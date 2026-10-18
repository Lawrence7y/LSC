"""平台层降级事件计数（2026-09-18 债务收口 P2-2）。

`FEATURE-INVENTORY §1` 把平台接入层列为产品唯一入口 P0，但这一层的失效此前
**只写日志、不进任何聚合出口**：解析器被平台改版打掉（`parser_stale`）、虎牙
SDK 链接改写回退、Cookie 被判失效——用户侧的表现都是"房间转圈/预览黑屏"，
日志翻完也拼不出"哪个平台、哪一类、一共发生了多少次"。

这里只做一件事：按 `(platform, kind)` 累计计数，并提供一个可序列化快照给
`get_system_stats` 的 `extra` 带出去。刻意不引入直方图/时间窗/告警策略——那些
属于上层策略，本模块只保证"降级不会静默"。

计数是进程级的、键空间由调用方枚举决定（平台 × 事件类别），无需淘汰。
"""
from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_counts: dict[str, dict[str, int]] = {}
_seen_reasons: set[str] = set()


def note_degradation(platform: str, kind: str, *, reason: str = "") -> bool:
    """记一次降级事件。

    返回 `True` 表示这个 `(platform, kind, reason)` 组合**首次**出现——调用方据此
    打一条 WARNING 就够，重复事件只累计计数不再刷日志。`reason` 留空时每次都
    返回 `False`（无区分度，不该告警）。
    """
    key = str(platform or "unknown")
    sub = str(kind or "unknown")
    with _lock:
        bucket = _counts.setdefault(key, {})
        bucket[sub] = bucket.get(sub, 0) + 1
        if not reason:
            return False
        reason_key = f"{key}|{sub}|{reason}"
        if reason_key in _seen_reasons:
            return False
        _seen_reasons.add(reason_key)
        return True


def degradation_snapshot() -> dict[str, Any]:
    """给 `system_stats` 用的只读快照：`{platform: {kind: count}}`。

    只读、可 JSON 序列化；计数为 0 的平台不会出现。
    """
    with _lock:
        return {p: dict(kinds) for p, kinds in _counts.items()}


def reset() -> None:
    """清空计数（仅测试用）。"""
    with _lock:
        _counts.clear()
        _seen_reasons.clear()
