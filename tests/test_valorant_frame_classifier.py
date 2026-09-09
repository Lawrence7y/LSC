from __future__ import annotations

import pytest

from lsc.analyzer.valorant_frame_classifier import ModelContractError, ValorantFrameClassifier


def test_optional_class_stable_prob_is_read_from_metadata(tmp_path) -> None:
    metadata = {
        "model_version": "test",
        "class_names": ["non_game", "buy", "combat", "result", "replay"],
        "input_size": [224, 224],
        "color_order": "RGB",
        "normalize_mean": [0.485, 0.456, 0.406],
        "normalize_std": [0.229, 0.224, 0.225],
        "threshold_version": "v1",
        "sha256": "0" * 64,
        "dataset_version": "test",
        "thresholds": {"stable_prob": 0.55, "high_prob": 0.8},
        "class_stable_prob": {"replay": 0.77},
    }
    model = ValorantFrameClassifier(tmp_path)
    model._meta = metadata

    assert model.class_stable_prob == {"replay": 0.77}


def test_invalid_class_stable_prob_is_rejected() -> None:
    classifier = ValorantFrameClassifier()
    with pytest.raises(ModelContractError):
        classifier._validate_meta({
            "model_version": "test",
            "class_names": ["non_game", "buy", "combat", "result", "replay"],
            "input_size": [224, 224],
            "color_order": "RGB",
            "normalize_mean": [0.485, 0.456, 0.406],
            "normalize_std": [0.229, 0.224, 0.225],
            "threshold_version": "v1",
            "sha256": "0" * 64,
            "dataset_version": "test",
            "thresholds": {"stable_prob": 0.55, "high_prob": 0.8},
            "class_stable_prob": {"not_a_class": 0.77},
        })


def test_active_model_requires_passing_promotion_metadata() -> None:
    classifier = ValorantFrameClassifier()
    with pytest.raises(ModelContractError, match="promotion report"):
        classifier._validate_meta({
            "model_version": "test",
            "class_names": ["non_game", "buy", "combat", "result", "replay"],
            "input_size": [224, 224],
            "color_order": "RGB",
            "normalize_mean": [0.485, 0.456, 0.406],
            "normalize_std": [0.229, 0.224, 0.225],
            "threshold_version": "v1",
            "sha256": "0" * 64,
            "dataset_version": "test",
            "thresholds": {"stable_prob": 0.55, "high_prob": 0.8},
            "class_stable_prob": {},
            "promotion_state": "active",
            "gate_results": {"gates_passed": False},
        })
