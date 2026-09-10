from __future__ import annotations

import json

import pytest

from scripts.valorant_vision.compare_models_official import _value, parse_models

_REPORT = {
    "macro_f1": 0.8935,
    "per_class": {
        "replay": {"precision": 0.5, "recall": 0.9, "f1": 0.6429},
        "combat": {"precision": 0.9, "recall": 0.7937, "f1": 0.84},
    },
}


def test_value_reads_report_level_metric() -> None:
    assert _value(_REPORT, ("", "macro_f1")) == 0.8935


def test_value_reads_per_class_metric() -> None:
    assert _value(_REPORT, ("replay", "precision")) == 0.5
    assert _value(_REPORT, ("combat", "recall")) == 0.7937


def test_value_defaults_to_zero_for_missing_class_or_key() -> None:
    """缺类 / 缺键一律 0.0，而不是抛错——对照表要能把"全无"这类读成 0。"""
    assert _value(_REPORT, ("buy", "precision")) == 0.0
    assert _value(_REPORT, ("replay", "support")) == 0.0
    assert _value({}, ("", "macro_f1")) == 0.0


def test_parse_models_accepts_name_equals_path(tmp_path) -> None:
    model = tmp_path / "m"
    model.mkdir()
    (model / "valorant_phase_v1.onnx").write_bytes(b"stub")
    parsed = parse_models([f"base={model}", f"cand={model}"])
    assert [name for name, _ in parsed] == ["base", "cand"]
    assert all(path == model.resolve() for _, path in parsed)


def test_parse_models_rejects_malformed_spec(tmp_path) -> None:
    with pytest.raises(SystemExit):
        parse_models([str(tmp_path)])


def test_parse_models_rejects_dir_without_onnx(tmp_path) -> None:
    model = tmp_path / "empty"
    model.mkdir()
    with pytest.raises(SystemExit):
        parse_models([f"base={model}"])


def test_compare_json_artifact_shape_matches_docs_report() -> None:
    """文档 §4.1 引用的 JSON 必须至少含那几行对照；文档与产物不许漂移。

    `docs/reports/*.json` 按 `.gitignore:203` 是**本地产物**（由
    `compare_models_official.py --json` 重新生成），故文件不存在时跳过——
    不能让一个 gitignore 的产物成为别人 clone 下的硬依赖。
    """
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "docs/reports/b1-retrain-compare-20260910.json"
    if not path.is_file():
        pytest.skip("本地产物不存在；用 compare_models_official.py --json 重新生成")
    payload = json.loads(path.read_text(encoding="utf-8"))
    for key in ("baseline:val", "control(no-relabel):val", "k1(relabeled):val", "k4(relabeled):val",
                "baseline:test", "k1(relabeled):test"):
        assert key in payload["results"], key
    # 文档里的两处关键结论数字必须与记录一致，防止文档与产物漂移
    assert payload["results"]["baseline:val"]["macro_f1"] == 0.8935
    assert payload["results"]["k1(relabeled):val"]["macro_f1"] == 0.7821
    assert payload["results"]["k1(relabeled):test"]["per_class"]["replay"]["recall"] == 0.625
    assert payload["results"]["k4(relabeled):val"]["per_class"]["combat"]["recall"] == 0.7864
