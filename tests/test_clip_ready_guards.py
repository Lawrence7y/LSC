"""clip_ready 门控守卫：无录制 ID 不得宣称精确切片。

精确导出只允许：
1. I/O 键 live=true 墙钟路径，或
2. TimelineContext + create_clip_snapshot + export_clip_by_id（须 clip_ready）

create_clip_snapshot handler 必须在校验 ctx 后拒绝 clip_ready=false。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _timeline_handler_source() -> str:
    """读取 timeline/export handler 源码（C9 抽离后从 timeline_handlers.py 读取）。"""
    extracted = ROOT / "python-backend/handlers/timeline_handlers.py"
    if extracted.exists():
        return extracted.read_text(encoding="utf-8")
    return (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")


def test_create_clip_snapshot_guard_precedes_snapshot_creation() -> None:
    src = _timeline_handler_source()
    handler_pos = src.find("@server.on('create_clip_snapshot')")
    assert handler_pos != -1, "create_clip_snapshot handler 未注册"
    region = src[handler_pos:]
    # clip_ready 门控必须在服务调用 .create_clip_snapshot( 之前出现
    # （用 ".create_clip_snapshot(" 区分服务调用与 handler 名/装饰器）
    guard_pos = region.find("CLIP_NOT_READY")
    snap_pos = region.find(".create_clip_snapshot(")
    assert guard_pos != -1 and snap_pos != -1
    assert guard_pos < snap_pos
