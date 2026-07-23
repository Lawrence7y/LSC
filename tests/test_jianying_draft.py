from __future__ import annotations

from lsc.core.models import JianyingDraftOptions, JianyingDraftResult


def test_draft_options_defaults():
    opt = JianyingDraftOptions()
    assert opt.include_recordings is True
    assert opt.include_clips is True
    assert opt.text_labels is True
    assert opt.vertical is False
    assert opt.draft_name == ""
    assert opt.non_main_volume_zero is False


def test_draft_result_fields():
    r = JianyingDraftResult(
        success=True,
        draft_name="LSC_test",
        draft_dir="C:/tmp/LSC_test",
        tracks=5,
        segments=3,
        warnings=["w"],
    )
    assert r.error == ""
    assert r.warnings == ["w"]
