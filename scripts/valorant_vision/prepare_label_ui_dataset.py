#!/usr/bin/env python3
"""Prepare a source dataset as a non-destructive queue for ``serve_label_ui``.

The source-separated rebuild emits a manifest for model evaluation, while the
label UI expects ``queue.json`` plus ``labels.json`` under the image root.
This adapter keeps the existing dataset label as ``current_label`` and starts
with an empty human-label file so every frame can be reviewed again.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

SPLIT_ORDER = {"train": 0, "val": 1, "test": 2}


def _resolve_frame_path(raw_path: Any, *, manifest_path: Path, dataset_root: Path) -> Path:
    if not raw_path:
        raise ValueError("manifest row missing frame_path")
    frame_path = Path(str(raw_path))
    if not frame_path.is_absolute():
        frame_path = (manifest_path.parent / frame_path).resolve()
    else:
        frame_path = frame_path.resolve()
    try:
        frame_path.relative_to(dataset_root)
    except ValueError as exc:
        raise ValueError(f"frame outside dataset root: {frame_path}") from exc
    if not frame_path.is_file():
        raise ValueError(f"frame does not exist: {frame_path}")
    return frame_path


def _stable_id(relative_path: str, index: int) -> str:
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:10]
    return f"dataset-{index:06d}-{digest}"


def build_queue(
    manifest_paths: Path | list[Path],
    dataset_root: Path,
    *,
    allow_unlabeled: bool = False,
) -> list[dict[str, Any]]:
    dataset_root = dataset_root.resolve()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    paths = [manifest_paths] if isinstance(manifest_paths, Path) else list(manifest_paths)
    for manifest_path in paths:
        for line_no, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{manifest_path}:{line_no}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{manifest_path}:{line_no}: row must be an object")
            frame_path = _resolve_frame_path(
                row.get("frame_path"), manifest_path=manifest_path, dataset_root=dataset_root,
            )
            relative_path = frame_path.relative_to(dataset_root).as_posix()
            split = str(row.get("split") or "")
            if split not in SPLIT_ORDER:
                raise ValueError(f"{manifest_path}:{line_no}: invalid split {split!r}")
            label = str(row.get("label") or "")
            if not label and not allow_unlabeled:
                raise ValueError(f"{manifest_path}:{line_no}: missing label")
            record = {
                "_sort": (SPLIT_ORDER[split], str(row.get("session_id") or ""),
                          float(row.get("timestamp_sec", 0.0)), relative_path),
                "rel_path": relative_path,
                "abs_path": str(frame_path),
                "video_id": str(row.get("video_id") or row.get("session_id") or ""),
                "video_path": row.get("video_path"),
                "timestamp_sec": float(row.get("timestamp_sec", 0.0)),
                "source_type": str(row.get("source_type") or ""),
                "session_id": str(row.get("session_id") or ""),
                "split": split,
                "current_label": label,
                "suggested_label": label,
                "coarse_label": row.get("coarse_label") or label or None,
                "coarse_confidence": row.get("coarse_confidence"),
                "review_reason": str(row.get("review_reason") or ""),
                "priority": (
                    "uncertain_review" if row.get("review_reason")
                    else ("dataset_review" if label else "new_recording")
                ),
                "reason": (
                    str(row.get("review_reason")) if row.get("review_reason")
                    else ("来源分离数据集复核" if label else "新录制素材，等待人工标注")
                ),
                "content_sha256": hashlib.sha256(frame_path.read_bytes()).hexdigest(),
            }
            grouped[record["content_sha256"]].append(record)

    rows: list[dict[str, Any]] = []
    for group in grouped.values():
        group.sort(key=lambda item: item["_sort"])
        row = dict(group[0])
        row.pop("_sort", None)
        row["duplicate_count"] = len(group)
        row["duplicate_paths"] = [item["rel_path"] for item in group[1:]]
        labels = sorted({str(item["current_label"]) for item in group if item["current_label"]})
        row["original_labels"] = labels
        if len(labels) > 1:
            row["current_label"] = "冲突: " + "/".join(labels)
            row["suggested_label"] = ""
            row["priority"] = "dataset_conflict"
            row["reason"] = f"同一画面存在多个原始标签：{' / '.join(labels)}"
        elif labels and not row["current_label"]:
            row["current_label"] = labels[0]
            row["suggested_label"] = labels[0]
            row["priority"] = "dataset_review"
            row["reason"] = "同一画面已有来源标签，等待确认"
        rows.append(row)

    rows.sort(key=lambda item: (
        SPLIT_ORDER[str(item["split"])], str(item["session_id"]),
        float(item["timestamp_sec"]), str(item["rel_path"]),
    ))
    for index, row in enumerate(rows, 1):
        row["id"] = _stable_id(row["rel_path"], index)
    return rows


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="为 serve_label_ui 准备来源数据集标注队列")
    parser.add_argument("--manifest", type=Path, nargs="+", required=True, help="来源 manifest JSONL，可传多个")
    parser.add_argument("--dataset-root", type=Path, required=True, help="图片数据集根目录")
    parser.add_argument("--output-root", type=Path, default=None,
                        help="队列输出根目录，默认使用 dataset-root")
    parser.add_argument("--force", action="store_true", help="允许覆盖已有 queue/labels")
    parser.add_argument("--allow-unlabeled", action="store_true", help="允许新录制素材暂时没有 label")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest_paths = [path.resolve() for path in args.manifest]
    dataset_root = args.dataset_root.resolve()
    output_root = (args.output_root or dataset_root).resolve()
    queue_path = output_root / "queue.json"
    labels_path = output_root / "labels.json"
    if (queue_path.exists() or labels_path.exists()) and not args.force:
        raise SystemExit(f"已有标注队列，请加 --force 才覆盖: {output_root}")
    rows = build_queue(manifest_paths, dataset_root, allow_unlabeled=args.allow_unlabeled)
    _write_json(queue_path, rows)
    _write_json(labels_path, {})
    print(f"queue={queue_path} rows={len(rows)}")
    print(f"labels={labels_path} human_labels=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
