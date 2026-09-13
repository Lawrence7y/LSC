"""赛事草稿「静默丢弃」回归：065 门（2026-09-12 09:01 现场）。

夹具 `tests/fixtures/broadcast_export_case_20260912_0901/`：L3 实跑 16 分钟后导出的真实请求 +
真 sidecar。现场：请求 9 条、写入 3 条、跳过计数 6 而明细只有 5 条 —— 缺的是 `round-000065`。

三层缺陷（都在本次修掉）：
1. **identity**：`_merge_authoritative_clip` 用权威侧 `clip_id` 覆盖前端值，而权威 clip_id 是按
   边界派生的（定稿把 end 从 801.5 裁到 789.75 ⇒ `…_6515_7898`），随后 `honor_clip_ids`
   只按请求里的旧 id 比对 ⇒ 整条静默丢弃（且丢的正是刚精修好的那条）。
2. **axis alias**：`resolve_common_range` 优先读 `recording_start_sec/recording_end_sec`，
   而 reconcile 只改 `start/end` ⇒ 权威出点被前端旧值顶掉，草稿带进 12s 赛后内容。
3. **accounting**：导出器 `clip_source_usable` 过滤只累加本地计数、不对外输出 ⇒
   requested−included 的差额无法逐条对账。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from handlers import jianying_handlers
from lsc.core.models import JianyingDraftOptions
from lsc.exporter.jianying_draft import build_session_draft

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "broadcast_export_case_20260912_0901"
STEM = "2026-09-12_08-44-04_至_2026-09-12_09-00-09"
REC = FIX / f"{STEM}.mp4"
ROOM_ID = "633dbf182b254333adc3d63d39a933eb"


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _room() -> SimpleNamespace:
    return SimpleNamespace(
        room_id=ROOM_ID, streamer_name="EDG夺冠回顾",
        record_output_path=str(REC), recording_id="009aa22700d9482fbd68ed63d5ad3428",
    )


class _Manager:
    def __init__(self, room) -> None:
        self._room = room

    def list_rooms(self):
        return [self._room]

    def get_room(self, _rid):
        return self._room


class _Timeline:
    def get_active_timeline_for_room(self, _rid):
        return None

    def get_clip_snapshot(self, _cid):
        return None


@pytest.fixture()
def case(monkeypatch):
    clips = _load("clips.json")["clips"]
    expected = _load("expected.json")
    room = _room()
    # 权威快照 = 请求的前端副本 + 065 的收尾定稿态（真实 pop 时的权威状态）
    listed = {f"{ROOM_ID}:{c['round_key']}": dict(c) for c in clips}
    key065 = f"{ROOM_ID}:round-000065"
    listed[key065] = dict(
        listed[key065],
        end=789.8,
        clip_id=f"{ROOM_ID}_6515_7898",
        broadcast_audit="passed",
        confirm_status="vision_confirmed",
        end_by="broadcast_exclusion",
        end_quality="precise",
    )
    monkeypatch.setattr(jianying_handlers, "get_timeline_service", lambda: _Timeline())
    monkeypatch.setattr(jianying_handlers, "_continuous_tasks", {})
    monkeypatch.setattr(jianying_handlers, "_analysis_jobs", {})
    monkeypatch.setattr(jianying_handlers, "_authority_snapshots", {ROOM_ID: {
        "room_id": ROOM_ID, "target_room_ids": [ROOM_ID], "recording_id": "",
        "listed_clips": listed, "rejected_round_keys": {},
    }})
    yield SimpleNamespace(clips=clips, expected=expected, room=room, listed=listed)


def _collect(case, tmp_path):
    payload = {
        "room_ids": [ROOM_ID],
        "main_room_id": ROOM_ID,
        "include_pending": False,
        "clip_ids": [c.get("clip_id") for c in case.clips],
        "clips": case.clips,
        "options": {"include_recordings": True, "include_clips": True, "text_labels": True,
                    "vertical": False, "draft_name": ""},
        "allow_single_fallback": False,
    }
    skipped: list[dict] = []
    err, sources, clip_sources, options, warnings, requested = (
        jianying_handlers._collect_draft_inputs(
            _Manager(case.room), payload, skipped_details=skipped,
        )
    )
    assert err is None
    return sources, clip_sources, options, warnings, requested, skipped, tmp_path


def test_clip_id_refined_by_authority_is_not_dropped(case):
    """① 权威 clip_id 随定稿变化时，请求里的旧 id 仍必须能认领这条切片。"""
    sources, clip_sources, _o, _w, _r, skipped, _t = _collect(case, Path("."))
    labels = {c.label for c in clip_sources}
    assert "EDG夺冠回_R07" in labels, (
        f"065 被静默丢弃（kept={sorted(labels)}，skipped={[s['round_key'] for s in skipped]}）"
    )
    # 被丢弃的必须逐条留痕：不允许出现"计数有、明细无"
    assert len(skipped) == len(case.expected["skipped"])
    assert {s["round_key"] for s in skipped} == set(case.expected["skipped"])


def test_refined_end_wins_over_stale_frontend_recording_alias(case):
    """② 精修后的出点必须覆盖前端陈旧的 recording_end_sec（否则草稿带进赛后内容）。"""
    _s, clip_sources, _o, _w, _r, _k, _t = _collect(case, Path("."))
    clip065 = next(c for c in clip_sources if c.label == "EDG夺冠回_R07")
    assert clip065.common_end == pytest.approx(
        case.expected["round_000065_authoritative_end"], abs=0.1,
    ), f"065 出点应取权威 {case.expected['round_000065_authoritative_end']}，实得 {clip065.common_end}"


def _room_draft_source(video: Path) -> object:
    from lsc.exporter.jianying_draft import RoomDraftSource

    return RoomDraftSource("r1", "EDG夺冠回顾", str(video), 0.0, is_main=True)


def test_frontend_removes_rejected_clips_from_list():
    """被拒切片必须从前端列表移除，否则会再次出现在下次导出请求里。

    实测复核（2026-09-12 09:01 现场）：程序自身的导出请求 7 条里**不含**任何被拒回合
    （007/034/036），被拒集合只出现在驱动脚本构造的请求里 —— 前端这一层本来就是对的，
    这条守卫防的是回退。
    """
    src = (ROOT / "lsc-electron" / "src" / "pages" / "Workbench" / "index.tsx").read_text(
        encoding="utf-8"
    )
    anchor = "on('clip_confirm_status'"
    assert anchor in src
    window = src[src.index(anchor): src.index(anchor) + 700]
    assert "confirm_status === 'rejected'" in window, "拒绝终态必须单独处理"
    assert "filter(" in window, "拒绝终态必须从列表移除"


def test_auto_named_draft_does_not_clobber_previous(tmp_path):
    """④ 自动命名（不含显式 draft_name）在同一分钟内必须避让，不能覆盖上一份。

    现场：09:01:48 的自动草稿（4 段）被 09:01:54 的手动导出（3 段）用同名覆盖。
    """
    probe = FIX / "2026-09-12_08-44-04_至_2026-09-12_09-00-09.mp4"  # 0 字节占位
    if not probe.exists():
        pytest.skip("夹具缺失")
    # 0 字节占位在素材探测阶段就会失败，这里只关心命名/避让逻辑 → 用 monkeypatch 短路
    import lsc.exporter.jianying_draft as mod

    calls: list[str] = []

    class _FakeFolder:
        def __init__(self, root):
            self.root = Path(root)

        def has_draft(self, name):
            return (self.root / name).exists()

        def create_draft(self, name, width, height, allow_replace=False):
            (self.root / name).mkdir(parents=True, exist_ok=True)
            calls.append(name)
            raise RuntimeError("stop-after-naming")  # 只验证命名阶段

    class _FakeDraft:
        DraftFolder = _FakeFolder

    # 预置一份"同分钟的既有草稿"（等价于 09:01:48 那份），验证自动命名避让
    base_name = mod._default_draft_name("EDG夺冠回顾")
    (tmp_path / base_name).mkdir(parents=True)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(mod, "_import_draft_lib", lambda: _FakeDraft)
    try:
        result = build_session_draft(
            rooms=[_room_draft_source(probe)], clips=[],
            options=JianyingDraftOptions(include_recordings=False, include_clips=False,
                                         text_labels=False, vertical=False, draft_name=""),
            draft_root=str(tmp_path),
        )
    finally:
        monkey.undo()

    assert calls, "构建应到达命名阶段"
    assert calls[0] != base_name, "同分钟已有同名草稿时必须避让"
    assert calls[0].startswith(base_name), "避让名应保留原前缀"
    assert any("以免覆盖" in w for w in result.warnings)


class _ServerStub:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def on(self, name):
        def decorator(fn):
            self.handlers[name] = fn
            return fn
        return decorator


def _make_fake_build(placed: int, excluded: list[dict]):
    from lsc.core.models import JianyingDraftResult

    def _fake(**_kwargs):
        return JianyingDraftResult(
            success=True, draft_name="probe", draft_dir="", tracks=1, segments=placed,
            placed_clip_count=placed, excluded_clips=list(excluded), warnings=[],
        )
    return _fake


def _run_draft_handler(case, tmp_path, monkeypatch, fake_result):
    """走真实 `generate_jianying_draft` handler（导出器被换成探针）验证响应口径。"""
    import asyncio

    server = _ServerStub()
    monkeypatch.setattr(jianying_handlers, "build_session_draft", fake_result)
    jianying_handlers.register_jianying_handlers(
        server,
        bridge=SimpleNamespace(),
        manager=_Manager(case.room),
        load_settings=lambda: {"jianying_draft_dir": str(tmp_path)},
    )
    handler = server.handlers["generate_jianying_draft"]
    payload = {
        "room_ids": [ROOM_ID],
        "main_room_id": ROOM_ID,
        "include_pending": False,
        "clip_ids": [c.get("clip_id") for c in case.clips],
        "clips": case.clips,
        "options": {"include_recordings": True, "include_clips": True, "text_labels": True,
                    "vertical": False, "draft_name": ""},
        "allow_single_fallback": False,
    }
    return asyncio.run(handler(payload))


def test_every_requested_clip_is_accounted(case, tmp_path, monkeypatch):
    """③ 响应口径：requested == included + skipped 明细 + 显式残差（残差必须为 0）。"""
    # 导出器排除一条（模拟 clip_source_usable 过滤），响应必须逐条可见
    excluded = [{
        "clip_id": f"{ROOM_ID}_9999_9999", "label": "EDG夺冠回_R99", "room_id": ROOM_ID,
        "reason_code": "EXCLUDED_BY_SOURCE_FILTER", "reason": "源可用性过滤",
        "confirm_status": "pending", "broadcast_audit": "pending_lookahead",
        "end_by": "next_prep", "end_quality": None,
    }]
    response = _run_draft_handler(case, tmp_path, monkeypatch, _make_fake_build(4, excluded))

    assert response["requested_clip_count"] == case.expected["counts"]["requested"]
    assert response["included_clip_count"] == case.expected["counts"]["included"]
    assert response["skipped_clip_count"] == case.expected["counts"]["skipped"]
    # 门禁层 5 条 + 导出器 1 条 = 6 条明细，计数 5+1=6 ⇒ 残差 0
    labels = [s["label"] for s in response["skipped"]]
    assert "EDG夺冠回_R99" in labels, "导出器排除的切片必须出现在 skipped 明细里"
    assert response["skipped_unaccounted"] == 0


def test_unaccounted_residual_is_exposed(case, tmp_path, monkeypatch):
    """③（b）只有计数、没有明细时必须显式报残差（否则静默丢弃无法被发现）。"""
    response = _run_draft_handler(case, tmp_path, monkeypatch, _make_fake_build(2, []))
    assert response["included_clip_count"] == 2
    assert response["skipped_clip_count"] == case.expected["counts"]["requested"] - 2
    assert response["skipped_unaccounted"] == case.expected["counts"]["requested"] - 2 - len(response["skipped"])


def test_exporter_exclusions_carry_labels(tmp_path):
    """③（b）导出器源可用性过滤必须逐条留痕（此前只有一个本地计数）。"""
    from lsc.exporter.jianying_draft import ClipDraftSource

    clip = ClipDraftSource(
        clip_id="c1", common_start=0.0, common_end=10.0, label="R-pending",
        precision="exact", confirm_status="pending", room_id="r1",
        source_profile="broadcast", broadcast_audit="pending_lookahead",
    )
    result = build_session_draft(
        # 房间必须指向存在的录制文件（否则导出器在源过滤前就以 no_rooms 早退）
        rooms=[SimpleNamespace(room_id="r1", name="房间", record_output_path=str(REC),
                               record_manifest_path="", recording_to_common_delta=0.0, is_main=True)],
        clips=[clip],
        options=JianyingDraftOptions(include_recordings=False, include_clips=True,
                                     text_labels=False, vertical=False, draft_name=""),
        draft_root=str(tmp_path / "draft"),
    )
    assert result.excluded_clips, "源可用性过滤必须留下逐条记录"
    assert result.excluded_clips[0]["label"] == "R-pending"
    assert result.excluded_clips[0]["reason_code"] == "EXCLUDED_BY_SOURCE_FILTER"
    assert any("R-pending" in w for w in result.warnings)
