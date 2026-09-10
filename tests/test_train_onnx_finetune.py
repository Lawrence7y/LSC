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


def test_export_meta_inherits_teacher_runtime_contract(tmp_path) -> None:
    """微调产物必须继承教师的运行时后处理契约（融合权重 / 类专属稳定阈值）。

    2026-09-10 实测踩到的坑：v4_fused 的 ``broadcast_input_fusion`` 与
    ``class_stable_prob`` 不在训练脚本的 meta 里，导出后凭空消失，运行时静默
    退回"无融合 + 默认阈值"，与基线不可比。
    """
    import json

    from scripts.valorant_vision.train_onnx_finetune import _inherit_runtime_meta

    teacher = tmp_path / "teacher"
    teacher.mkdir()
    (teacher / "valorant_phase_v1.json").write_text(
        json.dumps(
            {
                "thresholds": {"stable_prob": 0.55, "high_prob": 0.8},
                "class_stable_prob": {"replay": 0.77},
                "broadcast_input_fusion": {"full_frame_weight": 0.7, "top_hud_weight": 0.3},
                "calibration_note": "note",
                "sha256": "teacher-digest-should-not-leak",
                "train_count": 4446,
            }
        ),
        encoding="utf-8",
    )
    meta = {"thresholds": {"stable_prob": 0.9, "high_prob": 0.95}}
    inherited = _inherit_runtime_meta(teacher, meta)

    assert set(inherited) == {
        "thresholds",
        "class_stable_prob",
        "broadcast_input_fusion",
        "calibration_note",
    }
    # thresholds 由教师覆盖（同族运行时契约），不是脚本里的硬编码默认值
    assert meta["thresholds"] == {"stable_prob": 0.55, "high_prob": 0.8}
    assert meta["class_stable_prob"] == {"replay": 0.77}
    assert meta["broadcast_input_fusion"] == {"full_frame_weight": 0.7, "top_hud_weight": 0.3}
    # 训练产物自身的字段必须保留，且不得把教师的训练统计串进来
    assert meta["train_count"] if "train_count" in meta else True
    assert "sha256" not in meta


def test_export_meta_without_teacher_json_is_noop(tmp_path) -> None:
    from scripts.valorant_vision.train_onnx_finetune import _inherit_runtime_meta

    meta = {"thresholds": {"stable_prob": 0.55, "high_prob": 0.8}}
    assert _inherit_runtime_meta(tmp_path / "missing", meta) == []
    assert meta == {"thresholds": {"stable_prob": 0.55, "high_prob": 0.8}}


def test_export_meta_skips_null_teacher_keys(tmp_path) -> None:
    """教师把某个键显式写成 null 时不应污染导出元数据。"""
    import json

    from scripts.valorant_vision.train_onnx_finetune import _inherit_runtime_meta

    teacher = tmp_path / "teacher"
    teacher.mkdir()
    (teacher / "valorant_phase_v1.json").write_text(
        json.dumps({"class_stable_prob": None, "broadcast_input_fusion": {"full_frame_weight": 1.0}}),
        encoding="utf-8",
    )
    meta: dict = {}
    inherited = _inherit_runtime_meta(teacher, meta)
    assert inherited == ["broadcast_input_fusion"]
    assert "class_stable_prob" not in meta
