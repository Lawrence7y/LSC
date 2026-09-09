from __future__ import annotations

from scripts.valorant_vision.train_weighted_distill import pseudo_sample_weight


def test_pseudo_sample_weight_keeps_all_confidence_buckets() -> None:
    assert pseudo_sample_weight(None) == 0.05
    assert pseudo_sample_weight(0.54) == 0.05
    assert pseudo_sample_weight(0.55) == 0.15
    assert pseudo_sample_weight(0.69) == 0.15
    assert pseudo_sample_weight(0.70) == 0.30
