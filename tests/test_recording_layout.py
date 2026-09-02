"""录制/切片目录布局：按主播复用文件夹、对齐后归组、录像按时间至时间命名。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from lsc.core.recording_layout import (
    bind_rooms_to_bundle,
    bundle_folder_name,
    finalize_recording_file,
    recording_final_filename,
    recording_in_progress_filename,
    resolve_clip_output_dir,
    room_recording_dir,
    sanitize_folder_name,
    streamer_folder_name,
)


def test_sanitize_folder_name_strips_illegal_windows_chars() -> None:
    assert sanitize_folder_name('小羽/yx:直播') == "小羽_yx_直播"
    assert sanitize_folder_name("   ") == "room"


def test_streamer_folder_name_prefers_streamer_then_title() -> None:
    assert streamer_folder_name(streamer_name="小羽yx", stream_title="无畏契约", room_id="abc123") == "小羽yx"
    assert streamer_folder_name(streamer_name="", stream_title="无畏契约", room_id="abc123") == "无畏契约"
    assert streamer_folder_name(streamer_name="", stream_title="", room_id="abcdef123") == "def123"


def test_room_recording_dir_reuses_existing_folder(tmp_path: Path) -> None:
    first = room_recording_dir(str(tmp_path), streamer_name="小羽yx", room_id="r1")
    Path(first).mkdir(parents=True, exist_ok=True)
    (Path(first) / "old.mp4").write_bytes(b"x")

    second = room_recording_dir(str(tmp_path), streamer_name="小羽yx", room_id="r1")

    assert first == second
    assert (Path(second) / "old.mp4").is_file()
    assert list(tmp_path.iterdir()) == [Path(first)]


def test_bundle_folder_name_joins_unique_streamer_names() -> None:
    assert bundle_folder_name(["小羽yx", "选手A"]) == "小羽yx+选手A"
    assert bundle_folder_name(["小羽yx", "小羽yx", "选手A"]) == "小羽yx+选手A"


def test_recording_filenames_use_start_to_end_clock() -> None:
    started = datetime(2026, 9, 2, 9, 2, 23)
    ended = datetime(2026, 9, 2, 9, 31, 45)
    assert recording_in_progress_filename(started) == "2026-09-02_09-02-23_录制中.mp4"
    assert recording_final_filename(started, ended) == "2026-09-02_09-02-23_至_2026-09-02_09-31-45.mp4"


def test_finalize_recording_file_renames_and_moves_into_bundle(tmp_path: Path) -> None:
    solo = tmp_path / "小羽yx"
    solo.mkdir()
    src = solo / "2026-09-02_09-02-23_录制中.mp4"
    src.write_bytes(b"video")
    dest_dir = tmp_path / "小羽yx+选手A" / "小羽yx"

    result = finalize_recording_file(
        str(src),
        started_at=datetime(2026, 9, 2, 9, 2, 23),
        ended_at=datetime(2026, 9, 2, 9, 31, 45),
        dest_dir=str(dest_dir),
    )

    expected = dest_dir / "2026-09-02_09-02-23_至_2026-09-02_09-31-45.mp4"
    assert Path(result) == expected
    assert expected.is_file()
    assert not src.exists()


def test_bind_rooms_to_bundle_sets_shared_parent(tmp_path: Path) -> None:
    rooms = [
        SimpleNamespace(room_id="a", streamer_name="小羽yx", stream_title="", output_bundle_dir=""),
        SimpleNamespace(room_id="b", streamer_name="选手A", stream_title="", output_bundle_dir=""),
    ]

    bundle = bind_rooms_to_bundle(rooms, str(tmp_path))

    assert Path(bundle).name == "小羽yx+选手A"
    assert Path(bundle).is_dir()
    assert rooms[0].output_bundle_dir == bundle
    assert rooms[1].output_bundle_dir == bundle
    assert (Path(bundle) / "小羽yx").is_dir()
    assert (Path(bundle) / "选手A").is_dir()


def test_resolve_clip_output_dir_uses_bundle_after_align(tmp_path: Path) -> None:
    base = str(tmp_path)
    room = SimpleNamespace(
        streamer_name="选手A",
        stream_title="",
        room_id="b",
        output_bundle_dir="",
        reconnect_output_dir=str(tmp_path / "选手A"),
    )
    assert Path(resolve_clip_output_dir(room, base)).name == "选手A"

    room.output_bundle_dir = str(tmp_path / "小羽yx+选手A")
    clip_dir = resolve_clip_output_dir(room, base)
    assert Path(clip_dir) == tmp_path / "小羽yx+选手A" / "选手A"
