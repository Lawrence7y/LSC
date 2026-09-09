from __future__ import annotations

from lsc.analyzer.valorant_broadcast import (
    _predict_broadcast_batch,
    _stable_visual_label,
    _stabilize_broadcast_samples,
)


def test_class_threshold_can_conservatively_gate_replay() -> None:
    label, confidence = _stable_visual_label(
        [0.08, 0.04, 0.12, 0.03, 0.73],
        stable_prob=0.55,
        class_stable_prob={"replay": 0.77},
    )

    assert label == "unknown"
    assert confidence == 0.73


def test_class_threshold_falls_back_to_global_threshold() -> None:
    label, confidence = _stable_visual_label(
        [0.08, 0.04, 0.73, 0.03, 0.12],
        stable_prob=0.55,
        class_stable_prob={"replay": 0.77},
    )

    assert label == "combat"
    assert confidence == 0.73


def test_broadcast_predictor_uses_fusion_when_available() -> None:
    class FakeClassifier:
        def predict_broadcast_batch(self, frames):
            return ("fused", len(frames))

        def predict_batch(self, frames):
            return ("full", len(frames))

    assert _predict_broadcast_batch(FakeClassifier(), [object()])[0] == "fused"


def test_broadcast_predictor_falls_back_for_legacy_classifier() -> None:
    class FakeClassifier:
        def predict_batch(self, frames):
            return ("full", len(frames))

    assert _predict_broadcast_batch(FakeClassifier(), [object()])[0] == "full"


def test_temporal_stabilizer_repairs_only_uncertain_middle_sample() -> None:
    samples = [
        (0.0, "combat", 0.95),
        (4.0, "unknown", 0.42),
        (8.0, "combat", 0.95),
    ]

    assert _stabilize_broadcast_samples(samples) == [
        (0.0, "combat", 0.95),
        (4.0, "combat", 0.42),
        (8.0, "combat", 0.95),
    ]


def test_temporal_stabilizer_does_not_overwrite_confident_middle_class() -> None:
    samples = [
        (0.0, "result", 0.95),
        (4.0, "combat", 0.90),
        (8.0, "result", 0.95),
    ]

    assert _stabilize_broadcast_samples(samples) == samples
