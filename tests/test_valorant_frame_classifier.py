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


# ── 回放标记 ROI 支路（2026-09-10）──────────────────────────────────────────
#
# 背景：整帧压到 224×224 后标记只剩 ≈20×8 px、仅占整帧 8% 的边缘能量，
# 模型只能靠画面内容判回放（纠正标签后重训会把 combat 召回从 0.9490 打到 0.7937）。
# 该支路把标记区按原生分辨率放大成一路独立输入，只**加强**回放证据。


def _marker_meta(**branch) -> dict:
    meta = {
        "model_version": "test",
        "class_names": ["non_game", "buy", "combat", "result", "replay"],
        "input_size": [224, 224],
        "color_order": "RGB",
        "normalize_mean": [0.485, 0.456, 0.406],
        "normalize_std": [0.229, 0.224, 0.225],
        "thresholds": {"stable_prob": 0.55, "high_prob": 0.8},
        "broadcast_input_fusion": {"full_frame_weight": 0.7, "top_hud_weight": 0.3},
    }
    if branch:
        meta["marker_roi_branch"] = branch
    return meta


def _valid_branch(**overrides) -> dict:
    branch = {
        "model_path": "valorant_marker_v1.onnx",
        "weight": 1.0,
        "rois": {
            "top_right": [0.80, 0.00, 0.20, 0.10],
            "top_left_small": [0.24, 0.00, 0.14, 0.05],
            "bottom_right": [0.78, 0.85, 0.22, 0.15],
        },
        "class": "replay",
    }
    branch.update(overrides)
    return branch


def test_marker_roi_config_accepts_valid_declaration() -> None:
    from lsc.analyzer.valorant_frame_classifier import marker_roi_config

    config = marker_roi_config(_marker_meta(**_valid_branch()))
    assert config is not None
    assert config["class"] == "replay"
    assert config["class_index"] == 4
    assert set(config["rois"]) == {"top_right", "top_left_small", "bottom_right"}
    assert config["rois"]["top_right"] == (0.80, 0.0, 0.20, 0.10)


@pytest.mark.parametrize(
    "branch",
    [
        None,                                                              # 未声明
        _valid_branch(weight=0.0),                                         # 显式关闭
        _valid_branch(weight=-1.0),                                        # 负权重
        _valid_branch(model_path=""),                                      # 无模型路径
        _valid_branch(rois={}),                                            # 无 ROI
        _valid_branch(rois={"a": [0.0, 0.0, 0.1]}),                        # 框维度不对
        _valid_branch(rois={"a": [0.9, 0.0, 0.2, 0.1]}),                   # 越过右边界
        _valid_branch(rois={"a": [0.0, 0.0, 0.0, 0.1]}),                   # 零宽
        _valid_branch(rois={"a": ["x", 0.0, 0.1, 0.1]}),                   # 非数值
        _valid_branch(class_name="not_a_class") if False else _valid_branch(**{"class": "nope"}),
        _valid_branch(weight="abc"),                                       # 权重非数值
    ],
)
def test_marker_roi_config_is_none_when_disabled_or_invalid(branch) -> None:
    """契约宁缺毋滥：任何一项不合法都退回"未启用"，绝不半开半关。"""
    from lsc.analyzer.valorant_frame_classifier import marker_roi_config

    meta = _marker_meta(**branch) if branch else {"model_version": "test"}
    assert marker_roi_config(meta) is None
    assert marker_roi_config(None) is None


def test_crop_normalized_roi_clips_to_bounds_and_rejects_degenerate() -> None:
    import numpy as np

    from lsc.analyzer.valorant_frame_classifier import crop_normalized_roi

    frame = np.arange(100 * 200 * 3, dtype=np.uint8).reshape(100, 200, 3)
    crop = crop_normalized_roi(frame, (0.5, 0.0, 0.5, 0.5))
    assert crop is not None
    assert crop.shape == (50, 100, 3)
    assert crop[0, 0].tolist() == frame[0, 100].tolist()
    # 越界 ROI 要被裁到画面内，而不是报错
    assert crop_normalized_roi(frame, (0.5, 0.5, 1.0, 1.0)).shape == (50, 100, 3)
    # 贴到只剩 1px 高的框视为退化 → 拒绝（否则预处理会拿到 1px 输入）
    assert crop_normalized_roi(frame, (0.99, 0.99, 0.5, 0.5)) is None
    # 退化输入直接拒绝
    assert crop_normalized_roi(frame, (0.0, 0.0, 0.0, 0.5)) is None
    assert crop_normalized_roi(np.zeros((1, 1, 3), dtype=np.uint8), (0.0, 0.0, 0.5, 0.5)) is None
    assert crop_normalized_roi(np.zeros((4, 4), dtype=np.uint8), (0.0, 0.0, 0.5, 0.5)) is None


def _fusion_model_with_marker(monkeypatch, *, full_row, top_row, evidence, meta=None):
    """predict_batch 与 marker_roi_evidence 都被 stub 的广播档分类器。"""
    import numpy as np

    model = ValorantFrameClassifier()
    model._meta = meta if meta is not None else _marker_meta(**_valid_branch())
    monkeypatch.setattr(model, "load", lambda: None)
    monkeypatch.setattr(model, "_record_inference", lambda *a, **k: None)

    def fake_predict_batch(frames, _record_telemetry=True):
        row = top_row if frames[0].shape[0] < 100 else full_row
        return np.tile(np.asarray(row, dtype=np.float32), (len(frames), 1))

    monkeypatch.setattr(model, "predict_batch", fake_predict_batch)
    monkeypatch.setattr(
        model, "marker_roi_evidence",
        lambda frames: None if evidence is None else np.asarray(evidence, dtype=np.float32),
    )
    return model


def test_marker_branch_raises_replay_evidence(monkeypatch) -> None:
    """标记支路只**加强**回放证据：整帧 0.55 → 标记 0.93。"""
    import numpy as np

    model = _fusion_model_with_marker(
        monkeypatch,
        full_row=[0.30, 0.05, 0.10, 0.00, 0.55],
        top_row=[0.50, 0.20, 0.20, 0.05, 0.05],
        evidence=[0.93],
    )
    out = model.predict_broadcast_batch([np.zeros((120, 160, 3), dtype=np.uint8)])
    assert float(out[0][4]) > 0.9, f"标记证据应抬到 ≥0.9，实际 {float(out[0][4])}"
    assert abs(float(out[0].sum()) - 1.0) < 1e-3


def test_marker_branch_absent_keeps_b5_behaviour(monkeypatch) -> None:
    """未声明支路时（evidence 为 None）逐字等于 B5：replay 取整帧值。"""
    import numpy as np

    model = _fusion_model_with_marker(
        monkeypatch,
        full_row=[0.01, 0.01, 0.01, 0.01, 0.96],
        top_row=[0.50, 0.20, 0.20, 0.05, 0.05],
        evidence=None,
        meta=_marker_meta(),
    )
    out = model.predict_broadcast_batch([np.zeros((120, 160, 3), dtype=np.uint8)])
    assert float(out[0][4]) > 0.9
    assert abs(float(out[0].sum()) - 1.0) < 1e-3


def test_marker_branch_does_not_lower_replay_below_full_frame(monkeypatch) -> None:
    """标记支路权重很低时也不许把整帧已有的高回放证据压下去（取 max 而非加权）。"""
    import numpy as np

    model = _fusion_model_with_marker(
        monkeypatch,
        full_row=[0.01, 0.01, 0.01, 0.01, 0.96],
        top_row=[0.50, 0.20, 0.20, 0.05, 0.05],
        evidence=[0.10],
    )
    out = model.predict_broadcast_batch([np.zeros((120, 160, 3), dtype=np.uint8)])
    assert float(out[0][4]) > 0.9


def test_marker_branch_missing_model_file_is_graceful(tmp_path, monkeypatch) -> None:
    """声明的模型文件不存在时不得抛错/中断链路，只退回"无支路"。"""
    import numpy as np

    model = ValorantFrameClassifier(tmp_path)
    model._meta = _marker_meta(**_valid_branch(model_path="missing.onnx"))
    monkeypatch.setattr(model, "load", lambda: None)
    assert model.marker_roi_evidence([np.zeros((120, 160, 3), dtype=np.uint8)]) is None
    assert model._marker_error and "missing" in model._marker_error


def test_marker_roi_evidence_upscale_matches_dataset_pipeline() -> None:
    """源码守卫：支路放大必须用 INTER_CUBIC。

    离线 ROI 训练集由 `build_marker_roi_dataset.py` 用 CUBIC 生成；推理侧若交给
    `_preprocess_batch` 的 INTER_AREA 去放大，字形会退化成最近邻 —— 训练/推理
    口径不一致，支路会失准。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    classifier_src = (root / "lsc/analyzer/valorant_frame_classifier.py").read_text(encoding="utf-8")
    builder_src = (root / "scripts/valorant_vision/build_marker_roi_dataset.py").read_text(encoding="utf-8")
    assert "INTER_CUBIC" in classifier_src.split("def marker_roi_evidence", 1)[1]
    assert "INTER_CUBIC" in builder_src


def test_marker_roi_evidence_weight_is_a_multiplier(monkeypatch) -> None:
    """``weight`` 必须真的作用于证据（不是只当开关）——否则参数名骗人。

    用一个假 session 直接验证 multiplier 语义：weight=0.5 时证据减半。
    """
    import numpy as np

    class _FakeInput:
        name = "input"

    class _FakeSession:
        def get_inputs(self):
            return [_FakeInput()]

        def run(self, _outputs, feed):
            # 每个 (帧, ROI) 裁剪都返回"replay 列 = 0.9"
            row = np.zeros((1, 5), dtype=np.float32)
            row[0, 4] = 0.9
            return [np.tile(row, (feed["input"].shape[0], 1))]

    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    for weight, expected in ((1.0, 0.9), (0.5, 0.45), (0.0, None)):
        model = ValorantFrameClassifier()
        model._meta = _marker_meta(**_valid_branch(weight=weight))
        monkeypatch.setattr(model, "load", lambda: None)
        monkeypatch.setattr(model, "_marker_session", _FakeSession())
        evidence = model.marker_roi_evidence([frame])
        if expected is None:
            assert evidence is None, "weight<=0 视为未启用"
        else:
            assert evidence is not None
            assert abs(float(evidence[0]) - expected) < 1e-6
