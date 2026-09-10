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


def test_sidecar_suffixes_match_persistence_helpers() -> None:
    """sidecar 后缀单一事实来源守卫（P1 后续：命名分裂修复）。

    ``recording_layout.SIDECAR_SUFFIXES`` 用来在录像定稿改名时同步搬 sidecar；
    一旦与 ``persistence.py`` 的路径助手漂移，改名就会漏项，读取方按最终录像名
    查 ``{stem}.analysis.json`` 落空（剪映导出权威对账会因此漏掉被拒标记）。
    """
    import sys

    sys.path.insert(0, str(ROOT / "python-backend"))
    import persistence  # noqa: PLC0415

    from lsc.core.recording_layout import SIDECAR_SUFFIXES

    probe = "2026-09-10_18-05-24_至_2026-09-10_18-29-20.mp4"
    stem = probe[: -len(".mp4")]  # sidecar 由 stem 派生，不含扩展名
    expected = {
        str(persistence._analysis_json_path(probe))[len(stem):],
        str(persistence._finalization_json_path(probe))[len(stem):],
    }
    missing = expected - set(SIDECAR_SUFFIXES)
    assert not missing, f"persistence 的 sidecar 后缀未纳入改名列表: {missing}"


def test_finalize_moves_recording_sidecars() -> None:
    """定稿改名必须同时搬运 sidecar（源码级守卫，防回退）。"""
    src = (ROOT / "lsc/core/recording_layout.py").read_text(encoding="utf-8")
    body = src.split("def finalize_recording_file(", 1)[1]
    assert body.count("move_recording_sidecars(") >= 2, (
        "finalize_recording_file 的同盘与跨盘两条成功分支都必须搬运 sidecar"
    )
