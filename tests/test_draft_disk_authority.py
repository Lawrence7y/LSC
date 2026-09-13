"""草稿「磁盘权威」回归测试：应用重启后仍能建出切片（2026-09-12 现场）。

现场：重启后内存注册表全空（``_continuous_tasks`` / ``_analysis_jobs`` /
``_authority_snapshots``），前端切片列表只由 ``clip_queued`` 驱动、store 明确不消费
``listed_clips`` ⇒ 请求里 clips 为空、权威也无处可查 ⇒ 草稿只能建出"整段录像、零切片"。

修复（``jianying_handlers``）：
1. ``_resolve_room_recording_path``：内存路径为空/失效时从磁盘找回房间最近录像
   （否则草稿直接 ``no_rooms``）；
2. ``_sidecar_authoritative_clips`` + ``fill_authoritative`` 的磁盘分支：内存权威
   完全为空时，按房间读录像旁 ``{stem}.finalization.json`` 的已定稿候选并补入，
   走与内存权威**同一套**门禁与 epoch 对账。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "python-backend"
for extra in (ROOT, BACKEND):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import handlers.jianying_handlers as jh  # noqa: E402

ROOM_ID = "6d20d207f14e458ca482db1338fb03f6"
ROOM_NAME = "EDG夺冠回顾"
STEM = "2026-09-12_15-16-24_至_2026-09-12_15-49-03"


def _candidate(round_key: str, start: float, end: float, **over) -> dict:
    base = {
        "round_key": round_key, "start": start, "end": end,
        "boundary_source": "valorant_ocr_v1", "source_profile": "broadcast",
        "start_by": "ocr_combat", "end_by": "broadcast_exclusion",
        "confirm_status": "vision_confirmed", "broadcast_audit": "passed",
        "broadcast_review_required": True,
        "start_quality": "coarse", "end_quality": "precise",
        "start_review_required": True, "end_review_required": False,
        "end_confidence": 0.92, "end_delta": 2.5,
    }
    base.update(over)
    return base


class _Room:
    def __init__(self, path: str = "", *, name: str = ROOM_NAME, recording: bool = False) -> None:
        self.room_id = ROOM_ID
        self.name = name
        self.record_output_path = path
        self.is_recording = recording
        self.output_bundle_dir = ""
        self.reconnect_output_dir = ""


class _Manager:
    def __init__(self, room: _Room) -> None:
        self._room = room

    def get_room(self, rid: str):
        return self._room if rid == ROOM_ID else None

    def list_rooms(self):
        return [self._room]


class _Timeline:
    def get_active_timeline_for_room(self, _rid):
        return None

    def get_clip_snapshot(self, _cid):
        return None


@pytest.fixture()
def recording_dir(tmp_path: Path) -> Path:
    """`<root>/<房间名>/录像 + sidecar` 布局（与 settings.output_dir 约定一致）。"""
    root = tmp_path / "output"
    room_dir = root / ROOM_NAME
    room_dir.mkdir(parents=True)
    (room_dir / f"{STEM}.mp4").write_bytes(b"0" * 16)
    finalization = {
        "schema_version": 3, "room_id": ROOM_ID, "phase": "completed",
        "accepted_candidates": [
            _candidate("round-000006", 61.42, 134.11),
            _candidate("round-000017", 167.13, 273.08),
            # 未定稿（manual_review）：必须补入但被门禁拦住，并留原因
            _candidate("round-000071-s1", 855.8, 945.1, end_by="next_combat",
                       broadcast_audit="pending_lookahead", confirm_status="pending",
                       end_quality=None, end_review_required=True),
        ],
        "rejected_candidates": [_candidate("round-000099", 1.0, 2.0, broadcast_audit="rejected_x")],
    }
    (room_dir / f"{STEM}.finalization.json").write_text(
        json.dumps(finalization, ensure_ascii=False), encoding="utf-8",
    )
    return room_dir


def _payload() -> dict:
    return {
        "room_ids": [ROOM_ID], "main_room_id": ROOM_ID, "include_pending": False,
        "clips": [], "clip_ids": [],
        "options": {"include_recordings": True, "include_clips": True,
                    "text_labels": True, "vertical": False, "draft_name": ""},
        "allow_single_fallback": True,
    }


def test_resolve_room_recording_path_from_disk(recording_dir: Path) -> None:
    """内存路径为空时必须从磁盘找回录像（否则草稿 no_rooms）。"""
    room = _Room("")
    found = jh._resolve_room_recording_path(room, {"output_dir": str(recording_dir.parent)})
    assert found and Path(found).is_file()
    assert Path(found).name == f"{STEM}.mp4"
    assert room.record_output_path == found  # 房间字段被刷新（守卫/草稿都读它）


def test_resolve_room_recording_path_keeps_existing(recording_dir: Path) -> None:
    """已有可用路径时不得改写（幂等，避免把房间指到别的录像）。"""
    existing = str(recording_dir / f"{STEM}.mp4")
    room = _Room(existing)
    assert jh._resolve_room_recording_path(room, None) == existing
    assert room.record_output_path == existing


def test_sidecar_authoritative_clips_reads_accepted_only(recording_dir: Path) -> None:
    """只读 accepted_candidates；rejected 永不入稿（与内存权威口径一致）。"""
    room = _Room(str(recording_dir / f"{STEM}.mp4"))
    items = jh._sidecar_authoritative_clips(ROOM_ID, room)
    keys = {i["round_key"] for i in items}
    assert keys == {"round-000006", "round-000017", "round-000071-s1"}
    assert all(i.get("room_id") == ROOM_ID for i in items)


def test_draft_collects_clips_from_sidecar_after_restart(recording_dir, monkeypatch) -> None:
    """重启场景端到端：请求无切片 + 内存权威为空 ⇒ 从 sidecar 补出可入稿切片。"""
    monkeypatch.setattr(jh, "get_timeline_service", lambda: _Timeline())
    monkeypatch.setattr(jh, "_continuous_tasks", {})
    monkeypatch.setattr(jh, "_analysis_jobs", {})
    monkeypatch.setattr(jh, "_authority_snapshots", {})
    room = _Room("", name=ROOM_NAME)
    skipped: list[dict] = []
    err, sources, clip_sources, options, warnings, requested = jh._collect_draft_inputs(
        _Manager(room), _payload(), skipped_details=skipped,
        load_settings=lambda: {"output_dir": str(recording_dir.parent)},
    )
    assert err is None
    spans = sorted((c.common_start, c.common_end) for c in clip_sources)
    assert spans == [(61.42, 134.11), (167.13, 273.08)], (
        f"sidecar 权威未补入（kept={spans}, skipped={[s['round_key'] for s in skipped]}）"
    )
    assert all(c.broadcast_audit == "passed" and c.end_quality == "precise"
               for c in clip_sources)
    # 房间源必须带上录像（否则导出器 no_rooms）
    assert sources and Path(sources[0].record_output_path).is_file()
    # 未定稿的那条被门禁拦住且留痕（不允许静默丢）
    assert [s["round_key"] for s in skipped] == ["round-000071-s1"]
    # 导出侧稳定原因码（与应用内展示一致）：pending_lookahead → NEVER_AUDITED
    assert skipped[0]["reason_code"] == "NEVER_AUDITED"
    assert any("收尾 sidecar 补入" in w or "权威补入" in w for w in warnings)


def test_draft_does_not_read_sidecar_when_memory_authority_alive(recording_dir, monkeypatch) -> None:
    """正常实时会话（内存权威在场）不读盘：避免旧 sidecar 残留候选混入。"""
    monkeypatch.setattr(jh, "get_timeline_service", lambda: _Timeline())
    monkeypatch.setattr(jh, "_continuous_tasks", {})
    monkeypatch.setattr(jh, "_analysis_jobs", {})
    monkeypatch.setattr(jh, "_authority_snapshots", {ROOM_ID: {
        "room_id": ROOM_ID, "listed_clips": {
            f"{ROOM_ID}:round-000006": {
                **_candidate("round-000006", 61.42, 134.11), "room_id": ROOM_ID,
            },
        },
    }})
    room = _Room(str(recording_dir / f"{STEM}.mp4"))
    skipped: list[dict] = []
    err, _sources, clip_sources, _o, _w, _r = jh._collect_draft_inputs(
        _Manager(room), _payload(), skipped_details=skipped, load_settings=lambda: {},
    )
    assert err is None
    # 只应补内存快照里的那 1 条，不得再读盘补出 017/071-s1
    assert [(c.common_start, c.common_end) for c in clip_sources] == [(61.42, 134.11)]
    assert skipped == []
