from __future__ import annotations

import json

import pytest

from scripts.valorant_vision.compose_marker_roi_model import DEFAULT_ROIS, compose


def _fake_model_dir(tmp_path, *, with_marker_key: bool = False):
    model_dir = tmp_path / "base"
    model_dir.mkdir()
    (model_dir / "valorant_phase_v1.onnx").write_bytes(b"onnx-bytes")
    meta = {
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
    }
    if with_marker_key:
        meta["marker_roi_branch"] = {}
    (model_dir / "valorant_phase_v1.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8",
    )
    return model_dir


def _marker_model(tmp_path):
    marker_dir = tmp_path / "marker"
    marker_dir.mkdir()
    path = marker_dir / "valorant_phase_v1.onnx"
    path.write_bytes(b"marker-onnx")
    (marker_dir / "valorant_phase_v1.json").write_text(
        json.dumps({"dataset_version": "marker-roi-test"}), encoding="utf-8",
    )
    return path


def test_default_rois_match_dataset_builder() -> None:
    """ROI 定义必须与数据集生成器同一份 —— 训练/推理错位是静默错误，必须挡住。"""
    from scripts.valorant_vision.build_marker_roi_dataset import ROIS

    assert set(DEFAULT_ROIS) == set(ROIS)
    for name, box in ROIS.items():
        assert DEFAULT_ROIS[name] == [float(v) for v in box]


def test_compose_writes_branch_declaration_and_keeps_base_onnx(tmp_path) -> None:
    base = _fake_model_dir(tmp_path)
    out = tmp_path / "out"
    report = compose(
        base_dir=base,
        marker_model=_marker_model(tmp_path),
        out_dir=out,
        weight=1.0,
        rois=DEFAULT_ROIS,
        target_class="replay",
    )
    assert report["marker_meta_copied"] is True
    assert report["marker_dataset_version"] == "marker-roi-test"
    # 基模型 onnx 原样复制（sha256 校验针对它，不能动）
    assert (out / "valorant_phase_v1.onnx").read_bytes() == b"onnx-bytes"
    assert (out / "valorant_marker_v1.onnx").read_bytes() == b"marker-onnx"
    meta = json.loads((out / "valorant_phase_v1.json").read_text(encoding="utf-8"))
    branch = meta["marker_roi_branch"]
    assert branch["model_path"] == "valorant_marker_v1.onnx"
    assert branch["class"] == "replay"
    assert branch["weight"] == 1.0
    assert set(branch["rois"]) == set(DEFAULT_ROIS)
    assert meta["sha256"] == "0" * 64  # 其余元数据不变


def test_composed_metadata_parses_as_enabled_branch(tmp_path) -> None:
    """装配产物必须能被运行时解析成"已启用"（否则支路静默失效）。"""
    from lsc.analyzer.valorant_frame_classifier import marker_roi_config

    out = tmp_path / "out"
    compose(
        base_dir=_fake_model_dir(tmp_path),
        marker_model=_marker_model(tmp_path),
        out_dir=out,
        weight=0.7,
        rois=DEFAULT_ROIS,
        target_class="replay",
    )
    meta = json.loads((out / "valorant_phase_v1.json").read_text(encoding="utf-8"))
    config = marker_roi_config(meta)
    assert config is not None
    assert config["weight"] == 0.7
    assert config["class_index"] == 4


def test_compose_refuses_base_that_already_has_branch(tmp_path) -> None:
    """幂等性守卫：重复装配会叠加两份声明，必须拒绝而不是静默覆盖。"""
    base = _fake_model_dir(tmp_path, with_marker_key=True)
    with pytest.raises(SystemExit):
        compose(
            base_dir=base,
            marker_model=_marker_model(tmp_path),
            out_dir=tmp_path / "out",
            weight=1.0,
            rois=DEFAULT_ROIS,
            target_class="replay",
        )


def test_compose_rejects_missing_inputs(tmp_path) -> None:
    base = _fake_model_dir(tmp_path)
    with pytest.raises(SystemExit):
        compose(
            base_dir=base,
            marker_model=tmp_path / "nope.onnx",
            out_dir=tmp_path / "out",
            weight=1.0,
            rois=DEFAULT_ROIS,
            target_class="replay",
        )
