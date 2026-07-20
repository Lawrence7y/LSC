from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from lsc.analyzer.valorant_frame_classifier import (
    ModelContractError,
    ValorantFrameClassifier,
)

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
    onnx_path = FIXTURE_DIR / "valorant_phase_v1.onnx"
    digest = hashlib.sha256(onnx_path.read_bytes()).hexdigest()
    assert meta["sha256"] == digest


def test_load_rejects_sha_mismatch(tmp_path: Path) -> None:
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    bad = tmp_path / "valorant_phase_v1.json"
    onnx = FIXTURE_DIR / "valorant_phase_v1.onnx"
    meta["sha256"] = "0" * 64
    bad.write_text(json.dumps(meta), encoding="utf-8")
    clf = ValorantFrameClassifier(model_dir=tmp_path)
    (tmp_path / "valorant_phase_v1.onnx").write_bytes(onnx.read_bytes())
    with pytest.raises(ModelContractError, match="sha256"):
        clf.load()


def test_predict_batch_returns_five_probs() -> None:
    clf = ValorantFrameClassifier(model_dir=FIXTURE_DIR)
    clf.load()
    frames = [np.zeros((240, 320, 3), dtype=np.uint8) for _ in range(2)]
    out = clf.predict_batch(frames)
    assert out.shape == (2, 5)
    assert np.allclose(out.sum(axis=1), 1.0, atol=1e-3)


def test_missing_model_raises_diagnostic_error(tmp_path: Path) -> None:
    clf = ValorantFrameClassifier(model_dir=tmp_path)
    with pytest.raises(ModelContractError, match="missing"):
        clf.load()


def test_predict_batch_rejects_logits_masquerading_as_probabilities() -> None:
    class _Input:
        name = "input"

    class _LogitSession:
        def get_inputs(self):
            return [_Input()]

        def run(self, *_args, **_kwargs):
            return [np.asarray([[3.0, 1.0, -1.0, 0.5, 0.0]], dtype=np.float32)]

    clf = ValorantFrameClassifier(model_dir=FIXTURE_DIR)
    clf._meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    clf._session = _LogitSession()

    with pytest.raises(ModelContractError, match="probabilities"):
        clf.predict_batch([np.zeros((90, 160, 3), dtype=np.uint8)])


def test_runtime_preprocess_matches_training_square_resize() -> None:
    clf = ValorantFrameClassifier(model_dir=FIXTURE_DIR)
    clf._meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    frame = np.full((90, 160, 3), 255, dtype=np.uint8)

    tensor = clf._preprocess(frame)

    assert tensor.shape == (3, 224, 224)
    assert np.all(tensor[:, 0, :] > 1.0), "运行时不得添加训练阶段不存在的黑色 letterbox"


def test_training_export_writes_probabilities_and_uses_int8_as_runtime_model() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts/valorant_vision/train_export.py"
    ).read_text(encoding="utf-8")

    assert "nn.Softmax(dim=1)" in source
    assert "int8_path.replace(onnx_path)" in source
    assert "运行时默认仍使用 FP32" not in source


def test_metadata_rejects_invalid_normalization_and_thresholds() -> None:
    clf = ValorantFrameClassifier(model_dir=FIXTURE_DIR)
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    meta["normalize_mean"] = [0.5, 0.5]
    with pytest.raises(ModelContractError, match="normalize"):
        clf._validate_meta(meta)

    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    meta["thresholds"] = {"stable_prob": 0.9, "high_prob": 0.8}
    with pytest.raises(ModelContractError, match="threshold"):
        clf._validate_meta(meta)
