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


# ── B5（2026-09-10）：replay 类不参与顶部 HUD 融合 ─────────────────────────


def _fusion_model(monkeypatch, full_row, top_row):
    """构造一个 predict_batch 被 stub 的广播档分类器：整帧与顶部裁剪返回不同概率。"""
    import numpy as np

    model = ValorantFrameClassifier()
    model._meta = {
        "model_version": "test",
        "class_names": ["non_game", "buy", "combat", "result", "replay"],
        "input_size": [224, 224],
        "color_order": "RGB",
        "normalize_mean": [0.485, 0.456, 0.406],
        "normalize_std": [0.229, 0.224, 0.225],
        "thresholds": {"stable_prob": 0.55, "high_prob": 0.8},
        "class_stable_prob": {"replay": 0.77},
        "broadcast_input_fusion": {"full_frame_weight": 0.7, "top_hud_weight": 0.3},
    }
    monkeypatch.setattr(model, "load", lambda: None)
    monkeypatch.setattr(model, "_record_inference", lambda *a, **k: None)

    def fake_predict_batch(frames, _record_telemetry=True):
        # 顶部裁剪高度为原帧的 34%，据此区分两路输入
        row = top_row if frames[0].shape[0] < 100 else full_row
        return np.tile(np.asarray(row, dtype=np.float32), (len(frames), 1))

    monkeypatch.setattr(model, "predict_batch", fake_predict_batch)
    return model


def test_broadcast_fusion_keeps_replay_at_full_frame_value(monkeypatch):
    """replay 必须取整帧值（顶部 HUD 看不到底部水印，融合会把它压低）。"""
    import numpy as np

    # 整帧高置信度回放 0.96，但顶部裁剪几乎认不出（0.05）
    model = _fusion_model(monkeypatch,
                          full_row=[0.01, 0.01, 0.01, 0.01, 0.96],
                          top_row=[0.50, 0.20, 0.20, 0.05, 0.05])
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    out = model.predict_broadcast_batch([frame])
    replay = float(out[0][4])
    # 旧融合会给 0.7*0.96 + 0.3*0.05 = 0.687（低于 0.77 → 漏检）
    assert replay > 0.9, f"replay 应保持整帧值，实际 {replay}"
    # 概率行契约仍须成立
    assert abs(float(out[0].sum()) - 1.0) < 1e-3


def test_broadcast_fusion_bypass_list_is_replay_only() -> None:
    """豁免名单限定为 replay（避免误伤 HUD 驱动的类别）。"""
    from lsc.analyzer.valorant_frame_classifier import _FUSION_BYPASS_CLASSES

    assert _FUSION_BYPASS_CLASSES == ("replay",)


def test_broadcast_fusion_uses_conditional_renormalization() -> None:
    """源码守卫：不得退回"整行除以总和"（会把刚抬起的 replay 又压回阈值下）。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "lsc/analyzer/valorant_frame_classifier.py").read_text(encoding="utf-8")
    body = src.split("if bypass:", 1)[1].split("return fused", 1)[0]
    assert "bypass_total" in body and "scale" in body, "应为条件归一化（缩其余类）"
    assert "row_total" in body  # 极端情形兜底
