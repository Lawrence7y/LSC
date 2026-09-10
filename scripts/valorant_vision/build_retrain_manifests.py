#!/usr/bin/env python3
"""为重训生成两份清单：基线权重清单 + 「标签纠正样本」重加权清单。

为什么需要它们（2026-09-10 实测踩到的两个坑）
-------------------------------------------
1. ``datasets/valorant_phase_broadcast`` 现有清单 ``manifest_broadcast.jsonl``
   只有 ``video_id/frame_path/label/split/source_type/session_id`` —— **没有**
   ``label_source``/``coarse_confidence``。而 ``train_weighted_distill.collect_samples``
   把"没有 label_source"一律按 ``coarse_model`` + confidence 0.0 处理
   （``pseudo_sample_weight(0.0) == 0.05``，且 ``sample_weight`` 在 CE 里是
   **加权平均**，故只有**相对**权重有意义）。即：直接拿它当 ``--new-manifest``，
   所有帧都是等权，**人工纠正过的 455 帧拿不到任何额外监督**。

2. 更致命的是蒸馏项：教师 = 当前广播档模型，而它在被纠正的 455 帧上
   **454/455 判 ``non_game``（平均 p(non_game)=0.983、p(replay)=0.015）**。
   ``--new-manifest`` 会让这些帧拿到 ``distill_weight=0.50``，于是 KL 项会
   把刚纠正的标签**反向拉回** non_game —— 重训会白做。
   故这些帧必须 ``hard_distill_weight = 0.0``（蒸馏目标已知是错的）。

产物
----
``manifest_broadcast_retrain_<date>.jsonl``
    全量帧（train/val/test 三个 split），``label_source=coarse_model``、
    ``coarse_confidence=0.0`` → 等权 0.05，作为"背景权重基准"。
``manifest_broadcast_relabel_hard_<date>.jsonl``
    455 帧纠正样本，带 ``hard_weight``（CE 放大）与 ``hard_distill_weight=0.0``
    （关闭蒸馏），供 ``train_onnx_finetune.py --hard-manifest`` 使用。

用法
----
    python scripts/valorant_vision/build_retrain_manifests.py \
        --rollback C:/lsc_tmp/verify/relabel_rollback.jsonl \
        --hard-weight 4.0
    python scripts/valorant_vision/build_retrain_manifests.py --check   # 只报告不写
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CLASSES = ("non_game", "buy", "combat", "result", "replay")
# 增强/过采样标签（生成方：build_broadcast_hard_dataset.py 的 hardx/rarex；
# 判定方：rebuild_source_separated_datasets.py 的 is_oversample）。
# 注意两种出现形式都有：广播档是**中缀**（`..._bc_rarex0_bc_broadcast...`），
# POV/难例档是**前缀**（`hardpov0_...` / `low_...` / `hardx4_...`）。
_OVERSAMPLE_TAGS = r"(?:rarex\d+|replay_boost\d+|hardpov\d+|hardx\d+|low)"
_PREFIX_TAG_RE = re.compile(rf"^(?:{_OVERSAMPLE_TAGS})_")
_INFIX_TAG_RE = re.compile(rf"_(?:{_OVERSAMPLE_TAGS})(?=_)")


def _base_name(filename: str) -> str:
    """剥掉增强/过采样标签，得到"唯一源帧"标识（权重核算的口径基准）。

    循环直到不再变化，以覆盖 `replay_boost2_rarex3_` 这类**叠加**标签
    （它们逐层插入，一次替换去不干净）。
    """
    stem = filename[:-4] if filename.lower().endswith(".jpg") else filename
    while True:
        stripped = _PREFIX_TAG_RE.sub("", stem)
        stripped = _INFIX_TAG_RE.sub("", stripped)
        if stripped == stem:
            return stem
        stem = stripped


DEFAULT_DATA_DIR = _ROOT / "datasets/valorant_phase_broadcast"
DEFAULT_TEACHER = _ROOT / "lsc/analyzer/models/valorant_phase_broadcast_finetune_v4_fused_20260907"
DEFAULT_OUT_DIR = _ROOT / "scripts/valorant_vision"
DATE_TAG = "20260910"


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def _is_train(row: dict) -> bool:
    """CE 只作用在 train 分片——权重占比的分母不能把 val/test 算进来。"""
    return str(row.get("split") or "") == "train"


def _scan_dataset(data_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for split in ("train", "val", "test"):
        for label in CLASSES:
            for path in sorted((data_dir / split / label).glob("*.jpg")):
                rows.append(
                    {
                        "frame_path": str(path),
                        "label": label,
                        "split": split,
                        "label_source": "coarse_model",
                        "coarse_confidence": 0.0,
                        "notes": "retrain baseline weight (uniform)",
                    }
                )
    return rows


def _teacher_predictions(teacher_dir: Path, paths: list[Path]) -> dict[str, tuple[str, float]]:
    """用教师模型复算这些帧的预测（作为"蒸馏目标已知错误"的可审计证据）。"""
    import cv2
    import numpy as np

    from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier

    clf = ValorantFrameClassifier(teacher_dir)
    clf.load()
    out: dict[str, tuple[str, float]] = {}
    for start in range(0, len(paths), 64):
        batch = paths[start : start + 64]
        images = []
        keep: list[Path] = []
        for path in batch:
            image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is not None:
                images.append(image)
                keep.append(path)
        if not images:
            continue
        probs = np.asarray(clf.predict_batch(images), dtype=np.float32)
        for path, row in zip(keep, probs, strict=True):
            index = int(row.argmax())
            out[str(path)] = (CLASSES[index], float(row[index]))
    return out


def build(
    *,
    data_dir: Path,
    rollback: Path | None,
    teacher_dir: Path | None,
    out_dir: Path,
    hard_weight: float,
    date_tag: str,
) -> dict:
    base_rows = _scan_dataset(data_dir)
    report: dict = {"baseline_rows": len(base_rows), "by_split": {}}
    for split in ("train", "val", "test"):
        report["by_split"][split] = sum(1 for r in base_rows if r["split"] == split)

    hard_rows: list[dict] = []
    if rollback is not None and rollback.is_file():
        entries = [e for e in _read_jsonl(rollback) if e.get("to")]
        paths = [Path(str(e["to"])) for e in entries]
        missing = [str(p) for p in paths if not p.is_file()]
        predictions: dict[str, tuple[str, float]] = {}
        if teacher_dir is not None and teacher_dir.is_dir():
            predictions = _teacher_predictions(teacher_dir, paths)
        for entry, path in zip(entries, paths, strict=True):
            predicted, confidence = predictions.get(str(path), ("", 0.0))
            hard_rows.append(
                {
                    "frame_path": str(path),
                    "label": "replay",
                    "split": str(entry.get("split") or ""),
                    "true_label": "replay",
                    "previous_label": str(entry.get("was") or ""),
                    "predicted_label": predicted,
                    "confidence": round(float(confidence), 6),
                    "reason": "REPLAY 水印确证（OCR 实读 REPLAY，置信度 >=0.99，人工确认）"
                    "——纠正标签后必须关闭蒸馏：教师在此帧上判的是旧标签",
                    "hard_weight": float(hard_weight),
                    "hard_distill_weight": 0.0,
                }
            )
        report["relabel_rows"] = len(hard_rows)
        report["relabel_missing_files"] = missing
        report["teacher_predicted_non_replay"] = sum(
            1 for row in hard_rows if row["predicted_label"] not in ("", "replay")
        )
        report["by_split"]["relabel_train"] = sum(
            1 for row in hard_rows if (Path(row["frame_path"]).parent.parent.name == "train")
        )
    else:
        report["relabel_rows"] = 0

    # ---- 权重核算：CE 是加权平均，故看的是"纠正样本占 CE 权重比" ----
    # 注意按**成员身份**统计而不是"权重 > 基准"，否则 k=1（等权）会被算成 0。
    weights = [0.05] * len(base_rows)
    keys = {row["frame_path"]: i for i, row in enumerate(base_rows)}
    relabeled_indices: set[int] = set()
    for row in hard_rows:
        index = keys.get(row["frame_path"])
        if index is None:
            continue
        weights[index] = 0.05 * max(1.0, float(row["hard_weight"]))
        relabeled_indices.add(index)
    total = sum(weights[i] for i, row in enumerate(base_rows) if _is_train(row))
    relabeled_mass = sum(
        weights[i] for i in relabeled_indices if _is_train(base_rows[i])
    )
    report["ce_weight_share_of_relabeled"] = round(relabeled_mass / total, 4) if total else 0.0
    report["baseline_rows_boosted"] = len(relabeled_indices)
    # 有效样本量：唯一源帧（去掉 rarex/replay_boost 等增强/过采样副本）——
    # 这是唯一正确的权重分母口径（见报告 §2），文件数会把放大倍数误当样本量
    # （train/replay 实测 1587 帧 = 158 个唯一源，放大 10.04 倍）。
    train_bases = {_base_name(Path(row["frame_path"]).name) for row in base_rows if _is_train(row)}
    report["train_unique_sources"] = len(train_bases)
    report["train_frames"] = sum(1 for row in base_rows if _is_train(row))
    # 以**数据集扫描**的 split 为准（回滚单里的 split 字段未必可靠）
    relabel_train = {r["frame_path"] for r in hard_rows
                     if r["frame_path"] in {row["frame_path"] for row in base_rows if _is_train(row)}}
    report["relabel_train_rows"] = len(relabel_train)
    report["relabel_unique_sources"] = len(
        {_base_name(Path(row["frame_path"]).name) for row in hard_rows}
    )
    # 每**源帧**的 CE 权重比：纠正样本 vs 该 split 内既有同类的平均
    # （只有这个口径能回答"新样本是不是被过度加权"——文件数会骗人）
    replay_reference = {
        _base_name(Path(row["frame_path"]).name)
        for row in base_rows
        if _is_train(row) and row["label"] == "replay" and row["frame_path"] not in relabel_train
    }
    new_sources = len({_base_name(Path(p).name) for p in relabel_train})
    if new_sources and replay_reference:
        per_new = (report["relabel_train_rows"] * 0.05 * max(1.0, hard_weight)) / new_sources
        per_ref = (
            sum(1 for row in base_rows
                if _is_train(row) and row["label"] == "replay"
                and row["frame_path"] not in relabel_train) * 0.05
        ) / len(replay_reference)
        report["ce_weight_per_source_ratio"] = round(per_new / per_ref, 2) if per_ref else None
        report["existing_replay_sources"] = len(replay_reference)
    else:
        report["ce_weight_per_source_ratio"] = None

    report["baseline_manifest"] = str(
        out_dir / f"manifest_broadcast_retrain_{date_tag}.jsonl"
    )
    report["hard_manifest"] = str(
        out_dir / f"manifest_broadcast_relabel_hard_{date_tag}.jsonl"
    )
    _write_jsonl(out_dir / f"manifest_broadcast_retrain_{date_tag}.jsonl", base_rows)
    _write_jsonl(out_dir / f"manifest_broadcast_relabel_hard_{date_tag}.jsonl", hard_rows)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--rollback", type=Path, default=Path("C:/lsc_tmp/verify/relabel_rollback.jsonl"),
                        help="标签纠正回滚单（from/to/split/was），提供它才有重加权清单")
    parser.add_argument("--teacher-dir", type=Path, default=DEFAULT_TEACHER,
                        help="用于固化'蒸馏目标已知错误'的证据；--no-teacher 可跳过")
    parser.add_argument("--no-teacher", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--hard-weight", type=float, default=4.0,
                        help="纠正样本的 CE 放大倍数（相对等权基准）")
    parser.add_argument("--date-tag", default=DATE_TAG)
    parser.add_argument("--check", action="store_true", help="只核算权重，不写文件")
    args = parser.parse_args(argv)

    if args.check:
        data_dir = args.data_dir.expanduser().resolve()
        base_rows = _scan_dataset(data_dir)
        train_rows = [row for row in base_rows if _is_train(row)]
        entries = [e for e in _read_jsonl(args.rollback) if e.get("to")] if args.rollback.is_file() else []
        train_paths = {row["frame_path"] for row in train_rows}
        n_base, n_rel = len(train_rows), sum(1 for e in entries if str(e["to"]) in train_paths)
        total = (n_base - n_rel) * 0.05 + n_rel * 0.05 * max(1.0, args.hard_weight)
        share = (n_rel * 0.05 * max(1.0, args.hard_weight)) / total if total else 0.0
        unique_sources = len({_base_name(Path(row["frame_path"]).name) for row in train_rows})
        print(f"train 帧数 {n_base}（唯一源帧 {unique_sources}），纠正样本 train 帧数 {n_rel}"
              f"（唯一源帧 {len({_base_name(Path(str(e['to'])).name) for e in entries if str(e['to']) in train_paths})}）")
        print(f"hard_weight={args.hard_weight} → 纠正样本占 **train** CE 权重 {share:.2%}")
        print("（分母只含 train：val/test 不参与 CE）")
        return 0

    report = build(
        data_dir=args.data_dir.expanduser().resolve(),
        rollback=args.rollback.expanduser().resolve(),
        teacher_dir=None if args.no_teacher else args.teacher_dir.expanduser().resolve(),
        out_dir=args.out_dir.expanduser().resolve(),
        hard_weight=args.hard_weight,
        date_tag=args.date_tag,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
