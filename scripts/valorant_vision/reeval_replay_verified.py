#!/usr/bin/env python3
"""重训后复核：候选模型 vs 基线，在「val 集 + 水印确证 test/replay」上的对照评估。

为什么需要这个脚本
------------------
官方晋级口径（``eval_source_dataset.py --mode broadcast_runtime``）只跑 ``val``，
而在 2026-09-10 之前 ``test/replay`` **是 0 帧** —— 即"模型到底能不能认出回放"
这件事**根本没有测试覆盖**。本脚本复用官方 ``evaluate()``（口径完全一致），
额外把 ``test/replay``（水印确证：OCR 实读 REPLAY、置信度 ≥0.99、人工确认）
纳入评估，并给出**基线 vs 候选**的对照表与门禁判读。

背景（2026-09-10 全量扫描结论）
--------------------------------
``train/non_game`` 原有 1061 帧中有 **451 帧其实带 REPLAY 水印**（被标成 non_game，
其中 360 帧文件名自带 ``replay_boost``）。模型把水印确证回放判成 ``non_game``，
很大程度上是**学对了错误标签**，而非"看不见水印"。纠正标签后重训，本脚本用于
验证 ``replay`` 召回是否随之跳升。

用法
----
    python scripts/valorant_vision/reeval_replay_verified.py \
        --candidate-dir ~/LSC/models/valorant_phase_broadcast_retrain_20260910 \
        --baseline-dir lsc/analyzer/models/valorant_phase_broadcast_finetune_v4_fused_20260907 \
        --data-dir datasets/valorant_phase_broadcast \
        --json docs/reports/reeval_replay_verified_20260910.json

注意
----
本脚本只覆盖**帧级五分类**门禁；官方晋级还要求**回合级**报告
（``check_all_gates`` 里 ``rounds_missing`` 会直接判失败），需另行提供
``--rounds``（默认 None 时脚本会明确提示该项未验）。
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

# 广播档实际加载的模型目录（见 lsc/analyzer/valorant_frame_classifier.py）。
DEFAULT_BASELINE = (
    _REPO_ROOT / "lsc/analyzer/models/valorant_phase_broadcast_finetune_v4_fused_20260907"
)
DEFAULT_DATA_DIR = _REPO_ROOT / "datasets/valorant_phase_broadcast"


def _run_quietly(model_dir: Path, data_dir: Path, split: str, mode: str,
                 rounds_path: Path | None, manifest_path: Path | None) -> dict:
    """调用官方 evaluate()，抑制其冗长打印（本脚本另行汇总输出）。

    ``manifest_path`` 不可省：``promote_model.py`` 要求
    ``source_session_count >= 3``，而来源会话只能由 manifest 的
    ``source_type``/``session_id`` 字段提供（索引按完整路径**与 basename** 双键，
    故清单里的 /mnt/d/... 路径在 Windows 下也能按 basename 命中）。
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        return evaluate(model_dir, data_dir, split=split, mode=mode,
                        rounds_path=rounds_path, manifest_path=manifest_path)


def _gate_rows(report: dict) -> list[tuple[str, float, float, bool]]:
    per_class = report.get("per_class") or {}

    def metric(cls: str, name: str) -> float:
        return float((per_class.get(cls) or {}).get(name, 0.0) or 0.0)

    return [
        ("Macro F1", float(report.get("macro_f1", 0.0)), GATE_MACRO_F1_MIN,
         float(report.get("macro_f1", 0.0)) >= GATE_MACRO_F1_MIN),
        ("Replay Recall", metric("replay", "recall"), GATE_REPLAY_RECALL_MIN,
         metric("replay", "recall") >= GATE_REPLAY_RECALL_MIN),
        ("Non-Game Recall", metric("non_game", "recall"), GATE_NON_GAME_RECALL_MIN,
         metric("non_game", "recall") >= GATE_NON_GAME_RECALL_MIN),
        ("Buy Precision", metric("buy", "precision"), GATE_BUY_PRECISION_MIN,
         metric("buy", "precision") >= GATE_BUY_PRECISION_MIN),
        ("Result Precision", metric("result", "precision"), GATE_RESULT_PRECISION_MIN,
         metric("result", "precision") >= GATE_RESULT_PRECISION_MIN),
    ]


def _describe(report: dict) -> dict:
    per_class = report.get("per_class") or {}
    return {
        "model_version": report.get("model_version"),
        "model_sha256": report.get("model_sha256"),
        "macro_f1": round(float(report.get("macro_f1", 0.0)), 4),
        "per_class": {
            cls: {
                "precision": round(float(v.get("precision", 0.0)), 4),
                "recall": round(float(v.get("recall", 0.0)), 4),
                "f1": round(float(v.get("f1", 0.0)), 4),
                "support": int(v.get("support", 0) or 0),
            }
            for cls, v in per_class.items()
            if isinstance(v, dict)
        },
        "frame_count": int((report.get("data_summary") or {}).get("frame_count", 0) or 0),
        "source_session_count": int(
            (report.get("data_summary") or {}).get("source_session_count", 0) or 0
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--baseline-dir", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--mode", default="broadcast_runtime")
    parser.add_argument("--rounds", type=Path, default=None,
                        help="回合级报告输入；缺省则回合门禁未验（晋级必须另行提供）")
    parser.add_argument("--manifest", type=Path, default=None,
                        help="来源溯源清单（提供 source_type/session_id）；"
                             "晋级要求独立来源会话 >=3，故必须提供")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    candidate = args.candidate_dir.expanduser().resolve()
    baseline = args.baseline_dir.expanduser().resolve()
    data_dir = args.data_dir.expanduser().resolve()
    for label, path in (("candidate", candidate), ("baseline", baseline)):
        if not (path / "valorant_phase_v1.onnx").is_file():
            print(f"!! {label} 目录缺少 valorant_phase_v1.onnx: {path}", file=sys.stderr)
            return 2

    results: dict[str, dict] = {}
    for split in ("val", "test"):
        for label, model_dir in (("baseline", baseline), ("candidate", candidate)):
            report = _run_quietly(model_dir, data_dir, split, args.mode, args.rounds,
                                  args.manifest)
            results[f"{split}:{label}"] = report

    def rows(name: str) -> tuple[dict, list[tuple[str, float, float, bool]]]:
        report = results[name]
        return _describe(report), _gate_rows(report)

    print("=" * 92)
    print("重训复核：候选 vs 基线（口径复用 eval_source_dataset.evaluate）")
    print("=" * 92)
    print(f"data-dir  : {data_dir}")
    print(f"baseline  : {baseline}")
    print(f"candidate : {candidate}")

    summary: dict = {"data_dir": str(data_dir), "mode": args.mode,
                     "baseline_dir": str(baseline), "candidate_dir": str(candidate)}

    for split in ("val", "test"):
        base, base_gates = rows(f"{split}:baseline")
        cand, cand_gates = rows(f"{split}:candidate")
        tag = "val（官方门禁口径）" if split == "val" else "test（水印确证回放，官方原先无覆盖）"
        print(f"\n----- {split.upper()} · {tag} -----")
        print(f"  帧数: baseline {base['frame_count']} / candidate {cand['frame_count']}"
              f"   来源会话数: {base['source_session_count']} / {cand['source_session_count']}")
        print(f"  {'指标':<18}{'baseline':>12}{'candidate':>12}{'变化':>10}   {'门禁':<8}")
        for (bname, bval, bthr, bok), (cname, cval, cthr, cok) in zip(base_gates, cand_gates):
            delta = cval - bval
            flag = "PASS" if cok else f"FAIL(需 {cthr:.2f})"
            print(f"  {bname:<18}{bval:>12.4f}{cval:>12.4f}{delta:>+10.4f}   {flag:<8}")
        print("  replay 类: "
              f"precision {base['per_class'].get('replay', {}).get('precision', 0):.3f}"
              f" -> {cand['per_class'].get('replay', {}).get('precision', 0):.3f}   "
              f"recall {base['per_class'].get('replay', {}).get('recall', 0):.3f}"
              f" -> {cand['per_class'].get('replay', {}).get('recall', 0):.3f}   "
              f"support {cand['per_class'].get('replay', {}).get('support', 0)}")
        summary[split] = {"baseline": base, "candidate": cand,
                          "gate_rows": [{"check": n, "baseline": b, "candidate": c,
                                         "threshold": t, "passed": ok}
                                        for (n, b, t, _), (_n, c, _t2, ok) in zip(base_gates, cand_gates)]}

    print("\n" + "=" * 92)
    print("判读提示")
    print("=" * 92)
    print("  · val 看门禁是否全过；test 看 replay 召回是否随标签纠正而跳升（此前 val 口径看不到）")
    if args.manifest is None:
        print("  · ⚠️ 未提供 --manifest → source_session_count 将为 0，"
              "promote_model 会因'独立来源会话不足 3'拒绝晋级")
    if args.rounds is None:
        print("  · ⚠️ 回合级门禁未验（--rounds 缺省）→ check_all_gates 会因 rounds_missing 判失败，")
        print("    正式晋级前必须另行提供回合级报告")
    else:
        print(f"  · 回合级报告已提供: {args.rounds}")

    if args.json:
        out = args.json.expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写出 JSON: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
