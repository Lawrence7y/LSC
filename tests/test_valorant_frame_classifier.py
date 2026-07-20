from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "valorant_vision"
META_PATH = FIXTURE_DIR / "valorant_phase_v1.json"

REQUIRED_META_KEYS = {
    "model_version",
    "class_names",
    "input_size",
    "color_order",
    "normalize_mean",
    "normalize_std",
    "threshold_version",
    "sha256",
    "dataset_version",
    "thresholds",
}


def test_fixture_metadata_has_required_keys() -> None:
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    missing = REQUIRED_META_KEYS - set(meta)
    assert not missing, missing
    assert meta["class_names"] == [
        "non_game", "buy", "combat", "result", "replay",
    ]
    assert meta["input_size"] == [224, 224]
    assert meta["color_order"] == "RGB"
