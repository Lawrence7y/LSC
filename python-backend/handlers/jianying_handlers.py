"""剪映草稿导出 WebSocket handlers。"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

from lsc.exporter.jianying_draft import detect_jianying_draft_dir

_log = logging.getLogger(__name__)


def _resolve_draft_root(settings: dict) -> tuple[str | None, bool]:
    """返回 (path, auto_detected)。"""
    configured = (settings.get("jianying_draft_dir") or "").strip()
    if configured:
        return configured, False
    detected = detect_jianying_draft_dir()
    return detected, True


def register_jianying_handlers(
    server,
    *,
    bridge,
    manager,
    load_settings: Callable[[], dict],
) -> None:
    """注册剪映相关 WS handlers。"""

    @server.on("get_jianying_draft_dir")
    async def handle_get_jianying_draft_dir(data: dict[str, Any] | None):
        settings = load_settings()
        path, auto = _resolve_draft_root(settings)
        exists = bool(path and os.path.isdir(path))
        return {
            "success": True,
            "draft_dir": path or "",
            "auto_detected": auto and bool(path),
            "exists": exists,
        }

    # generate_jianying_draft — Task 3
