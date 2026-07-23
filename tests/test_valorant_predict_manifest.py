from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "valorant_vision"
sys.path.insert(0, str(SCRIPTS))

from predict_manifest import build_parser, predict_rows


class FakeClassifier:
    def predict_batch(self, frames):
        rows = np.zeros((len(frames), 5), dtype=np.float32)
        rows[:, 2] = 1.0
        return rows


def test_predict_rows_preserves_manifest_keys() -> None:
    rows = [{
        "video_id": "pov", "timestamp_sec": 12.5,
        "source_type": "pov", "label": "combat",
    }]
    predicted = predict_rows(rows, FakeClassifier(), load_frame=lambda _row: np.zeros((8, 8, 3), np.uint8))
    assert predicted == [{
        "video_id": "pov", "timestamp_sec": 12.5,
        "source_type": "pov", "predicted_label": "combat",
    }]


def test_predict_parser_accepts_test_split() -> None:
    args = build_parser().parse_args([
        "--manifest", "manifest.jsonl", "--data-dir", "dataset",
        "--model-dir", "model", "--split", "test", "--output", "predictions.jsonl",
    ])
    assert args.split == "test"
