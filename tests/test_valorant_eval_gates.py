from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "valorant_vision"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import eval_blind  # noqa: E402
from eval_gates import (  # noqa: E402
    GATE_MACRO_F1_MIN,
    build_classification_report,
    check_all_gates,
    check_classification_gates,
    check_round_gates,
    compute_round_report,
    metrics_from_confusion,
)


def _passing_classification_dict() -> dict:
    per_class = {
        name: {
            "precision": 0.98,
            "recall": 0.96,
            "f1": 0.97,
            "support": 100,
        }
        for name in ("non_game", "buy", "combat", "result", "replay")
    }
    per_class["buy"]["precision"] = 0.98
    per_class["result"]["precision"] = 0.98
    per_class["replay"]["recall"] = 0.96
    per_class["non_game"]["recall"] = 0.96
    return {
        "macro_f1": 0.95,
        "per_class": per_class,
        "by_source_type": {
            "broadcast": {"macro_f1": 0.95, "total": 50},
            "pov": {"macro_f1": 0.95, "total": 50},
        },
    }


def _passing_rounds_dict() -> dict:
    return {
        "recall": 0.92,
        "listed_precision": 0.98,
        "vision_confirmed_err_p95": 0.5,
        "vision_confirmed_err_max": 1.2,
        "ground_truth_count": 100,
        "listed_count": 90,
        "vision_confirmed_count": 80,
        "non_game_listed_count": 0,
        "forced_close_150": 0,
        "forced_close_180": 0,
        "duplicate_keys": 0,
    }


def test_metrics_from_confusion_perfect() -> None:
    labels = ("non_game", "buy", "combat", "result", "replay")
    confusion = {g: {p: (1 if g == p else 0) for p in labels} for g in labels}
    per_class, macro_f1 = metrics_from_confusion(confusion)
    assert macro_f1 == pytest.approx(1.0)
    assert per_class["buy"].precision == pytest.approx(1.0)


def test_build_classification_report_with_source_split() -> None:
    y_true = ["buy", "combat", "buy", "replay"]
    y_pred = ["buy", "combat", "combat", "replay"]
    sources = ["broadcast", "broadcast", "pov", "pov"]
    report = build_classification_report(y_true, y_pred, sources)
    assert report.total == 4
    assert "broadcast" in report.by_source_type
    assert "pov" in report.by_source_type


def test_classification_gates_pass() -> None:
    assert check_classification_gates(_passing_classification_dict()) == []


def test_classification_gates_fail_macro_f1() -> None:
    data = _passing_classification_dict()
    data["macro_f1"] = GATE_MACRO_F1_MIN - 0.05
    failures = check_classification_gates(data)
    assert any(f.check == "macro_f1" for f in failures)


def test_classification_gates_fail_buy_precision() -> None:
    data = _passing_classification_dict()
    data["per_class"]["buy"]["precision"] = 0.90
    failures = check_classification_gates(data)
    assert any(f.check == "buy_precision" for f in failures)


def test_round_gates_pass() -> None:
    assert check_round_gates(_passing_rounds_dict()) == []


def test_round_gates_fail_forced_close() -> None:
    data = _passing_rounds_dict()
    data["forced_close_150"] = 2
    failures = check_round_gates(data)
    assert any(f.check == "forced_close_150" for f in failures)


def test_round_gates_fail_err_max() -> None:
    data = _passing_rounds_dict()
    data["vision_confirmed_err_max"] = 2.5
    failures = check_round_gates(data)
    assert any(f.check == "vision_confirmed_err_max" for f in failures)


def test_compute_round_report_from_json() -> None:
    payload = {
        "ground_truth": [
            {"start_sec": 10.0, "end_sec": 100.0},
            {"start_sec": 200.0, "end_sec": 280.0},
        ],
        "predictions": [
            {
                "start_sec": 10.2,
                "end_sec": 99.8,
                "listed": True,
                "confirm_status": "vision_confirmed",
                "round_key": "r1",
            },
            {
                "start_sec": 200.1,
                "end_sec": 279.5,
                "listed": True,
                "confirm_status": "vision_confirmed",
                "round_key": "r2",
            },
        ],
    }
    report = compute_round_report(payload)
    assert report.recall == pytest.approx(1.0)
    assert report.listed_precision == pytest.approx(1.0)
    assert report.vision_confirmed_err_max <= 0.5


def test_check_all_gates_combined_fail() -> None:
    cls = _passing_classification_dict()
    rnd = _passing_rounds_dict()
    rnd["duplicate_keys"] = 1
    failures = check_all_gates(cls, rnd)
    assert len(failures) == 1
    assert failures[0].check == "duplicate_keys"


def test_synthetic_report_roundtrip() -> None:
    """夹具级合成报告可序列化并通过全部门。"""
    merged = {
        "classification": _passing_classification_dict(),
        "rounds": _passing_rounds_dict(),
    }
    blob = json.dumps(merged)
    loaded = json.loads(blob)
    assert check_all_gates(loaded["classification"], loaded["rounds"]) == []


def test_classification_gate_requires_both_source_types() -> None:
    data = _passing_classification_dict()
    data["by_source_type"].pop("pov")

    failures = check_classification_gates(data)

    assert any(f.check == "pov_missing" for f in failures)


def test_round_gate_rejects_vacuous_zero_listed_and_zero_confirmed() -> None:
    report = compute_round_report({
        "ground_truth": [{"start": 10.0, "end": 20.0}],
        "predictions": [{
            "start": 10.0,
            "end": 20.0,
            "listed": False,
            "confirm_status": "pending",
            "round_key": "r1",
        }],
    })

    failures = check_round_gates(report)

    assert any(f.check == "listed_count" for f in failures)
    assert any(f.check == "vision_confirmed_count" for f in failures)


def test_round_gate_rejects_non_game_in_list() -> None:
    data = _passing_rounds_dict()
    data.update({
        "ground_truth_count": 10,
        "listed_count": 10,
        "vision_confirmed_count": 8,
        "non_game_listed_count": 1,
    })

    failures = check_round_gates(data)

    assert any(f.check == "non_game_listed" for f in failures)


def test_all_gates_require_classification_and_round_reports() -> None:
    assert any(f.check == "classification_missing" for f in check_all_gates(None, _passing_rounds_dict()))
    assert any(f.check == "rounds_missing" for f in check_all_gates(_passing_classification_dict(), None))


def test_full_video_manifest_runs_detector_and_keeps_video_scope(tmp_path) -> None:
    videos = []
    for name in ("a", "b"):
        path = tmp_path / f"{name}.mp4"
        path.write_bytes(b"fake")
        videos.append(path)

    records = [
        {
            "video_id": "a",
            "video_path": str(videos[0]),
            "session_id": "sa",
            "ground_truth": [{"start": 10.0, "end": 20.0}],
        },
        {
            "video_id": "b",
            "video_path": str(videos[1]),
            "session_id": "sb",
            "ground_truth": [{"start": 100.0, "end": 110.0}],
        },
    ]

    def crossed_detector(video_path: str, **_kwargs):
        if video_path.endswith("a.mp4"):
            return [{
                "start": 100.0, "end": 110.0,
                "confirm_status": "vision_confirmed",
                "boundary_source": "valorant_hybrid_v1",
                "round_key": "a1",
            }]
        return [{
            "start": 10.0, "end": 20.0,
            "confirm_status": "vision_confirmed",
            "boundary_source": "valorant_hybrid_v1",
            "round_key": "b1",
        }]

    report = eval_blind.build_rounds_from_videos(
        records,
        detector=crossed_detector,
        model_dir=tmp_path,
    )

    assert report["ground_truth_count"] == 2
    assert report["listed_count"] == 2
    assert report["recall"] == 0.0


def test_eval_blind_parser_accepts_round_manifest_and_model_dir() -> None:
    args = eval_blind.build_parser().parse_args([
        "--report", "classification.json",
        "--round-manifest", "rounds.json",
        "--model-dir", "models",
    ])

    assert args.round_manifest == Path("rounds.json")
    assert args.model_dir == Path("models")
