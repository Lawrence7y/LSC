from __future__ import annotations

import json

from scripts.valorant_vision.train_weighted_distill import (
    Sample,
    apply_hard_sample_weights,
)


def test_hard_sample_weights_reduce_distillation_and_raise_supervision(tmp_path) -> None:
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"frame")
    manifest = tmp_path / "hard.jsonl"
    manifest.write_text(
        json.dumps({
            "frame_path": str(frame),
            "hard_weight": 2.5,
            "hard_distill_weight": 0.05,
        }) + "\n",
        encoding="utf-8",
    )
    sample = Sample(frame, 3, 1.0, 0.5, "original")

    adjusted = apply_hard_sample_weights([sample], manifest)

    assert adjusted[0].sample_weight == 2.5
    assert adjusted[0].distill_weight == 0.05
    assert adjusted[0].source == "original+hard"
