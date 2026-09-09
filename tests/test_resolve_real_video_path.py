"""Test resolve_real_video_path helper."""
from pathlib import Path
from lsc.utils.helpers import resolve_real_video_path


def test_resolve_real_video_path_returns_existing(tmp_path: Path):
    file_path = tmp_path / "normal_video.mp4"
    file_path.write_bytes(b"data")
    assert resolve_real_video_path(str(file_path)) == str(file_path)


def test_resolve_real_video_path_redirects_recording_rename(tmp_path: Path):
    old_name = "2026-09-05_09-09-38_录制中.mp4"
    new_name = "2026-09-05_09-09-38_至_2026-09-05_09-36-34.mp4"

    new_file = tmp_path / new_name
    new_file.write_bytes(b"final recording data")

    old_file_str = str(tmp_path / old_name)
    resolved = resolve_real_video_path(old_file_str)

    assert resolved == str(new_file)
