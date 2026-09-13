"""草稿前「孤儿录像定稿自愈」回归测试（2026-09-12 现场）。

现场：直播源 broken pipe 让录制 ffmpeg 死在写盘中途 ⇒ 收尾改名没跑 ⇒ 录像停在
``*_录制中.mp4`` ⇒ 草稿守卫（只按文件名/房间路径判"仍在录制"）三次拒绝生成草稿
（``recording_not_finalized``），用户只能重启应用（启动自愈才改名）。

修复：``jianying_handlers._heal_rooms_in_progress_recording`` 在生成草稿前跑同一个
自愈函数，并刷新房间的 ``record_output_path``（守卫与草稿都读它）。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "python-backend"
for extra in (ROOT, BACKEND):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import handlers.jianying_handlers as jh  # noqa: E402


class _Room:
    def __init__(self, path: str, *, recording: bool = False) -> None:
        self.record_output_path = path
        self.is_recording = recording
        self.output_bundle_dir = ""
        self.reconnect_output_dir = ""


class _Manager:
    def __init__(self, rooms: dict[str, _Room]) -> None:
        self._rooms = rooms

    def get_room(self, room_id: str):
        return self._rooms.get(room_id)

    def list_rooms(self):
        return list(self._rooms.values())


def _make_orphan(tmp_path: Path, *, stem: str = "2026-09-12_15-16-24",
                 idle_sec: float = 600.0) -> Path:
    """造一个「已停写但仍叫 _录制中」的录像 + 两个 sidecar（mtime 已陈旧）。"""
    video = tmp_path / f"{stem}_录制中.mp4"
    video.write_bytes(b"0" * 32)
    for suffix in (".analysis.json", ".finalization.json"):
        (tmp_path / f"{stem}_录制中{suffix}").write_text("{}", encoding="utf-8")
    stale = time.time() - idle_sec
    for p in tmp_path.glob(f"{stem}_录制中*"):
        os.utime(p, (stale, stale))
    return video


def test_heal_renames_orphan_and_refreshes_room_path(tmp_path: Path) -> None:
    """孤儿录像必须被定稿改名，且房间路径同步刷新（否则守卫仍按旧名拦截）。"""
    video = _make_orphan(tmp_path)
    room = _Room(str(video))
    manager = _Manager({"room-1": room})

    healed = jh._heal_rooms_in_progress_recording(manager, ["room-1"])

    assert len(healed) == 1
    new_path = healed[0]
    assert "_录制中" not in new_path and "_至_" in new_path
    assert Path(new_path).is_file()
    assert not video.exists()
    # sidecar 随录像改名，保持 {stem}.* 契约
    assert Path(new_path).with_suffix(".analysis.json").is_file()
    assert Path(new_path).with_suffix(".finalization.json").is_file()
    # 房间路径刷新 ⇒ 守卫（"_录制中" in basename）放行
    assert room.record_output_path == new_path
    assert "_录制中" not in os.path.basename(room.record_output_path)


def test_heal_skips_room_still_recording(tmp_path: Path) -> None:
    """正在录的房间绝不改名（自愈的 active_paths 契约）。"""
    video = _make_orphan(tmp_path)
    room = _Room(str(video), recording=True)
    manager = _Manager({"room-1": room})

    assert jh._heal_rooms_in_progress_recording(manager, ["room-1"]) == []
    assert video.is_file()
    assert room.record_output_path == str(video)


def test_heal_skips_freshly_written_in_progress_file(tmp_path: Path) -> None:
    """刚写过的 `_录制中` 文件不是孤儿（避免把正在写的文件误判）。"""
    video = _make_orphan(tmp_path, idle_sec=1.0)
    room = _Room(str(video))
    manager = _Manager({"room-1": room})

    assert jh._heal_rooms_in_progress_recording(manager, ["room-1"]) == []
    assert video.is_file()


def test_heal_is_idempotent(tmp_path: Path) -> None:
    """已定稿的录像再跑一次自愈不得再改（幂等，不产生重复副本）。"""
    video = _make_orphan(tmp_path)
    room = _Room(str(video))
    manager = _Manager({"room-1": room})
    first = jh._heal_rooms_in_progress_recording(manager, ["room-1"])
    assert len(first) == 1

    second = jh._heal_rooms_in_progress_recording(manager, ["room-1"])
    assert second == []
    assert Path(first[0]).is_file()
    assert len(list(tmp_path.glob("*.mp4"))) == 1  # 不留副本


def test_collect_draft_inputs_heals_before_in_progress_guard() -> None:
    """接线守门：自愈必须在 in_progress 守卫之前调用（否则改了名也照样被拒）。"""
    src = (BACKEND / "handlers/jianying_handlers.py").read_text(encoding="utf-8")
    heal_at = src.index("_heal_rooms_in_progress_recording(manager, list(room_ids_resolved))")
    guard_at = src.index('"_录制中" in path_name')
    assert heal_at < guard_at, "自愈必须早于 in_progress 守卫"
