"""同轨重叠去重必须逐条进 excluded_clips（skipped_unaccounted 恒等 0 的前提）。

2026-09-13 09:04 真机现场：收尾补扫合成出与既有切片重叠的重复候选
（101-206.25 vs 105-206.25 等 4 条），placement 阶段被 SegmentOverlap 拦下后
只写 warnings、不进 excluded_clips ⇒ 响应 skipped_unaccounted=4，违反
「requested == included + skipped 明细（残差恒 0）」的对账契约。
内容本身无损失（草稿 6 段与审计精确出点逐一吻合、不变量审计 passed），
缺的是逐条留痕。

红线：不放宽 ``clip_source_usable`` / ``_broadcast_gate_passed`` 判据——
本修复只补"被重叠去重的切片"的留痕，不改变任何切片能否入草稿的结论。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_backend_dir = Path(__file__).resolve().parents[1] / "python-backend"
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

import lsc.exporter.jianying_draft as mod
from lsc.core.models import JianyingDraftOptions
from lsc.exporter.jianying_draft import (
    ClipDraftSource,
    RoomDraftSource,
    build_session_draft,
)


class _FakeTimerange:
    def __init__(self, start: int, duration: int) -> None:
        self.start = start
        self.duration = duration

    def overlaps(self, other: "_FakeTimerange") -> bool:
        return not (self.start + self.duration <= other.start or other.start + other.duration <= self.start)


class _FakeSegment:
    def __init__(self, material, target_timerange, source_timerange=None, volume=None, **_kw) -> None:
        self.target_timerange = target_timerange
        self.track_name: str | None = None


class _FakeScript:
    def __init__(self) -> None:
        self._tracks: dict[str, list[_FakeSegment]] = {}

    def append_tracks(self, specs) -> None:
        for spec in specs:
            self._tracks[spec.name] = []

    def add_segment(self, segment: _FakeSegment, track_name: str) -> None:
        track = self._tracks.setdefault(track_name, [])
        for existing in track:
            if existing.target_timerange.overlaps(segment.target_timerange):
                raise RuntimeError(
                    f"New segment overlaps with existing segment "
                    f"[start: {segment.target_timerange.start}, duration: {segment.target_timerange.duration}]"
                )
        segment.track_name = track_name
        track.append(segment)

    def save(self) -> None:  # pragma: no cover - 只验证记账，不落盘
        return None


@pytest.fixture()
def fake_draft_lib(monkeypatch, tmp_path):
    class _FakeDraftFolder:
        def __init__(self, root: str) -> None:
            self.root = Path(root)

        def has_draft(self, name: str) -> bool:
            return (self.root / name).exists()

        def create_draft(self, name: str, width: int, height: int, allow_replace: bool = False):
            draft_dir = self.root / name
            draft_dir.mkdir(parents=True, exist_ok=True)
            return _FakeScript()

    class _FakeVideoMaterial:
        def __init__(self, path: str, crop_settings=None) -> None:
            # duration 单位微秒：600s 素材
            self.duration = 600 * 1_000_000
            self.width = 1920
            self.height = 1080
            self.path = path

    fake = SimpleNamespace(
        DraftFolder=_FakeDraftFolder,
        VideoMaterial=_FakeVideoMaterial,
        VideoSegment=_FakeSegment,
        TextSegment=lambda label, trange: SimpleNamespace(label=label, target_timerange=trange),
        TrackSpec=lambda track_type, name: SimpleNamespace(track_type=track_type, name=name),
        TrackType=SimpleNamespace(video="video", text="text"),
        SEC=1_000_000,
    )
    monkeypatch.setattr(mod, "_import_draft_lib", lambda: fake)
    return tmp_path


def _clip(cid: str, start: float, end: float) -> ClipDraftSource:
    return ClipDraftSource(
        clip_id=f"room_{cid}",
        common_start=start,
        common_end=end,
        label=f"EDG夺冠回_{cid}",
        precision="exact",
        confirm_status="vision_confirmed",
        room_id="r1",
        source_profile="broadcast",
        broadcast_audit="passed",
        end_quality="precise",
        end_by="broadcast_exclusion",
    )


def test_overlap_dedup_is_accounted_in_excluded_clips(fake_draft_lib, tmp_path):
    """被 SegmentOverlap 去重的切片必须逐条进 excluded_clips（带 reason_code）。"""
    rec = tmp_path / "rec.mp4"
    rec.write_bytes(b"")
    room = RoomDraftSource(
        room_id="r1", name="EDG夺冠回顾", record_output_path=str(rec),
        recording_to_common_delta=0.0, is_main=True,
    )
    # 模拟收尾补扫的重复候选：c2 与 c1 在同轨重叠（105-206.25 ⊂ 100-200 的邻域）
    clips = [_clip("R05", 100.0, 200.0), _clip("R06", 105.0, 206.25)]

    result = build_session_draft(
        rooms=[room],
        clips=clips,
        options=JianyingDraftOptions(
            include_recordings=False, include_clips=True,
            text_labels=False, vertical=False, draft_name="overlap_case",
        ),
        draft_root=str(tmp_path),
    )

    assert result.success, result.error
    # 只放得下第一条；第二条必须留在 excluded_clips 里逐条可见
    assert result.placed_clip_count == 1, (
        f"placed={result.placed_clip_count}, excluded={result.excluded_clips}, "
        f"warnings={result.warnings}"
    )
    overlap_entries = [e for e in result.excluded_clips if e.get("reason_code") == "OVERLAP_DEDUP"]
    # 改前：重叠去重只写 warnings ⇒ excluded_clips 为空 ⇒ skipped_unaccounted > 0
    assert len(overlap_entries) == 1, (
        f"重叠去重未逐条留痕: excluded={result.excluded_clips}, warnings={result.warnings}"
    )
    entry = overlap_entries[0]
    assert entry.get("label") == "EDG夺冠回_R06"
    assert entry.get("start") == pytest.approx(105.0)
    assert entry.get("end") == pytest.approx(206.25)


def test_gate_rejections_still_accounted(fake_draft_lib, tmp_path):
    """守卫：修复不得影响既有留痕路径（门禁拒绝仍走原 reason_code）。"""
    rec = tmp_path / "rec2.mp4"
    rec.write_bytes(b"")
    room = RoomDraftSource(
        room_id="r1", name="EDG夺冠回顾", record_output_path=str(rec),
        recording_to_common_delta=0.0, is_main=True,
    )
    pending = ClipDraftSource(
        clip_id="room_p1", common_start=10.0, common_end=40.0, label="EDG夺冠回_P1",
        precision="exact", confirm_status="pending", room_id="r1",
        source_profile="valorant",  # 非 broadcast：pending 必被源过滤拦下
    )
    result = build_session_draft(
        rooms=[room],
        clips=[pending],
        options=JianyingDraftOptions(
            include_recordings=False, include_clips=True,
            text_labels=False, vertical=False, draft_name="gate_case",
        ),
        draft_root=str(tmp_path),
    )
    assert result.placed_clip_count == 0
    assert any(e.get("reason_code") == "EXCLUDED_BY_SOURCE_FILTER" for e in result.excluded_clips)
