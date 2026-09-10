from __future__ import annotations

import json

from scripts.valorant_vision.build_retrain_manifests import (
    _base_name,
    build,
    CLASSES,
)


def _make_dataset(root) -> None:
    """造一个最小数据集：4 帧同源（1 基帧 + 3 增强副本）+ 1 帧别的源。"""
    layout = {
        "train/non_game": ["a_bc_bc_broadcast.jpg", "a_bc_rarex0_bc_broadcast.jpg", "zz_bc_broadcast.jpg"],
        "train/replay": ["a_bc_replay_boost0_bc_broadcast.jpg"],
        "val/non_game": ["v_bc_broadcast.jpg"],
        "val/replay": ["v_bc_rarex1_bc_broadcast.jpg"],
    }
    for rel, names in layout.items():
        directory = root / rel
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            (directory / name).write_bytes(name.encode())


def test_base_name_strips_oversample_tags_only() -> None:
    """`rarex/replay_boost/hardx/hardpov/low` 是增强/过采样副本标签，不是内容证据。"""
    assert _base_name("s_1000_bc_rarex0_bc_broadcast.jpg") == "s_1000_bc_bc_broadcast"
    assert _base_name("s_1000_bc_replay_boost2_bc_broadcast.jpg") == "s_1000_bc_bc_broadcast"
    assert _base_name("s_1000_bc_replay_boost2_rarex3_bc_broadcast.jpg") == "s_1000_bc_bc_broadcast"
    assert _base_name("hardx4_x_replay.jpg") == "x_replay"
    assert _base_name("hardpov0_x.jpg") == "x"
    assert _base_name("low_x.jpg") == "x"
    assert _base_name("rarex0_replay_boost1_x.jpg") == "x"
    # 不带标签的名字原样保留
    assert _base_name("plain_name.jpg") == "plain_name"


def test_build_emits_uniform_baseline_and_zero_distill_relabel_manifest(tmp_path) -> None:
    data_dir = tmp_path / "data"
    _make_dataset(data_dir)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    rollback = tmp_path / "rollback.jsonl"
    moved = data_dir / "train/replay/a_bc_replay_boost0_bc_broadcast.jpg"
    was = data_dir / "train/non_game/a_bc_broadcast.jpg"
    rollback.write_text(
        json.dumps({"from": str(was), "to": str(moved), "split": "train", "was": "non_game"}) + "\n",
        encoding="utf-8",
    )

    report = build(
        data_dir=data_dir,
        rollback=rollback,
        teacher_dir=None,
        out_dir=out_dir,
        hard_weight=4.0,
        date_tag="unittest",
    )

    baseline = [
        json.loads(line)
        for line in (out_dir / "manifest_broadcast_retrain_unittest.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    hard = [
        json.loads(line)
        for line in (out_dir / "manifest_broadcast_relabel_hard_unittest.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert report["baseline_rows"] == len(baseline) == 6  # train 4 + val 2
    assert {row["label"] for row in baseline} <= set(CLASSES)
    # 基线清单全为等权伪标（无 label_source / confidence 时脚本按 0.05 处理）
    assert all(row["label_source"] == "coarse_model" for row in baseline)
    assert all(row["coarse_confidence"] == 0.0 for row in baseline)

    assert report["relabel_rows"] == len(hard) == 1
    assert report["relabel_missing_files"] == []
    # 关键：蒸馏必须关掉，否则教师会按旧标签把这帧拉回去
    assert hard[0]["hard_distill_weight"] == 0.0
    assert hard[0]["hard_weight"] == 4.0
    assert hard[0]["label"] == "replay"
    assert hard[0]["previous_label"] == "non_game"
    assert hard[0]["frame_path"] == str(moved)

    # 权重口径：唯一源帧与实际 CE 权重占比都要报出来
    assert report["relabel_unique_sources"] == 1
    assert report["train_unique_sources"] == 2  # a_bc_bc_broadcast / zz_bc_broadcast
    assert report["train_frames"] == 4
    assert report["relabel_train_rows"] == 1
    # 分母只含 train（val 的 2 帧不参与 CE）
    assert report["ce_weight_share_of_relabeled"] == round(1 * 0.05 * 4 / (3 * 0.05 + 1 * 0.05 * 4), 4)


def test_build_reports_weight_share_for_equal_weight_case(tmp_path) -> None:
    """k=1（等权）时占比不能算成 0——按"成员身份"统计而不是"权重 > 基准"。"""
    data_dir = tmp_path / "data"
    _make_dataset(data_dir)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    rollback = tmp_path / "rollback.jsonl"
    rollback.write_text(
        json.dumps(
            {
                "from": str(data_dir / "train/non_game/a_bc_bc_broadcast.jpg"),
                "to": str(data_dir / "train/replay/a_bc_replay_boost0_bc_broadcast.jpg"),
                "split": "train",
                "was": "non_game",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = build(
        data_dir=data_dir, rollback=rollback, teacher_dir=None,
        out_dir=out_dir, hard_weight=1.0, date_tag="unittest_k1",
    )
    assert report["ce_weight_share_of_relabeled"] == 0.25  # 1 / 4 个 train 帧
    assert report["baseline_rows_boosted"] == 1


def test_build_without_rollback_yields_no_relabel_rows(tmp_path) -> None:
    data_dir = tmp_path / "data"
    _make_dataset(data_dir)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    report = build(
        data_dir=data_dir, rollback=tmp_path / "missing.jsonl", teacher_dir=None,
        out_dir=out_dir, hard_weight=1.0, date_tag="unittest_none",
    )
    assert report["relabel_rows"] == 0
    assert (out_dir / "manifest_broadcast_relabel_hard_unittest_none.jsonl").read_text(encoding="utf-8") == ""
