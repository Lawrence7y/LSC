"""持续分析 pending 切片 upsert + 状态/kick 决策纯函数测试。"""
from __future__ import annotations

from handlers import room_handler as rh


def test_first_list_should_broadcast() -> None:
    listed: set[str] = set()
    exported: set[str] = set()
    refined: set[str] = set()
    bounds: dict = {}
    action = rh._should_broadcast_clip_list_update(
        "room1:round-000001",
        "round-000001",
        10.0,
        40.0,
        "pending",
        listed_ids=listed,
        exported_ids=exported,
        refined_keys=refined,
        listed_bounds=bounds,
    )
    assert action == "first"


def test_upsert_when_bounds_move_over_threshold() -> None:
    listed = {"room1:round-000001"}
    bounds = {"room1:round-000001": (10.0, 40.0, "pending")}
    action = rh._should_broadcast_clip_list_update(
        "room1:round-000001",
        "round-000001",
        10.5,
        45.0,
        "pending",
        listed_ids=listed,
        exported_ids=set(),
        refined_keys=set(),
        listed_bounds=bounds,
    )
    assert action == "upsert"


def test_skip_when_bounds_jitter_under_threshold() -> None:
    listed = {"room1:round-000001"}
    bounds = {"room1:round-000001": (10.0, 40.0, "pending")}
    action = rh._should_broadcast_clip_list_update(
        "room1:round-000001",
        "round-000001",
        10.1,
        40.2,
        "pending",
        listed_ids=listed,
        exported_ids=set(),
        refined_keys=set(),
        listed_bounds=bounds,
    )
    assert action == "skip"


def test_upsert_when_confirm_status_changes() -> None:
    listed = {"room1:round-000001"}
    bounds = {"room1:round-000001": (10.0, 40.0, "pending")}
    action = rh._should_broadcast_clip_list_update(
        "room1:round-000001",
        "round-000001",
        10.0,
        40.0,
        "ocr_confirmed",
        listed_ids=listed,
        exported_ids=set(),
        refined_keys=set(),
        listed_bounds=bounds,
    )
    assert action == "upsert"


def test_skip_when_refined_or_exported() -> None:
    listed = {"room1:round-000001"}
    bounds = {"room1:round-000001": (10.0, 40.0, "pending")}
    assert rh._should_broadcast_clip_list_update(
        "room1:round-000001",
        "round-000001",
        20.0,
        50.0,
        "pending",
        listed_ids=listed,
        exported_ids=set(),
        refined_keys={"round-000001"},
        listed_bounds=bounds,
    ) == "skip"
    assert rh._should_broadcast_clip_list_update(
        "room1:round-000001",
        "round-000001",
        20.0,
        50.0,
        "pending",
        listed_ids=listed,
        exported_ids={"room1:round-000001"},
        refined_keys=set(),
        listed_bounds=bounds,
    ) == "skip"


def test_skip_scan_kick_requires_ocr_match() -> None:
    state = {
        "scan_range": (0.0, 80.0),
        "scan_phase": "incremental",
        "refine_with_ocr": False,
    }
    assert rh._should_skip_continuous_scan_kick(
        state, (0.0, 80.0), full_rescan=False, use_ocr=False, finalize=False,
    ) is True
    assert rh._should_skip_continuous_scan_kick(
        state, (0.0, 80.0), full_rescan=False, use_ocr=True, finalize=False,
    ) is False


def test_build_continuous_status_confirmed_rounds_default_zero() -> None:
    payload = rh._build_continuous_status_payload(
        {
            "target_room_ids": ["r1"],
            "mode": "valorant_round",
            "last_analyzed": 12.0,
            "recorded_duration": 30.0,
            "highlights": [{"start": 1, "end": 2}, {"start": 3, "end": 4}],
            "analysis_stage": "分析中",
            "refine_with_ocr": True,
            "round_phase": "combat",
            "valorant_profile": "broadcast",
            "scan_running": False,
        },
        room_id="r1",
    )
    assert payload["running"] is True
    assert payload["confirmed_rounds"] == 0
    assert payload["total_highlights"] == 2
    assert payload["refine_with_ocr"] is True
    assert payload["round_phase"] == "combat"
    assert payload["valorant_profile"] == "broadcast"
