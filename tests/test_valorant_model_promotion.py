from __future__ import annotations

import json
from pathlib import Path

from scripts.valorant_vision.promote_model import promote_model


def _report(*, passed: bool) -> dict:
    return {
        "gates_passed": passed,
        "evaluation_mode": "broadcast_runtime",
        "gate_failures": [] if passed else [{"check": "macro_f1", "message": "below gate"}],
        "data_summary": {
            "class_support": {
                "non_game": 1,
                "buy": 1,
                "combat": 1,
                "result": 1,
                "replay": 1,
            },
            "source_session_count": 6,
            "source_sessions_by_type": {"broadcast": 3, "pov": 3},
        },
    }


def _candidate(tmp_path: Path) -> Path:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "valorant_phase_v1.onnx").write_bytes(b"candidate")
    import hashlib

    metadata = {
        "model_version": "test",
        "class_names": ["non_game", "buy", "combat", "result", "replay"],
        "input_size": [224, 224],
        "color_order": "RGB",
        "normalize_mean": [0.485, 0.456, 0.406],
        "normalize_std": [0.229, 0.224, 0.225],
        "threshold_version": "v1",
        "sha256": hashlib.sha256(b"candidate").hexdigest(),
        "dataset_version": "test",
        "thresholds": {"stable_prob": 0.55, "high_prob": 0.8},
    }
    (candidate / "valorant_phase_v1.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    return candidate


def test_failed_promotion_does_not_touch_production(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    production = tmp_path / "production"
    production.mkdir()
    original = production / "valorant_phase_v1.onnx"
    original.write_bytes(b"original")
    report = tmp_path / "failed.json"
    report.write_text(json.dumps(_report(passed=False)), encoding="utf-8")

    result = promote_model(candidate, production, report)

    assert result["promoted"] is False
    assert original.read_bytes() == b"original"
    assert not (production / "valorant_phase_v1.json").exists()


def test_successful_promotion_records_rollback_metadata(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    production = tmp_path / "production"
    production.mkdir()
    (production / "valorant_phase_v1.onnx").write_bytes(b"original")
    report = tmp_path / "passed.json"
    payload = _report(passed=True)
    import hashlib

    payload["model_sha256"] = hashlib.sha256(b"candidate").hexdigest()
    report.write_text(json.dumps(payload), encoding="utf-8")

    result = promote_model(candidate, production, report)

    assert result["promoted"] is True
    metadata = json.loads((production / "valorant_phase_v1.json").read_text())
    assert metadata["promotion_state"] == "active"
    assert metadata["rollback_model_sha"] == hashlib.sha256(b"original").hexdigest()
