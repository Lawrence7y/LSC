"""录制目录布局必须接到开录 / 对齐 / 导出三条生产路径。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCH = (ROOT / "lsc/core/orchestrator.py").read_text(encoding="utf-8")
ALIGN = (ROOT / "python-backend/handlers/alignment_handlers.py").read_text(encoding="utf-8")
EXPORT = (ROOT / "python-backend/handlers/export_handlers.py").read_text(encoding="utf-8")
SESSION = (ROOT / "lsc/core/session.py").read_text(encoding="utf-8")


def test_orchestrator_reuses_streamer_folder_instead_of_suffixing() -> None:
    assert "room_recording_dir" in ORCH
    start = ORCH.split("def start_recording(", 1)[1].split("def start_recording_all(", 1)[0]
    assert "while os.path.exists(room_output_dir)" not in start


def test_orchestrator_uses_time_range_recording_names() -> None:
    assert "recording_in_progress_path" in ORCH
    assert "finalize_room_recording" in ORCH


def test_session_has_output_bundle_dir() -> None:
    assert "output_bundle_dir" in SESSION


def test_alignment_success_binds_output_bundle() -> None:
    apply = ALIGN.split("def _apply_alignment_and_create_timeline", 1)[1].split(
        "timeline_payload = None", 1
    )[0]
    assert "bind_rooms_to_bundle" in apply
    assert "align_group_id = group_id" in apply


def test_queue_export_writes_clips_into_room_layout_dir() -> None:
    body = EXPORT.split("async def queue_export(", 1)[1].split("async def ", 1)[0]
    assert "resolve_clip_output_dir" in body
