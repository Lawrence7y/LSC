"""Valorant 混合视觉分类评估与完整录像盲测发布门。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from eval_gates import (
    CLASS_NAMES,
    align_predictions_labels,
    build_classification_report,
    check_all_gates,
    classification_report_to_dict,
    compute_round_report,
    load_jsonl,
    round_report_to_dict,
)

def _print_confusion(confusion: dict[str, dict[str, int]]) -> None:
    header = "true\\pred".ljust(12) + "".join(c.rjust(10) for c in CLASS_NAMES)
    print(header)
    print("-" * len(header))
    for gt in CLASS_NAMES:
        row = gt.ljust(12) + "".join(
            str(confusion.get(gt, {}).get(pr, 0)).rjust(10) for pr in CLASS_NAMES
        )
        print(row)


def _print_classification(report_dict: dict) -> None:
    print("\n=== 五分类评估 ===")
    print(f"样本数: {report_dict.get('total', 0)}")
    print(f"Macro F1: {report_dict.get('macro_f1', 0.0):.4f}")
    print("\n混淆矩阵:")
    _print_confusion(report_dict.get("confusion_matrix") or {})
    print("\n每类 P/R/F1:")
    for name in CLASS_NAMES:
        m = (report_dict.get("per_class") or {}).get(name)
        if not m:
            continue
        print(
            f"  {name:10s}  P={m['precision']:.4f}  R={m['recall']:.4f}  "
            f"F1={m['f1']:.4f}  n={m['support']}"
        )
    by_source = report_dict.get("by_source_type") or {}
    if by_source:
        print("\n按 source_type:")
        for st, block in by_source.items():
            print(f"  [{st}] Macro F1={block.get('macro_f1', 0.0):.4f}  n={block.get('total', 0)}")


def _print_rounds(report_dict: dict) -> None:
    print("\n=== 回合级评估 ===")
    print(f"GT 回合: {report_dict.get('ground_truth_count', 0)}")
    print(f"匹配: {report_dict.get('matched', 0)}  recall={report_dict.get('recall', 0.0):.4f}")
    print(
        f"入列: {report_dict.get('listed_count', 0)}  "
        f"precision={report_dict.get('listed_precision', 0.0):.4f}"
    )
    print(
        f"起止误差 P95: start={report_dict.get('start_err_p95', 0.0):.3f}s  "
        f"end={report_dict.get('end_err_p95', 0.0):.3f}s"
    )
    print(
        f"vision_confirmed |err| P95={report_dict.get('vision_confirmed_err_p95', 0.0):.3f}s  "
        f"max={report_dict.get('vision_confirmed_err_max', 0.0):.3f}s  "
        f"n={report_dict.get('vision_confirmed_count', 0)}"
    )
    print(
        f"强制闭合 150s: {report_dict.get('forced_close_150', 0)}  "
        f"180s: {report_dict.get('forced_close_180', 0)}  "
        f"重复 key: {report_dict.get('duplicate_keys', 0)}  "
        f"非游戏入列: {report_dict.get('non_game_listed_count', 0)}"
    )


def load_report_json(path: Path) -> tuple[dict | None, dict | None]:
    data = json.loads(path.read_text(encoding="utf-8"))
    classification = data.get("classification") or data.get("frame_classification")
    rounds = data.get("rounds") or data.get("round_metrics")
    if classification is None and "macro_f1" in data:
        classification = data
    return classification, rounds


def build_from_predictions(
    predictions_path: Path,
    labels_path: Path,
) -> dict:
    preds = load_jsonl(predictions_path)
    labels = load_jsonl(labels_path)
    y_true, y_pred, sources = align_predictions_labels(preds, labels)
    if not y_true:
        raise ValueError("predictions 与 labels 无交集键 (video_id, timestamp_sec)")
    report = build_classification_report(y_true, y_pred, sources)
    return classification_report_to_dict(report)


def load_round_manifest(path: Path) -> list[dict]:
    if path.suffix.lower() == ".jsonl":
        return load_jsonl(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("videos") if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise ValueError("round manifest 必须是 JSON 数组或包含 videos 数组")
    return records


def build_rounds_from_videos(
    records: list[dict],
    *,
    detector,
    model_dir: Path | None,
) -> dict:
    ground_truth: list[dict] = []
    predictions: list[dict] = []
    for index, record in enumerate(records):
        video_path = str(record.get("video_path") or "")
        if not video_path:
            raise ValueError(f"round manifest 第 {index + 1} 条缺少 video_path")
        video_id = str(record.get("video_id") or Path(video_path).stem)
        session_id = str(record.get("session_id") or video_id)
        ground_truth.extend(
            {**row, "video_id": video_id}
            for row in record.get("ground_truth") or []
        )
        for row in detector(
            video_path,
            model_dir=model_dir,
            session_id=session_id,
        ):
            start = float(row.get("start", row.get("start_sec", 0.0)))
            end = float(row.get("end", row.get("end_sec", 0.0)))
            listed = (
                row.get("boundary_source") == "valorant_hybrid_v1"
                and row.get("confirm_status") in ("vision_confirmed", "pending")
                and end > start
            )
            predictions.append({**row, "video_id": video_id, "listed": listed})
    return round_report_to_dict(compute_round_report({
        "ground_truth": ground_truth,
        "predictions": predictions,
    }))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Valorant 视觉分类与盲测回合评估")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--report", type=Path, help="已计算的评估报告 JSON")
    src.add_argument("--predictions", type=Path, help="预测 JSONL（与 --labels 联用）")
    p.add_argument("--labels", type=Path, help="标签 JSONL")
    rounds = p.add_mutually_exclusive_group()
    rounds.add_argument("--rounds", type=Path, help="回合真值/预测 JSON")
    rounds.add_argument("--round-manifest", type=Path, help="完整录像及回合真值 manifest")
    p.add_argument("--model-dir", type=Path, help="Valorant ONNX 模型目录")
    p.add_argument(
        "--enforce-gates",
        action="store_true",
        help="门槛未通过时以非零退出码结束",
    )
    p.add_argument(
        "--output",
        type=Path,
        help="将合并报告写入 JSON 文件",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    classification_dict: dict | None = None
    rounds_dict: dict | None = None

    if args.report:
        classification_dict, rounds_dict = load_report_json(args.report)
    else:
        if not args.labels:
            print("错误: --predictions 需要同时提供 --labels", file=sys.stderr)
            return 2
        classification_dict = build_from_predictions(args.predictions, args.labels)

    if args.rounds:
        rounds_raw = json.loads(args.rounds.read_text(encoding="utf-8"))
        rounds_dict = round_report_to_dict(compute_round_report(rounds_raw))
    elif args.round_manifest:
        from lsc.analyzer.round_detector import detect_valorant_rounds_hybrid

        rounds_dict = build_rounds_from_videos(
            load_round_manifest(args.round_manifest),
            detector=detect_valorant_rounds_hybrid,
            model_dir=args.model_dir,
        )

    if classification_dict:
        _print_classification(classification_dict)
    if rounds_dict:
        _print_rounds(rounds_dict)

    merged = {
        "classification": classification_dict,
        "rounds": rounds_dict,
    }
    if args.output:
        args.output.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    failures = check_all_gates(classification_dict, rounds_dict)
    if failures:
        print("\n=== 发布门 ===")
        for f in failures:
            print(f"  FAIL  {f.check}: {f.message}")
        if args.enforce_gates:
            return 1
        print("（未加 --enforce-gates，不以失败退出）")
    else:
        print("\n=== 发布门 ===")
        print("  PASS  全部门槛满足")

    return 0


if __name__ == "__main__":
    sys.exit(main())
