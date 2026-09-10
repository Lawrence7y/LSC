from __future__ import annotations

import json

from scripts.valorant_vision.merge_marker_roi_datasets import merge


def _source(root, rows, *, name_prefix="src"):
    """造一个最小的 ROI 数据集源：真实文件 + manifest。"""
    for index, (split, label) in enumerate(rows):
        directory = root / split / label
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name_prefix}_{index}.jpg").write_bytes(b"jpg")
    manifest = root / "manifest_marker_roi.jsonl"
    manifest.write_text(
        "".join(
            json.dumps(
                {
                    "frame_path": str(root / split / label / f"{name_prefix}_{index}.jpg"),
                    "label": label,
                    "split": split,
                    "roi": "top_right",
                    "label_source": "ocr_per_crop",
                },
                ensure_ascii=False,
            )
            + "\n"
            for index, (split, label) in enumerate(rows)
        ),
        encoding="utf-8",
    )
    return root


def test_merge_links_and_rewrites_frame_path(tmp_path) -> None:
    first = _source(tmp_path / "a", [("train", "replay"), ("train", "non_game")], name_prefix="a")
    second = _source(tmp_path / "b", [("val", "replay")], name_prefix="b")
    out = tmp_path / "merged"

    report = merge([first, second], out)

    assert report["rows"] == 3
    assert report["missing"] == []
    assert report["by_split_label"] == {"train/replay": 1, "train/non_game": 1, "val/replay": 1}
    rows = [
        json.loads(line)
        for line in (out / "manifest_marker_roi.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 3
    # frame_path 必须指向合并目录（否则训练读的还是源路径，跨机失效）
    from pathlib import Path

    for row in rows:
        assert Path(row["frame_path"]).is_file()
        assert str(out) in row["frame_path"]
    hard = [
        json.loads(line)
        for line in (out / "manifest_marker_roi_nodistill.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(hard) == 3
    # 教师整帧模型在 ROI 裁图上完全 OOD → 蒸馏必须全关
    assert all(row["hard_distill_weight"] == 0.0 for row in hard)


def test_merge_reports_train_positive_ratio(tmp_path) -> None:
    source = _source(
        tmp_path / "a",
        [("train", "replay"), ("train", "non_game"), ("train", "non_game"), ("train", "non_game")],
    )
    report = merge([source], tmp_path / "merged")
    assert report["train_positive_ratio"] == 0.25


def test_merge_is_idempotent_on_rerun(tmp_path) -> None:
    """重跑不应报错，也不应把行数翻倍（同名目标视为已存在并去重）。"""
    source = _source(tmp_path / "a", [("train", "replay")])
    out = tmp_path / "merged"
    first = merge([source], out)
    second = merge([source], out)
    assert first["rows"] == second["rows"] == 1
    assert second["duplicate"] >= 1


def test_merge_records_missing_files_instead_of_crashing(tmp_path) -> None:
    source = _source(tmp_path / "a", [("train", "replay")])
    manifest = source / "manifest_marker_roi.jsonl"
    row = json.loads(manifest.read_text(encoding="utf-8").splitlines()[0])
    row["frame_path"] = str(source / "train" / "replay" / "gone.jpg")
    manifest.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    report = merge([source], tmp_path / "merged")
    assert report["rows"] == 0
    assert report["missing"] and "gone.jpg" in report["missing"][0]


def test_cli_accepts_repeated_and_grouped_sources(tmp_path) -> None:
    """两种写法都必须吃到**全部**源。

    ⚠️ 实测踩过：argparse 对非 append 的 `--source` 只保留最后一次 →
    `--source A --source B --source C` 静默只吃到 C，合并结果只剩 1/3，
    而下游只会报"训练集或验证集为空"，很难往回追。
    """
    from scripts.valorant_vision.merge_marker_roi_datasets import main

    a = _source(tmp_path / "a", [("train", "replay")], name_prefix="a")
    b = _source(tmp_path / "b", [("train", "non_game")], name_prefix="b")

    repeated = tmp_path / "out_repeated"
    assert main(["--source", str(a), "--source", str(b), "--out-dir", str(repeated)]) == 0
    rows = (repeated / "manifest_marker_roi.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 2

    grouped = tmp_path / "out_grouped"
    assert main(["--source", str(a), str(b), "--out-dir", str(grouped)]) == 0
    rows = (grouped / "manifest_marker_roi.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 2
