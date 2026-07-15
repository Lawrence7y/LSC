"""UX 终审 Important 项回归：对齐组清除、offset 快照、重连画质、排队、单房文案。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]


def test_align_failure_and_low_confidence_clear_align_group_id() -> None:
    """可信不足或低置信房间必须清除 align_group_id，避免分析门槛被旧组绕过。"""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    body = source.split("async def handle_align_preview_audio", 1)[1].split(
        "async def handle_check_dependencies", 1
    )[0]
    assert "align_group_id = ''" in body or 'align_group_id = ""' in body
    # 失败路径（trusted < 2）也要清组
    fail_branch = body.split("if len(trusted) < 2:", 1)[1].split("import time as _align_time", 1)[0]
    assert "align_group_id" in fail_branch
    # 低置信分支清组
    low = body.split("if score < _ALIGN_TRUST_THRESHOLD:", 1)[1].split("room.content_offset = float", 1)[0]
    assert "align_group_id" in low


def test_resolve_export_range_uses_snapshotted_content_offset() -> None:
    """导出优先使用请求里的 content_offset 快照，不被房间当前值隐含覆盖。"""
    from handlers.room_handler import _resolve_export_range

    export_start, export_end, precision = _resolve_export_range(
        10.0,
        20.0,
        source="",
        content_offset=2.0,  # 切片入队时快照
        snap_in=100.0,
        snap_out=110.0,
        snap_rec=90.0,
    )
    assert precision == "exact"
    assert export_start == 8.0  # 100-90-2
    assert export_end == 18.0


def test_add_clip_and_export_snapshot_content_offset() -> None:
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    types = (ROOT / "lsc-electron/src/types/index.ts").read_text(encoding="utf-8")
    handler = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")

    clip_type = types.split("export interface ClipSegment", 1)[1].split("export interface StreamInfo", 1)[0]
    assert "content_offset" in clip_type

    add_body = workbench.split("const handleAddClip = useCallback", 1)[1].split("}, [addClip])", 1)[0]
    assert "content_offset:" in add_body

    export_many = workbench.split("const handleExportMany", 1)[1].split("const handleOpenExportFile", 1)[0]
    assert "content_offset: clip.content_offset" in export_many

    export_clip = handler.split("async def handle_export_clip", 1)[1].split("@server.on(", 1)[0]
    assert "content_offset" in export_clip
    queue_sig = handler.split("async def queue_export(", 1)[1].split("):", 1)[0]
    assert "content_offset" in queue_sig


def test_mse_reconnect_uses_compute_preview_quality_params() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    # 重连循环内不得再手写 >=6 / >=8 降级；应复用统一函数
    # _on_mse_error 内含嵌套 async def，不能按首个 async def 截断
    reconnect_loop = source.split("async def _on_mse_error", 1)[1].split("while True:", 1)[1]
    assert "_compute_preview_quality_params" in reconnect_loop
    assert "active_mse_count >= 8" not in reconnect_loop
    assert "active_mse_count >= 6" not in reconnect_loop

    compute = source.split("def _compute_preview_quality_params", 1)[1].split(
        "\ndef _preview_quality_response_fields", 1
    )[0]
    # 死代码阶梯应已删除
    assert "active_mse_count >= 8" not in compute
    assert "active_mse_count >= 6" not in compute


def test_recording_queue_when_multiple_starting() -> None:
    """Semaphore 未 locked 但已有 ≥2 路正在启动时，新请求也应进入排队。"""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    body = source.split("async def handle_start_recording", 1)[1].split(
        "async def handle_stop_recording", 1
    )[0]
    assert "recording_queue" in body
    assert "_recording_starting" in body
    # 排队条件不只看 locked()
    assert "len(_recording_starting)" in body or "starting_others" in body


def test_single_room_align_does_not_claim_success() -> None:
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    align = workbench.split("const handleAlignLive = useCallback", 1)[1].split(
        "}, [selectedRoomIds", 1
    )[0]
    # 单房/不足 2 房缓冲对齐不得 message.success 宣称「已对齐」
    assert "message.success(`已对齐" not in align
    assert "单房间" in align or "message.info" in align
