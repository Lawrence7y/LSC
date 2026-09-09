#!/usr/bin/env python3
"""Filter newly inferred frames that deserve an uncertainty review.

The filter keeps every low-confidence prediction and every one-frame label run.
The latter catches isolated category jumps that can look confident to the model
but are often caused by a transition, overlay, or a bad frame.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _confidence(row: dict[str, Any]) -> float | None:
    value = row.get("coarse_confidence")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _prediction(row: dict[str, Any]) -> str:
    return str(row.get("coarse_label") or row.get("label") or "")


def filter_suspicious_rows(
    rows: list[dict[str, Any]],
    *,
    confidence_threshold: float = 0.70,
    max_run_frames: int = 1,
) -> list[dict[str, Any]]:
    """Return rows selected for manual uncertainty review.

    Runs are calculated independently for each video and in timestamp order.
    A row is selected when its confidence is below the threshold, confidence
    is missing, or its predicted label belongs to a run no longer than
    ``max_run_frames``.
    """
    if max_run_frames < 1:
        raise ValueError("max_run_frames must be at least 1")

    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("manifest rows must be objects")
        video_id = str(row.get("video_id") or row.get("session_id") or "")
        grouped[video_id].append((index, row))

    selected: dict[int, dict[str, Any]] = {}
    reasons: dict[int, set[str]] = defaultdict(set)
    for items in grouped.values():
        ordered = sorted(items, key=lambda item: float(item[1].get("timestamp_sec", 0.0)))
        start = 0
        while start < len(ordered):
            end = start + 1
            prediction = _prediction(ordered[start][1])
            while end < len(ordered) and _prediction(ordered[end][1]) == prediction:
                end += 1
            run_length = end - start
            for position in range(start, end):
                index, row = ordered[position]
                confidence = _confidence(row)
                if confidence is None or confidence < confidence_threshold:
                    reasons[index].add("low_confidence")
                if run_length <= max_run_frames:
                    reasons[index].add("isolated_label_transition")
                if index in reasons:
                    selected[index] = row
            start = end

    result: list[dict[str, Any]] = []
    for index, row in sorted(selected.items(), key=lambda item: item[0]):
        item = dict(row)
        reason_codes = reasons[index]
        ordered_codes = [
            code for code in ("low_confidence", "isolated_label_transition") if code in reason_codes
        ]
        item["review_reason"] = "+".join(ordered_codes)
        result.append(item)
    return result


def _read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_no}: row must be an object")
        rows.append(value)
    return rows


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="筛选新增素材中可疑或不确定的粗标注帧")
    parser.add_argument("--input", type=Path, required=True, help="粗标注 manifest JSONL")
    parser.add_argument("--output", type=Path, required=True, help="筛选后的 manifest JSONL")
    parser.add_argument("--confidence-threshold", type=float, default=0.70)
    parser.add_argument("--max-run-frames", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rows = _read_rows(args.input)
    selected = filter_suspicious_rows(
        rows,
        confidence_threshold=args.confidence_threshold,
        max_run_frames=args.max_run_frames,
    )
    _write_rows(args.output, selected)
    counts = Counter(row["review_reason"] for row in selected)
    print(f"input={args.input} rows={len(rows)}")
    print(f"output={args.output} rows={len(selected)}")
    print("reasons=" + json.dumps(counts, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
