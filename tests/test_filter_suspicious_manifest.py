from __future__ import annotations

from scripts.valorant_vision.filter_suspicious_manifest import filter_suspicious_rows


def test_selects_low_confidence_and_isolated_runs() -> None:
    rows = [
        {"video_id": "v", "timestamp_sec": 0, "label": "combat", "coarse_confidence": 0.95},
        {"video_id": "v", "timestamp_sec": 4, "label": "combat", "coarse_confidence": 0.60},
        {"video_id": "v", "timestamp_sec": 8, "label": "replay", "coarse_confidence": 0.99},
        {"video_id": "v", "timestamp_sec": 12, "label": "combat", "coarse_confidence": 0.95},
        {"video_id": "v", "timestamp_sec": 16, "label": "combat", "coarse_confidence": 0.95},
    ]

    selected = filter_suspicious_rows(rows)

    assert [row["timestamp_sec"] for row in selected] == [4, 8]
    assert selected[0]["review_reason"] == "low_confidence"
    assert selected[1]["review_reason"] == "isolated_label_transition"


def test_runs_are_independent_per_video() -> None:
    rows = [
        {"video_id": "a", "timestamp_sec": 0, "label": "combat", "coarse_confidence": 0.95},
        {"video_id": "a", "timestamp_sec": 4, "label": "combat", "coarse_confidence": 0.95},
        {"video_id": "b", "timestamp_sec": 0, "label": "combat", "coarse_confidence": 0.95},
    ]

    selected = filter_suspicious_rows(rows)

    assert [row["video_id"] for row in selected] == ["b"]
