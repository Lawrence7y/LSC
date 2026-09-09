from __future__ import annotations

from pathlib import Path

import pytest

from scripts.recover_recording import build_report, resolve_output_path


def test_recovery_output_never_overwrites_input(tmp_path: Path) -> None:
    source = tmp_path / "recording_录制中.mp4"
    source.write_bytes(b"source")

    assert resolve_output_path(str(source)).name == "recording_录制中.repaired.mp4"
    with pytest.raises(ValueError):
        resolve_output_path(str(source), str(source))


def test_report_contains_hash_and_validation_without_credentials(tmp_path: Path) -> None:
    source = tmp_path / "recording.mp4"
    source.write_bytes(b"fake")
    report = build_report(
        input_path=source,
        output_path=None,
        input_valid=False,
        input_error="invalid header",
        output_valid=None,
    )

    assert report["input_exists"] is True
    assert len(report["input_sha256"]) == 64
    assert "Cookie" not in json_text(report)


def json_text(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)

