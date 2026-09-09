from __future__ import annotations

from scripts.valorant_vision.rebuild_source_separated_datasets import (
    _assign_split,
    _extract_timestamp,
    _is_broadcast,
)
from scripts.valorant_vision.extract_frames import ManifestRow, run_extraction


def test_hard_mined_timestamp_uses_original_t_tag() -> None:
    assert _extract_timestamp("yuezi_keep_combat_t0262_x4") == 262.0
    assert _extract_timestamp("pov_fish_part2_t0458_buy") == 458.0


def test_millisecond_timestamp_is_converted_to_seconds() -> None:
    assert _extract_timestamp("ann_broadcast_yuezi_20260720_202557_236000") == 236.0


def test_pakki_blind_frames_are_broadcast_domain() -> None:
    assert _is_broadcast("pakki_combat_000001") is True
    assert _is_broadcast("ann_pov_ling_20260720_134749_1012000") is False


def test_broadcast_session_never_splits_by_timestamp() -> None:
    assert _assign_split("broadcast", "binggan_20260731_181220") == "val"
    assert _assign_split("broadcast", "binggan_20260731_181220") == "val"


def test_source_holdout_sessions_are_stable() -> None:
    assert _assign_split("broadcast", "pakki_tournament") == "test"
    assert _assign_split("pov", "fish_live") == "test"
    assert _assign_split("pov", "tangqihua_20260721_141301") == "val"


def test_frame_only_manifest_is_accepted_and_copied(tmp_path) -> None:
    source = tmp_path / "source.jpg"
    source.write_bytes(b"jpeg-placeholder")
    row = ManifestRow.from_dict(
        {
            "video_id": "frame_only_1",
            "video_path": None,
            "frame_path": str(source),
            "timestamp_sec": 1.0,
            "label": "combat",
            "split": "train",
            "source_type": "pov",
            "session_id": "session_1",
        }
    )

    planned, extracted, skipped = run_extraction(
        [row],
        tmp_path / "output",
        ffmpeg="ffmpeg-not-used",
        timeout_sec=1.0,
        dry_run=False,
        skip_existing=False,
    )

    assert (planned, extracted, skipped) == (1, 1, 0)
    assert (tmp_path / "output" / "train" / "combat" / "frame_only_1_1000.jpg").read_bytes() == source.read_bytes()


def test_original_video_manifest_remains_compatible() -> None:
    row = ManifestRow.from_dict(
        {
            "video_id": "video_1",
            "video_path": "D:/recordings/video_1.mp4",
            "timestamp_sec": 2.5,
            "label": "buy",
            "split": "val",
            "source_type": "broadcast",
            "session_id": "session_1",
        }
    )
    assert row.video_path == "D:/recordings/video_1.mp4"
    assert row.frame_path is None
