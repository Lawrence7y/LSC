#!/usr/bin/env python3
"""Apply the current Valorant classifier as a coarse label pass."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lsc.analyzer.valorant_frame_classifier import _CLASS_NAMES, ValorantFrameClassifier


def _native_path(raw: str) -> Path:
    """Convert WSL ``/mnt/<drive>/...`` paths for Windows Python when needed."""
    value = str(raw)
    if os.name == "nt" and value.startswith("/mnt/") and len(value) > 6:
        drive = value[5].upper()
        tail = value[7:].replace("/", "\\")
        return Path(f"{drive}:\\{tail}")
    return Path(value)


def _read_frame(path: Path):
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return image if image is not None else cv2.imread(str(path))


def coarse_label_rows(rows: list[dict], *, model_dir: Path, batch_size: int = 32) -> list[dict]:
    classifier = ValorantFrameClassifier(model_dir=model_dir)
    output: list[dict] = []
    for start in range(0, len(rows), batch_size):
        batch_rows = rows[start : start + batch_size]
        images = []
        for row in batch_rows:
            frame_path = _native_path(str(row.get("frame_path") or ""))
            image = _read_frame(frame_path)
            if image is None:
                raise ValueError(f"无法读取抽帧: {frame_path}")
            images.append(image)
        probabilities = classifier.predict_batch(images)
        for row, probs in zip(batch_rows, probabilities, strict=True):
            index = int(probs.argmax())
            label = _CLASS_NAMES[index]
            confidence = float(probs[index])
            item = dict(row)
            item["label"] = label
            item["coarse_label"] = label
            item["coarse_confidence"] = round(confidence, 6)
            item["notes"] = (
                f"{str(row.get('notes') or '').strip()}；当前官方模型粗标 "
                f"{label} conf={confidence:.3f}"
            ).lstrip("；")
            output.append(item)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用当前 Valorant 模型粗标录制帧 manifest")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args(argv)
    rows = [
        json.loads(line)
        for line in args.manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.batch_size <= 0:
        raise SystemExit("batch-size must be positive")
    labeled = coarse_label_rows(rows, model_dir=args.model_dir, batch_size=args.batch_size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in labeled) + "\n",
        encoding="utf-8",
    )
    tmp.replace(args.output)
    print(f"coarse_labeled={len(labeled)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
