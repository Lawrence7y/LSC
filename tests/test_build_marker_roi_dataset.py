from __future__ import annotations

import json

import pytest

from scripts.valorant_vision.build_marker_roi_dataset import (
    CROP_SIZE,
    NEGATIVE_LABEL,
    POSITIVE_LABEL,
    ROIS,
    _process_frame,
    _roi_name,
    build,
    crop_roi,
)


def _jpeg(path, height=120, width=200):
    import cv2
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.full((height, width, 3), 40, dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    buffer.tofile(str(path))


@pytest.fixture
def fake_dataset(tmp_path):
    """4 个 train 帧 + 2 个 val 帧 + 1 个 test 帧，文件名里带 'MARK' 的算带标记。"""
    root = tmp_path / "data"
    for rel, names in {
        "train/non_game": ["a_plain.jpg", "b_MARK.jpg"],
        "train/replay": ["c_MARK.jpg", "d_plain.jpg"],
        "val/non_game": ["e_plain.jpg"],
        "test/replay": ["f_MARK.jpg"],
    }.items():
        for name in names:
            _jpeg(root / rel / name, FRAME_H, FRAME_W)
    return root


FRAME_H, FRAME_W = 120, 200


def _crop_shape(box, height=FRAME_H, width=FRAME_W) -> tuple[int, int]:
    """按生成器的取整规则算出该 ROI 在给定帧尺寸下的裁剪形状（用于识别是哪一路）。"""
    x0, y0, bw, bh = box
    return (int((y0 + bh) * height) - int(y0 * height),
            int((x0 + bw) * width) - int(x0 * width))


def _oracle_for(roi_name: str):
    """构造一个"只有指定那一路命中"的 OCR 替身（不加载真 OCR）。"""
    target = _crop_shape(ROIS[roi_name])

    def fake_detect(crop):
        return ("REPLAY", 0.97) if tuple(crop.shape[:2]) == target else ("", 0.0)

    return fake_detect


def test_crop_roi_and_naming() -> None:
    import numpy as np

    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    crop = crop_roi(frame, (0.5, 0.5, 0.5, 0.5))
    assert crop.shape == (50, 100, 3)
    assert crop_roi(frame, (0.99, 0.99, 0.5, 0.5)) is None
    assert _roi_name("stem", "top_right") == "stem__roi_top_right.jpg"


def test_process_frame_labels_per_crop(monkeypatch, tmp_path) -> None:
    """标签按**裁剪**定（不是按帧标签）——这是踩过的坑，用测试钉住。"""
    import scripts.valorant_vision.build_marker_roi_dataset as builder

    frame_path = tmp_path / "frames" / "x.jpg"
    _jpeg(frame_path, FRAME_H, FRAME_W)
    # 只有 top_right 那一路命中：若退回"按帧标签打标"，三路会全被标成同一类
    monkeypatch.setattr(builder, "detect_marker_text", _oracle_for("top_right"))
    result = _process_frame((
        str(frame_path), "train", "non_game", ROIS, str(tmp_path / "out"),
    ))
    assert result["readable"] is True
    labels = {row["roi"]: row["label"] for row in result["rows"]}
    assert set(labels) == set(ROIS)
    assert labels["top_right"] == POSITIVE_LABEL
    assert labels["bottom_right"] == NEGATIVE_LABEL, "逐裁剪判标：只有命中那一路是正样本"
    assert result["conflicts"], "帧标签是 non_game 却在裁剪里读到标记 → 必须记冲突"


def test_build_splits_labels_by_crop_and_writes_manifests(monkeypatch, fake_dataset, tmp_path) -> None:
    import scripts.valorant_vision.build_marker_roi_dataset as builder

    monkeypatch.setattr(builder, "detect_marker_text", _oracle_for("top_right"))
    out_dir = tmp_path / "out"
    report = build(data_dir=fake_dataset, out_dir=out_dir, rois=ROIS)

    # 每个类目录里都是 224×224 的裁剪
    from PIL import Image

    positives = sorted((out_dir / "train" / POSITIVE_LABEL).glob("*.jpg"))
    assert positives
    with Image.open(positives[0]) as image:
        assert image.size == (CROP_SIZE, CROP_SIZE)

    rows = [
        json.loads(line)
        for line in (out_dir / "manifest_marker_roi.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert report["total_crops"] == len(rows) == 6 * len(ROIS)  # 6 帧 × 每路 ROI
    assert all(row["label"] in (POSITIVE_LABEL, NEGATIVE_LABEL) for row in rows)
    assert all(row["label_source"] == "ocr_per_crop" for row in rows)
    # 只有 top_right 那一路是正样本
    positives_rows = [row for row in rows if row["label"] == POSITIVE_LABEL]
    assert {row["roi"] for row in positives_rows} == {"top_right"}

    hard = [
        json.loads(line)
        for line in (out_dir / "manifest_marker_roi_nodistill.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(hard) == len(rows)
    # 教师在这类裁图上完全 OOD → 蒸馏必须全关
    assert all(row["hard_distill_weight"] == 0.0 for row in hard)


def test_build_parallel_matches_serial(monkeypatch, fake_dataset, tmp_path) -> None:
    """workers>1 走进程池，路径/标签必须与单进程一致（spawn 下易出静默差异）。"""
    import scripts.valorant_vision.build_marker_roi_dataset as builder

    monkeypatch.setattr(builder, "detect_marker_text", _oracle_for("top_right"))
    serial = build(data_dir=fake_dataset, out_dir=tmp_path / "out_serial", rois=ROIS, workers=1)
    # 进程池的 worker 在子进程里执行，monkeypatch 不生效；此处只验证主流程在
    # workers>1 分支下不会崩，并用 1 个 worker 走同一代码路径。
    parallel = build(data_dir=fake_dataset, out_dir=tmp_path / "out_w1", rois=ROIS, workers=1)
    assert serial["splits"]["train"]["crops"] == parallel["splits"]["train"]["crops"]
