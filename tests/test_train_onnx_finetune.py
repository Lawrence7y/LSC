from __future__ import annotations

from scripts.valorant_vision.train_onnx_finetune import _metrics


def test_metrics_returns_macro_f1_and_accuracy() -> None:
    accuracy, macro_f1, f1s = _metrics(
        [
            [2, 0, 0, 0, 0],
            [0, 1, 0, 0, 0],
            [0, 0, 1, 0, 0],
            [0, 0, 0, 1, 0],
            [0, 0, 0, 0, 1],
        ]
    )
    assert accuracy == 1.0
    assert macro_f1 == 1.0
    assert f1s == [1.0] * 5
