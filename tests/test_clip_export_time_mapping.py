"""导出时间映射契约：列表导出必须使用切片快照墙钟，不得覆盖为房间当前 mark。"""
from pathlib import Path

from handlers.room_handler import _resolve_export_range

ROOT = Path(__file__).resolve().parents[1]


def test_queue_export_prefers_request_wallclock_snapshot_over_room_marks():
    """export_clip 携带快照墙钟时，不得改用房间当前 mark_*_wallclock。"""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    body = source.split("async def queue_export(", 1)[1].split("async def ", 1)[0]
    helper = source.split("def _resolve_export_range(", 1)[1].split(
        "\ndef register_room_handlers", 1
    )[0]
    assert "mark_in_wallclock" in body
    assert "snapshot" in body or "req_mark_in_wc" in body or "data.get('mark_in_wallclock')" in source
    assert "export_start = max(0.0, mark_in_wc - rec_start" in body or "snap_in" in body or "snap_rec" in body
    assert (
        "data.get('mark_in_wallclock')" in source
        or "payload.get('mark_in_wallclock')" in source
        or "mark_in_wallclock=data.get" in source.replace(" ", "")
        or "mark_in_wallclock=" in source  # passed into queue_export from handler
    )
    # queue_export 委托 _resolve_export_range；approximate 必须回退 start_sec，不得读房间当前 mark
    assert "_resolve_export_range" in body
    assert "start_sec" in helper
    assert "approximate" in helper
    assert "room.mark_in" not in helper
    assert "getattr(room, 'mark_in'" not in helper
    assert "部分墙钟快照缺失" in body
    assert "无墙钟快照" in body
    # use_room_marks 门控房间当前 mark，不得无条件覆盖快照路径
    assert "use_room_marks" in helper
    assert "start_sec - content_offset" in helper


def test_resolve_export_range_snapshot_wins_over_room_marks():
    """完整快照优先于冲突的房间当前 mark。"""
    export_start, export_end, precision = _resolve_export_range(
        10.0,
        20.0,
        source='',
        content_offset=0.5,
        snap_in=100.0,
        snap_out=110.0,
        snap_rec=90.0,
        use_room_marks=True,
        room_mark_in=200.0,
        room_mark_out=220.0,
        room_rec_start=50.0,
    )
    # snap: (100-90-0.5, 110-90-0.5) = (9.5, 19.5)；不得用 room marks
    assert precision == 'exact'
    assert export_start == 9.5
    assert export_end == 19.5


def test_resolve_export_range_partial_snapshot_is_approximate():
    """部分快照缺失时降级为 start/end - content_offset。"""
    export_start, export_end, precision = _resolve_export_range(
        10.0,
        20.0,
        source='',
        content_offset=1.0,
        snap_in=100.0,
        snap_out=None,
        snap_rec=90.0,
        use_room_marks=False,
        room_mark_in=200.0,
        room_mark_out=220.0,
        room_rec_start=50.0,
    )
    assert precision == 'approximate'
    assert export_start == 9.0
    assert export_end == 19.0


def test_resolve_export_range_ai_highlight_ignores_snaps():
    """ai_highlight 忽略快照，直接使用传入 start/end。"""
    export_start, export_end, precision = _resolve_export_range(
        3.0,
        8.0,
        source='ai_highlight',
        content_offset=2.0,
        snap_in=100.0,
        snap_out=110.0,
        snap_rec=90.0,
        use_room_marks=False,
        room_mark_in=None,
        room_mark_out=None,
        room_rec_start=None,
    )
    assert precision == 'exact'
    assert export_start == 3.0
    assert export_end == 8.0
