"""批次1止血回归：码率单位归一化 + 剪映草稿不误删用户已有草稿。"""
from __future__ import annotations

from pathlib import Path

from lsc.core.controller import HeadlessRecordingController


def test_normalize_video_bitrate_matches_orchestrator() -> None:
    n = HeadlessRecordingController._normalize_video_bitrate
    assert n("8000", "kbps") == "8000k"
    assert n("8", "Mbps") == "8M"
    assert n("2500k", "M") == "2500k"
    # 旧默认 unit="M" 不得拼出 8Gbps
    assert n("8000", "M") == "8000k"
    assert n("", "") == "8000k"
    assert n("12.5M", "kbps") == "12.5M"


def test_build_encoder_args_uses_normalized_bitrate() -> None:
    args = HeadlessRecordingController._build_encoder_args(
        "h264_nvenc", 23, "自定义码率", "8000", "kbps", "", "", ""
    )
    assert args[args.index("-b:v") + 1] == "8000k"
    crf_args = HeadlessRecordingController._build_encoder_args(
        "libx264", 23, "CRF 质量", "8000", "kbps", "", "", ""
    )
    assert "-crf" in crf_args and "-b:v" not in crf_args


def test_jianying_failed_init_does_not_delete_existing_draft(tmp_path: Path) -> None:
    """create_draft 失败且同名草稿已存在时，必须保留用户草稿（经备份恢复）。"""
    import lsc.exporter.jianying_draft as jd
    from lsc.core.models import JianyingDraftOptions
    from lsc.exporter.jianying_draft import RoomDraftSource

    draft_root = tmp_path / "drafts"
    draft_root.mkdir()
    user_draft = draft_root / "我的草稿"
    user_draft.mkdir()
    (user_draft / "draft_content.json").write_text("{}", encoding="utf-8")

    class BoomFolder:
        def __init__(self, root: str) -> None:
            self.root = root

        def has_draft(self, name: str) -> bool:
            return (Path(self.root) / name).is_dir()

        def create_draft(self, name: str, width: int, height: int, allow_replace: bool = False):
            raise RuntimeError("create_draft boom")

    class FakeDraftModule:
        DraftFolder = BoomFolder

        class TrackSpec:
            def __init__(self, *a, **k):
                pass

        class TrackType:
            video = "video"
            text = "text"

        SEC = 1_000_000

    result = None
    orig = jd._import_draft_lib
    jd._import_draft_lib = lambda: FakeDraftModule  # type: ignore[assignment]
    try:
        result = jd.build_session_draft(
            rooms=[
                RoomDraftSource(
                    room_id="r1",
                    name="主",
                    record_output_path=str(tmp_path / "a.mp4"),
                    recording_to_common_delta=0.0,
                    is_main=True,
                )
            ],
            clips=[],
            options=JianyingDraftOptions(draft_name="我的草稿"),
            draft_root=str(draft_root),
        )
    finally:
        jd._import_draft_lib = orig

    assert result is not None and result.success is False
    # 用户草稿必须还在（内容未丢）
    assert (user_draft / "draft_content.json").exists()
    assert (draft_root / "我的草稿").is_dir()
