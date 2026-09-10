#!/usr/bin/env python3
"""多模型对照：在**官方口径**下并排输出 val/test 的帧级门禁指标。

为什么要它
----------
``reeval_replay_verified.py`` 只做"基线 vs 候选"两方对照。B1 重训的关键证据是一条
**四方对照**：baseline / 不换标签的同配方对照 / 两种权重——只有并排才看得出
"`test` 召回涨的部分有多少来自纯微调、多少来自标签纠正"，以及代价落在哪一类上。

本脚本**完全复用** ``eval_source_dataset.evaluate()``（``--mode broadcast_runtime``），
不自己实现任何指标，故与官方晋级口径逐位一致。

用法
----
    python scripts/valorant_vision/compare_models_official.py \
        --models baseline=lsc/analyzer/models/valorant_phase_broadcast_finetune_v4_fused_20260907 \
                 retrain=C:/lsc_models/broadcast_retrain_k1_20260910 \
        --json docs/reports/b1-retrain-compare-20260910.json

注意
----
``combat_recall`` 与 ``replay_precision`` 必须与宏观指标**一起读**：2026-09-10 实跑中
宏观 Macro F1 掉 0.11 而 replay 召回**一点没变**（0.90 → 0.90），代价全在 combat——只看
replay 相关指标会得出完全相反的结论。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from eval_gates import (  # noqa: E402
    GATE_BUY_PRECISION_MIN,
    GATE_MACRO_F1_MIN,
    GATE_NON_GAME_RECALL_MIN,
    GATE_REPLAY_RECALL_MIN,
    GATE_RESULT_PRECISION_MIN,
)
from eval_source_dataset import evaluate  # noqa: E402

DEFAULT_DATA_DIR = _REPO_ROOT / "datasets/valorant_phase_broadcast"
DEFAULT_MANIFEST = _REPO_ROOT / "scripts/valorant_vision/manifest_broadcast.jsonl"

# (显示名, 取值路径, 门禁阈值或 None, 是否越大越好)
METRICS: tuple[tuple[str, tuple[str, str], float | None], ...] = (
    ("macro_f1", ("", "macro_f1"), GATE_MACRO_F1_MIN),
    ("replay_recall", ("replay", "recall"), GATE_REPLAY_RECALL_MIN),
    ("replay_precision", ("replay", "precision"), None),
    ("replay_f1", ("replay", "f1"), None),
    ("nongame_recall", ("non_game", "recall"), GATE_NON_GAME_RECALL_MIN),
    ("buy_precision", ("buy", "precision"), GATE_BUY_PRECISION_MIN),
    ("result_precision", ("result", "precision"), GATE_RESULT_PRECISION_MIN),
    ("combat_recall", ("combat", "recall"), None),
)


def _value(report: dict, path: tuple[str, str]) -> float:
    cls, key = path
    if not cls:
        return float(report.get(key, 0.0) or 0.0)
    per_class = report.get("per_class") or {}
    return float((per_class.get(cls) or {}).get(key, 0.0) or 0.0)


def evaluate_quietly(model_dir: Path, data_dir: Path, split: str, manifest: Path | None) -> dict:
    """调用官方 evaluate()，抑制其冗长打印（本脚本另行汇总）。"""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        return evaluate(
            model_dir, data_dir, split=split, mode="broadcast_runtime",
            rounds_path=None, manifest_path=manifest,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", required=True,
                        help="名称=模型目录（可多个），如 baseline=<dir> retrain=<dir>")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                        help="来源溯源清单（供 source_session_count；不提供会警告）")
    parser.add_argument("--splits", nargs="+", default=["val", "test"])
    parser.add_argument("--json", type=Path, default=None)
    return parser


def parse_models(specs: list[str]) -> list[tuple[str, Path]]:
    models: list[tuple[str, Path]] = []
    for spec in specs:
        name, sep, raw = spec.partition("=")
        if not sep or not name.strip() or not raw.strip():
            raise SystemExit(f"--models 需要 `名称=目录` 形式，收到: {spec!r}")
        path = Path(raw.strip()).expanduser().resolve()
        if not (path / "valorant_phase_v1.onnx").is_file():
            raise SystemExit(f"模型目录缺少 valorant_phase_v1.onnx: {path}")
        models.append((name.strip(), path))
    return models


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir.expanduser().resolve()
    manifest = args.manifest.expanduser().resolve() if args.manifest else None
    if manifest is not None and not manifest.is_file():
        print(f"!! 清单不存在，将按 None 处理: {manifest}", file=sys.stderr)
        manifest = None
    models = parse_models(args.models)

    results: dict[str, dict] = {}
    for name, path in models:
        for split in args.splits:
            report = evaluate_quietly(path, data_dir, split, manifest)
            results[f"{name}:{split}"] = report
        print(f"evaluated {name}", file=sys.stderr, flush=True)

    header = f"{'model':<24}{'split':<7}" + "".join(f"{name:>18}" for name, _, _ in METRICS)
    print("=" * len(header))
    print("多模型对照（口径：eval_source_dataset --mode broadcast_runtime）")
    print("=" * len(header))
    print(f"data-dir: {data_dir}")
    print(f"manifest: {manifest}")
    print(header)
    print("-" * len(header))
    for split in args.splits:
        for name, _ in models:
            report = results[f"{name}:{split}"]
            cells = "".join(f"{_value(report, path):>18.4f}" for _, path, _ in METRICS)
            print(f"{name:<24}{split:<7}{cells}")
        print("-" * len(header))
    print("门禁：" + "  ".join(
        f"{name}>={thr:.2f}" for name, _, thr in METRICS if thr is not None
    ))

    summary = {
        "data_dir": str(data_dir),
        "manifest": str(manifest) if manifest else None,
        "mode": "broadcast_runtime",
        "metrics": [name for name, _, _ in METRICS],
        "models": {name: str(path) for name, path in models},
        "results": {
            key: {
                "macro_f1": round(_value(report, ("", "macro_f1")), 4),
                "per_class": {
                    cls: {
                        key2: round(_value(report, (cls, key2)), 4)
                        for key2 in ("precision", "recall", "f1")
                    }
                    for cls in ("non_game", "buy", "combat", "result", "replay")
                },
                "frame_count": int((report.get("data_summary") or {}).get("frame_count", 0) or 0),
                "source_session_count": int(
                    (report.get("data_summary") or {}).get("source_session_count", 0) or 0
                ),
                "unknown_prediction_count": int(
                    (report.get("data_summary") or {}).get("unknown_prediction_count", 0) or 0
                ),
            }
            for key, report in results.items()
        },
    }
    if args.json:
        out = args.json.expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n已写出 JSON: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
