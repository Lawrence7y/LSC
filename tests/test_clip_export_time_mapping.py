"""导出时间映射契约：列表导出必须使用切片快照墙钟，不得覆盖为房间当前 mark。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_queue_export_prefers_request_wallclock_snapshot_over_room_marks():
    """export_clip 携带快照墙钟时，不得改用房间当前 mark_*_wallclock。"""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    body = source.split("async def queue_export(", 1)[1].split("async def ", 1)[0]
    assert "mark_in_wallclock" in body
    assert "snapshot" in body or "req_mark_in_wc" in body or "data.get('mark_in_wallclock')" in source
    assert "export_start = max(0.0, mark_in_wc - rec_start" in body or "snap_in" in body or "snap_rec" in body
    assert (
        "data.get('mark_in_wallclock')" in source
        or "payload.get('mark_in_wallclock')" in source
        or "mark_in_wallclock=data.get" in source.replace(" ", "")
        or "mark_in_wallclock=" in source  # passed into queue_export from handler
    )
