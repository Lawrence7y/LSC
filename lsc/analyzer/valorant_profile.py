"""Valorant 输入来源策略。

``pov`` 保持主播/第一视角的既有 OCR 行为；``broadcast`` 用于官方赛事和
赛事二路转播，启用回放/暂停的保守边界审计。``auto`` 只负责把明显的赛事
标题路由到 broadcast，无法判断时必须回到 pov，保证普通直播不改变行为。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

VALID_PROFILES = frozenset({"auto", "pov", "broadcast"})

# 仅作为 auto 的提示，不把单个词当成绝对事实；显式 profile 优先级更高。
_BROADCAST_HINTS = (
    "官方赛事",
    "官方解说",
    "赛事直播",
    "赛事转播",
    "赛事解说",
    "比赛直播",
    "比赛解说",
    "赛事实况",
    "二路",
    "联赛",
    "锦标赛",
    "太平洋",
    "进化者",
    "vct",
    "valorant champions",
    "valorant masters",
    "tournament",
    "official match",
)


def normalize_valorant_profile(value: Any) -> str:
    """Normalize legacy/invalid values without changing the safe default."""
    profile = str(value or "auto").strip().lower()
    if profile == "valorant":
        return "auto"
    return profile if profile in VALID_PROFILES else "auto"


@dataclass(slots=True)
class ValorantProfileDecision:
    """Valorant 来源策略判定详情与诊断信息。"""

    requested_profile: str
    resolved_profile: str
    profile_reason: str  # "explicit", "title_hint", "fallback_pov"
    profile_mismatch_warning: bool
    warning_message: str = ""


def inspect_valorant_profile(
    requested: Any,
    *,
    streamer_name: Any = "",
    stream_title: Any = "",
    room_url: Any = "",
) -> ValorantProfileDecision:
    """检查并诊断来源策略。

    若显式选择 broadcast 但房间信息明显属于普通个人直播，标记
    profile_mismatch_warning=True 并附带提示，防止误选导致不可逆的分析滞后。
    """
    requested_norm = normalize_valorant_profile(requested)
    text = " ".join(
        str(part or "").strip().lower()
        for part in (streamer_name, stream_title, room_url)
    )
    compact = re.sub(r"\s+", "", text)
    has_broadcast_hint = any(hint in text or hint in compact for hint in _BROADCAST_HINTS)

    if requested_norm == "broadcast":
        is_mismatch = bool(text and not has_broadcast_hint)
        warning = (
            "当前直播间无明显赛事特征，但已显式启用赛事策略 (broadcast)，可能导致分析滞后"
            if is_mismatch
            else ""
        )
        return ValorantProfileDecision(
            requested_profile="broadcast",
            resolved_profile="broadcast",
            profile_reason="explicit",
            profile_mismatch_warning=is_mismatch,
            warning_message=warning,
        )

    if requested_norm == "pov":
        is_mismatch = bool(text and has_broadcast_hint)
        warning = (
            "当前直播间具有官方赛事/解说特征，但已显式使用第一视角策略 (pov)，"
            "回放/暂停画面可能进入切片，建议切换为“自动”或“官方赛事/二路转播”。"
            if is_mismatch
            else ""
        )
        return ValorantProfileDecision(
            requested_profile="pov",
            resolved_profile="pov",
            profile_reason="explicit",
            profile_mismatch_warning=is_mismatch,
            warning_message=warning,
        )

    # auto 模式
    if has_broadcast_hint:
        return ValorantProfileDecision(
            requested_profile="auto",
            resolved_profile="broadcast",
            profile_reason="title_hint",
            profile_mismatch_warning=False,
            warning_message="",
        )
    return ValorantProfileDecision(
        requested_profile="auto",
        resolved_profile="pov",
        profile_reason="fallback_pov",
        profile_mismatch_warning=False,
        warning_message="",
    )


def resolve_valorant_profile(
    requested: Any,
    *,
    streamer_name: Any = "",
    stream_title: Any = "",
    room_url: Any = "",
) -> str:
    """Resolve an explicit or automatic source profile.

    The fallback is ``pov`` because it is the current production behavior and
    has the better false-negative tradeoff for ordinary streams.
    """
    return inspect_valorant_profile(
        requested,
        streamer_name=streamer_name,
        stream_title=stream_title,
        room_url=room_url,
    ).resolved_profile


__all__ = [
    "VALID_PROFILES",
    "ValorantProfileDecision",
    "inspect_valorant_profile",
    "normalize_valorant_profile",
    "resolve_valorant_profile",
]
