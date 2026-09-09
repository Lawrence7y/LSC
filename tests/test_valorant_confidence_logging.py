from __future__ import annotations

import logging

from lsc.analyzer.valorant_broadcast import _log_model_confidence


class _FakeClassifier:
    model_version = "valorant_phase_v1"
    provider = "DmlExecutionProvider"


def test_model_confidence_log_contains_auditable_fields(caplog) -> None:
    probabilities = [0.1, 0.2, 0.65, 0.03, 0.02]

    with caplog.at_level(logging.INFO, logger="lsc.analyzer.valorant_broadcast"):
        _log_model_confidence(
            _FakeClassifier(),
            stage="tail",
            timestamp_sec=12.5,
            probabilities=probabilities,
            stable_label="combat",
            confidence=0.65,
            threshold=0.55,
        )

    message = next(
        record.getMessage()
        for record in caplog.records
        if record.name == "lsc.analyzer.valorant_broadcast"
    )
    assert "stage=tail" in message
    assert "ts=12.500" in message
    assert "model=valorant_phase_v1" in message
    assert "provider=DmlExecutionProvider" in message
    assert "candidate=combat" in message
    assert "label=combat" in message
    assert "confidence=0.6500" in message
    assert "threshold=0.5500" in message
    assert "non_game:0.1000" in message
    assert "combat:0.6500" in message
