#!/usr/bin/env python3
"""Batch-infer Valorant phase labels for manifest rows and write predictions JSONL.

Usage::

    python scripts/valorant_vision/predict_manifest.py \\
        --manifest datasets/valorant_phase/annotations/manifest.jsonl \\
        --data-dir datasets/valorant_phase \\
        --model-dir datasets/valorant_phase/model \\
        --split test \\
        --output datasets/valorant_phase/reports/predictions_test.jsonl
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from extract_frames import ManifestRow, SPLITS, load_manifest, output_path_for  # noqa: E402
from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier  # noqa: E402

_log = logging.getLogger(__name__)

CLASS_NAMES = ("non_game", "buy", "combat", "result", "replay")


def predict_rows(
    rows: list[dict],
    classifier,
    *,
    load_frame,
    batch_size: int = 64,
) -> list[dict]:
    output: list[dict] = []
    for offset in range(0, len(rows), batch_size):
        batch_rows = rows[offset:offset + batch_size]
        frames = [load_frame(row) for row in batch_rows]
        probabilities = classifier.predict_batch(frames)
        for row, probs in zip(batch_rows, probabilities, strict=True):
            output.append({
                "video_id": row["video_id"],
                "timestamp_sec": float(row["timestamp_sec"]),
                "source_type": row["source_type"],
                "predicted_label": CLASS_NAMES[int(np.argmax(probs))],
            })
    return output


def _rows_for_split(manifest_path: Path, split: str) -> list[dict]:
    rows = load_manifest(manifest_path)
    return [
        {
            "video_id": row.video_id,
            "video_path": row.video_path,
            "timestamp_sec": row.timestamp_sec,
            "label": row.label,
            "split": row.split,
            "source_type": row.source_type,
            "session_id": row.session_id,
            "notes": row.notes,
        }
        for row in rows
        if row.split == split
    ]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Valorant manifest 批量推理并输出 predictions JSONL")
    p.add_argument("--manifest", type=Path, required=True, help="训练清单 JSONL")
    p.add_argument("--data-dir", type=Path, required=True, help="抽帧 JPEG 根目录")
    p.add_argument("--model-dir", type=Path, required=True, help="Valorant ONNX 模型目录")
    p.add_argument(
        "--split",
        choices=SPLITS,
        required=True,
        help="仅推理该 split 的 manifest 行",
    )
    p.add_argument("--output", type=Path, required=True, help="predictions JSONL 输出路径")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    if not args.manifest.is_file():
        _log.error("清单不存在: %s", args.manifest)
        return 2
    if not args.data_dir.is_dir():
        _log.error("数据目录不存在: %s", args.data_dir)
        return 2

    try:
        rows = _rows_for_split(args.manifest, args.split)
    except ValueError as exc:
        _log.error("%s", exc)
        return 2

    if not rows:
        _log.warning("split=%s 无 manifest 行", args.split)
        _write_jsonl(args.output, [])
        return 0

    import cv2

    classifier = ValorantFrameClassifier(model_dir=args.model_dir)

    def load_frame(row: dict) -> np.ndarray:
        image_path = output_path_for(ManifestRow.from_dict(row), args.data_dir)
        frame = cv2.imread(str(image_path))
        if frame is None:
            raise ValueError(f"无法解码 JPEG: {image_path}")
        return frame

    predicted = predict_rows(rows, classifier, load_frame=load_frame)
    _write_jsonl(args.output, predicted)
    _log.info("已写入 %d 行 -> %s", len(predicted), args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
