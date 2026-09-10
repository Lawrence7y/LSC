from __future__ import annotations

from pathlib import Path

import pytest

from scripts.valorant_vision.fetch_bilibili_vods import pick_page
from scripts.valorant_vision.mine_marker_roi_from_videos import collect_videos


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


def test_collect_videos_walks_directories_recursively(tmp_path) -> None:
    _touch(tmp_path / "a" / "one.mp4")
    _touch(tmp_path / "b" / "two.MKV")
    _touch(tmp_path / "b" / "notes.txt")
    found = collect_videos([tmp_path])
    assert [p.name for p in found] == ["two.MKV", "one.mp4"] or [p.name for p in found] == ["one.mp4", "two.MKV"]
    assert all(p.suffix.lower() in (".mp4", ".mkv") for p in found)


def test_collect_videos_exclude_blocks_leakage(tmp_path) -> None:
    """**挡数据泄漏**：test 帧的来源录像绝不能进训练集。

    `test/replay` 的 32 帧就来自 12-00-36 / 02-06-12 两个录像 —— 把它们挖进 train，
    test 指标会虚高（自欺）。这条守卫必须一直在。
    """
    _touch(tmp_path / "2026-09-10_12-00-36_至_2026-09-10_12-20-01.mp4")
    _touch(tmp_path / "2026-09-10_02-06-12_至_2026-09-10_02-20-38.mp4")
    _touch(tmp_path / "2026-09-10_09-06-29_至_2026-09-10_09-21-10.mp4")

    kept = [p.name for p in collect_videos([tmp_path], ["12-00-36", "02-06-12"])]
    assert len(kept) == 1
    assert "09-06-29" in kept[0]
    # 不传 exclude 时全部保留（默认行为不变）
    assert len(collect_videos([tmp_path])) == 3


def test_collect_videos_accepts_single_file(tmp_path) -> None:
    video = tmp_path / "single.mp4"
    _touch(video)
    assert collect_videos([video]) == [video]


def test_pick_page_prefers_longest_match_segment() -> None:
    """二路 VOD 常是「p1 赛前分析 + 每图一个 p」→ 要挑实际比赛那段。"""
    data = {
        "duration": 100,
        "pages": [
            {"page": 1, "part": "赛前分析", "duration": 352},
            {"page": 2, "part": "图一 隐世修所", "duration": 2116},
            {"page": 3, "part": "图二 亚海悬城", "duration": 2751},
            {"page": 4, "part": "赛后采访", "duration": 900},
        ],
    }
    assert pick_page(data)["page"] == 3


def test_pick_page_falls_back_when_all_segments_are_skipped() -> None:
    data = {
        "duration": 100,
        "pages": [
            {"page": 1, "part": "赛前分析", "duration": 352},
            {"page": 2, "part": "赛后采访", "duration": 900},
        ],
    }
    assert pick_page(data)["page"] == 2


def test_pick_page_single_part_video() -> None:
    assert pick_page({"duration": 500, "pages": [{"page": 1, "part": "图一", "duration": 500}]})["page"] == 1
    assert pick_page({"duration": 500})["page"] == 1


def test_marker_roi_set_covers_measured_positions() -> None:
    """四种实测标记位置都必须落在某个 ROI 框内 —— 少一个就会整段素材命中 0。

    2026-09-11 实测教训：只用 右上角+右下角 时，2026 进化者杯整段（1200 个裁剪）
    命中 0，因为它的标记在"顶部居中"。
    """
    from scripts.valorant_vision.build_marker_roi_dataset import ROIS

    measured = {
        "top_right": (0.878, 0.020, 0.968, 0.056),
        "top_center": (0.46, 0.01, 0.54, 0.07),
        "top_left_small": (0.292, 0.006, 0.323, 0.020),
        "bottom_right": (0.848, 0.905, 0.942, 0.967),
    }
    for name, (x0, y0, x1, y1) in measured.items():
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        covered = any(
            bx <= cx <= bx + bw and by <= cy <= by + bh
            for bx, by, bw, bh in ROIS.values()
        )
        assert covered, f"实测位置 {name} 的中心 ({cx:.3f},{cy:.3f}) 不在任何 ROI 内"


def test_marker_rois_stay_within_frame_and_reasonably_sized() -> None:
    from scripts.valorant_vision.build_marker_roi_dataset import ROIS

    for name, (x, y, w, h) in ROIS.items():
        assert 0.0 <= x < 1.0 and 0.0 <= y < 1.0, name
        assert x + w <= 1.0 + 1e-9 and y + h <= 1.0 + 1e-9, name
        # 框太宽会把字形横向压扁到读不出（实测教训），限制一下宽高比
        assert w == pytest.approx(min(w, 0.26)), name
        assert h == pytest.approx(min(h, 0.17)), name
