#!/usr/bin/env python3
"""把多个「回放标记 ROI」数据集合并成一个可训练的目录（硬链接，不复制字节）。

为什么需要它
------------
标记支路的样本来自多路：仓库数据集（`build_marker_roi_dataset.py`）+ 若干次
录像挖掘（`mine_marker_roi_from_videos.py`，本地录像 / B站素材各一次）。
`train_onnx_finetune.py` 只认 `<data-dir>/<split>/<class>/*.jpg` 这一种结构，
所以需要一个"合并视图"。

用**硬链接**而非复制：同一卷上零额外空间，且源目录保持独立可重建；
`frame_path` 会被改写成合并目录下的路径（否则训练读的是源路径，跨机就失效）。

用法
----
    python scripts/valorant_vision/merge_marker_roi_datasets.py \
        --source D:/lsc_models/roi4_dataset \
        --source D:/lsc_models/roi4_mined_local \
        --source D:/lsc_models/roi4_mined_bili \
        --out-dir D:/lsc_models/marker_roi4_merged
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

CLASSES = ("non_game", "replay")


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _hardlink(source: Path, destination: Path) -> str:
    """硬链接；跨卷或已存在时退化为复制（并如实报告）。"""
    if destination.exists():
        return "exists"
    try:
        os.link(source, destination)
        return "linked"
    except OSError:
        import shutil

        shutil.copyfile(source, destination)
        return "copied"


def merge(sources: list[Path], out_dir: Path) -> dict:
    report: dict = {"sources": [str(s) for s in sources], "rows": 0, "by_split_label": {},
                    "link_modes": {}, "missing": [], "duplicate": 0}
    seen: dict[str, dict] = {}
    for source in sources:
        manifest = source / "manifest_marker_roi.jsonl"
        if not manifest.is_file():
            raise SystemExit(f"缺少清单: {manifest}")
        for row in _read_jsonl(manifest):
            original = Path(row["frame_path"])
            if not original.is_file():
                report["missing"].append(str(original))
                continue
            destination = out_dir / row["split"] / row["label"] / original.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            mode = _hardlink(original, destination)
            report["link_modes"][mode] = report["link_modes"].get(mode, 0) + 1
            if mode == "exists":
                report["duplicate"] += 1
            key = str(destination)
            new_row = dict(row)
            new_row["frame_path"] = key
            if key in seen:
                report["duplicate"] += 1
            seen[key] = new_row
            bucket = f"{row['split']}/{row['label']}"
            report["by_split_label"][bucket] = report["by_split_label"].get(bucket, 0) + 1

    rows = sorted(seen.values(), key=lambda item: item["frame_path"])
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest_marker_roi.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    # 教师整帧模型在 ROI 裁图上完全 OOD（p_replay≈0.002）→ 蒸馏必须全关
    (out_dir / "manifest_marker_roi_nodistill.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "frame_path": row["frame_path"],
                    "label": row["label"],
                    "hard_weight": 1.0,
                    "hard_distill_weight": 0.0,
                    "reason": "标记支路：教师整帧模型在 ROI 裁图上完全 OOD → 关闭蒸馏",
                },
                ensure_ascii=False,
            )
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    report["rows"] = len(rows)
    report["train_positive_ratio"] = round(
        report["by_split_label"].get("train/replay", 0)
        / max(sum(v for k, v in report["by_split_label"].items() if k.startswith("train/")), 1),
        4,
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", nargs="+", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    sources = [s.expanduser().resolve() for s in args.source]
    for source in sources:
        if not source.is_dir():
            print(f"!! 源目录不存在: {source}", file=sys.stderr)
            return 2
    report = merge(sources, args.out_dir.expanduser().resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
