from __future__ import annotations

import json
from pathlib import Path

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
    marker_dir.mkdir(exist_ok=True)
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


def _prod_dir(tmp_path):
    """模拟生产模型目录（.gitignore 覆盖，安装它不产生仓库噪音）。"""
    d = tmp_path / "prod"
    d.mkdir()
    (d / "valorant_phase_v1.onnx").write_bytes(b"prod-weights")
    (d / "valorant_phase_v1.json").write_text(
        json.dumps(
            {
                "model_version": "valorant_phase_v1",
                "class_names": ["non_game", "buy", "combat", "result", "replay"],
                "input_size": [224, 224],
                "color_order": "RGB",
                "normalize_mean": [0.485, 0.456, 0.406],
                "normalize_std": [0.229, 0.224, 0.225],
                "threshold_version": "v1",
                "sha256": "deadbeef",
                "dataset_version": "prod",
                "thresholds": {"stable_prob": 0.55, "high_prob": 0.8},
                "broadcast_input_fusion": {"full_frame_weight": 0.7, "top_hud_weight": 0.3},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return d


def test_install_in_place_adds_branch_and_backs_up_meta(tmp_path) -> None:
    """原地安装：装进生产目录、**不动权重**、原元数据有备份（回滚可还原）。"""
    from scripts.valorant_vision.compose_marker_roi_model import MARKER_ONNX, install_in_place

    prod = _prod_dir(tmp_path)
    original_meta = (prod / "valorant_phase_v1.json").read_text(encoding="utf-8")
    report = install_in_place(
        base_dir=prod, marker_model=_marker_model(tmp_path),
        weight=1.0, rois=DEFAULT_ROIS, target_class="replay",
    )

    # 权重文件一字未动（sha256 校验针对它）
    assert (prod / "valorant_phase_v1.onnx").read_bytes() == b"prod-weights"
    assert (prod / MARKER_ONNX).read_bytes() == b"marker-onnx"
    # 备份可还原
    backup = Path(report["meta_backup"])
    assert backup.is_file() and backup.read_text(encoding="utf-8") == original_meta
    # 声明已写入，且既有键保留
    meta = json.loads((prod / "valorant_phase_v1.json").read_text(encoding="utf-8"))
    assert meta["marker_roi_branch"]["class"] == "replay"
    assert meta["broadcast_input_fusion"] == {"full_frame_weight": 0.7, "top_hud_weight": 0.3}
    assert meta["sha256"] == "deadbeef"
    assert report["mode"] == "in_place"


def test_install_in_place_refuses_double_install(tmp_path) -> None:
    """重复安装会叠加声明 → 必须拒绝，并要求先回滚。"""
    from scripts.valorant_vision.compose_marker_roi_model import install_in_place

    prod = _prod_dir(tmp_path)
    install_in_place(base_dir=prod, marker_model=_marker_model(tmp_path),
                     weight=1.0, rois=DEFAULT_ROIS, target_class="replay")
    with pytest.raises(SystemExit):
        install_in_place(base_dir=prod, marker_model=_marker_model(tmp_path),
                         weight=1.0, rois=DEFAULT_ROIS, target_class="replay")


def test_installed_branch_is_loadable_by_runtime(tmp_path) -> None:
    """装完必须能被运行时解析成"已启用"——否则生产上会静默不生效。"""
    from lsc.analyzer.valorant_frame_classifier import marker_roi_config
    from scripts.valorant_vision.compose_marker_roi_model import install_in_place

    prod = _prod_dir(tmp_path)
    install_in_place(base_dir=prod, marker_model=_marker_model(tmp_path),
                     weight=1.0, rois=DEFAULT_ROIS, target_class="replay")
    meta = json.loads((prod / "valorant_phase_v1.json").read_text(encoding="utf-8"))
    config = marker_roi_config(meta)
    assert config is not None and config["class_index"] == 4
    assert set(config["rois"]) == set(DEFAULT_ROIS)


def test_cli_requires_exactly_one_target(tmp_path) -> None:
    from scripts.valorant_vision.compose_marker_roi_model import main

    prod = _prod_dir(tmp_path)
    marker = _marker_model(tmp_path)
    with pytest.raises(SystemExit):
        main(["--base-dir", str(prod), "--marker-model", str(marker)])
    with pytest.raises(SystemExit):
        main(["--base-dir", str(prod), "--marker-model", str(marker),
              "--in-place", "--out-dir", str(tmp_path / "o")])
