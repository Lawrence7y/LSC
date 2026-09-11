"""录制/切片目录布局：按主播复用文件夹、对齐后归组、录像按时间至时间命名。"""
from __future__ import annotations

from datetime import datetime
import os
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


# ── P1（2026-09-10）：停录定稿改名必须幂等，绝不累积副本 ──────────────────


def _mkdirs(tmp_path: Path):
    src_dir = tmp_path / "EDG夺冠回顾"
    src_dir.mkdir()
    src = src_dir / "2026-09-10_18-05-24_录制中.mp4"
    src.write_bytes(b"video-bytes")
    return src, src_dir


def test_finalize_recording_file_busy_source_leaves_no_copy(tmp_path, monkeypatch):
    """源被占用时必须抛错且**不得留下任何副本**（P1 根因守卫）。

    旧实现用 shutil.move：os.rename 被拒 → 回退 copy2（成功）+ unlink（失败）→
    抛错，但复制出来的目标文件留在磁盘上；调用方重试一次就再多一份完整副本。
    """
    import errno

    import lsc.core.recording_layout as layout

    src, _ = _mkdirs(tmp_path)
    dest_dir = tmp_path / "out"
    dest_dir.mkdir()

    def _busy(*_a, **_k):
        raise PermissionError(errno.EACCES, "file is busy (simulated)")

    monkeypatch.setattr(layout.os, "replace", _busy)
    try:
        layout.finalize_recording_file(
            str(src),
            started_at=datetime(2026, 9, 10, 18, 5, 24),
            ended_at=datetime(2026, 9, 10, 18, 29, 20),
            dest_dir=str(dest_dir),
        )
        raise AssertionError("源被占用时应当抛出 OSError")
    except PermissionError:
        pass

    assert list(dest_dir.iterdir()) == [], "源被占用时不得留下任何副本"
    assert src.is_file(), "源文件必须原样保留"


def test_finalize_recording_file_cross_device_copies_then_removes_source(tmp_path, monkeypatch):
    """确属跨盘时才复制+删源（EXDEV 分支）。"""
    import errno

    import lsc.core.recording_layout as layout

    src, _ = _mkdirs(tmp_path)
    dest_dir = tmp_path / "out"
    dest_dir.mkdir()

    def _exdev(*_a, **_k):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(layout.os, "replace", _exdev)
    result = layout.finalize_recording_file(
        str(src),
        started_at=datetime(2026, 9, 10, 18, 5, 24),
        ended_at=datetime(2026, 9, 10, 18, 29, 20),
        dest_dir=str(dest_dir),
    )
    assert Path(result).is_file()
    assert not src.exists(), "跨盘复制成功后必须删源"


def test_finalize_recording_file_cross_device_unlink_failure_rolls_back(tmp_path, monkeypatch):
    """跨盘复制成功但删源失败 → 必须回滚目标，否则重试会累积副本。"""
    import errno

    import lsc.core.recording_layout as layout

    src, _ = _mkdirs(tmp_path)
    dest_dir = tmp_path / "out"
    dest_dir.mkdir()

    monkeypatch.setattr(layout.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EXDEV, "x")))
    real_unlink = layout.os.unlink
    state = {"n": 0}

    def _unlink_flaky(path, *a, **k):
        state["n"] += 1
        if state["n"] == 1:
            # 只有「删源」被占用挡住；回滚删目标（无人占用）应当能成功。
            raise PermissionError("busy: source locked")
        return real_unlink(path, *a, **k)

    monkeypatch.setattr(layout.os, "unlink", _unlink_flaky)

    try:
        layout.finalize_recording_file(
            str(src),
            started_at=datetime(2026, 9, 10, 18, 5, 24),
            ended_at=datetime(2026, 9, 10, 18, 29, 20),
            dest_dir=str(dest_dir),
        )
        raise AssertionError("删源失败时应当抛出")
    except PermissionError:
        pass

    assert list(dest_dir.iterdir()) == [], "删源失败后目标必须被回滚"
    assert src.is_file()


def test_finalize_recording_file_busy_retry_converges_to_single_file(tmp_path, monkeypatch):
    """模拟"先占用后释放"：重试后只能得到**一份**定稿文件，不得有副本。"""
    import errno

    import lsc.core.recording_layout as layout

    src, _ = _mkdirs(tmp_path)
    dest_dir = tmp_path / "out"
    dest_dir.mkdir()

    calls = {"n": 0}
    real_replace = layout.os.replace

    def _flaky(s, d):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(errno.EACCES, "busy")
        return real_replace(s, d)

    monkeypatch.setattr(layout.os, "replace", _flaky)

    started = datetime(2026, 9, 10, 18, 5, 24)
    # 第 1 次失败
    try:
        layout.finalize_recording_file(str(src), started_at=started,
                                       ended_at=datetime(2026, 9, 10, 18, 29, 20),
                                       dest_dir=str(dest_dir))
    except PermissionError:
        pass
    # 第 2 次成功（注意结束时刻不同，模拟原先因 datetime.now() 产生的不同文件名）
    result = layout.finalize_recording_file(str(src), started_at=started,
                                            ended_at=datetime(2026, 9, 10, 18, 29, 21),
                                            dest_dir=str(dest_dir))

    files = sorted(dest_dir.iterdir())
    assert len(files) == 1, f"重试后不得留下副本，实际: {[f.name for f in files]}"
    assert Path(result) == files[0]
    assert not src.exists()


# ── 命名分裂修复（2026-09-10）：sidecar 必须随录像改名 ────────────────────


def test_finalize_recording_file_moves_sidecars_along(tmp_path: Path) -> None:
    """定稿改名后，analysis / finalization sidecar 必须跟到新名（{stem} 契约）。"""
    solo = tmp_path / "EDG夺冠回顾"
    solo.mkdir()
    src = solo / "2026-09-10_18-05-24_录制中.mp4"
    src.write_bytes(b"video")
    (solo / "2026-09-10_18-05-24_录制中.analysis.json").write_text('{"highlights":[]}', encoding="utf-8")
    (solo / "2026-09-10_18-05-24_录制中.finalization.json").write_text('{"phase":"completed"}', encoding="utf-8")

    result = finalize_recording_file(
        str(src),
        started_at=datetime(2026, 9, 10, 18, 5, 24),
        ended_at=datetime(2026, 9, 10, 18, 29, 20),
        dest_dir=str(solo),
    )
    new_stem = Path(result).stem
    assert Path(result).is_file()
    assert (solo / f"{new_stem}.analysis.json").is_file(), "分析 sidecar 未随录像改名"
    assert (solo / f"{new_stem}.finalization.json").is_file(), "收尾 sidecar 未随录像改名"
    # 旧名不得残留（否则恢复扫描会同时读到两份）
    assert not (solo / "2026-09-10_18-05-24_录制中.analysis.json").exists()
    assert not (solo / "2026-09-10_18-05-24_录制中.finalization.json").exists()


def test_finalize_recording_file_without_sidecars_is_fine(tmp_path: Path) -> None:
    """没有 sidecar 时改名照常成功（不得因缺失而失败）。"""
    solo = tmp_path / "EDG夺冠回顾"
    solo.mkdir()
    src = solo / "2026-09-10_18-05-24_录制中.mp4"
    src.write_bytes(b"video")
    result = finalize_recording_file(
        str(src),
        started_at=datetime(2026, 9, 10, 18, 5, 24),
        ended_at=datetime(2026, 9, 10, 18, 29, 20),
        dest_dir=str(solo),
    )
    assert Path(result).is_file()
    assert not src.exists()


def test_move_recording_sidecars_failure_is_non_fatal(tmp_path, monkeypatch):
    """sidecar 搬运失败只能告警，绝不能影响录像本身的定稿结果。"""
    import lsc.core.recording_layout as layout

    solo = tmp_path / "EDG夺冠回顾"
    solo.mkdir()
    src = solo / "2026-09-10_18-05-24_录制中.mp4"
    src.write_bytes(b"video")
    (solo / "2026-09-10_18-05-24_录制中.analysis.json").write_text("{}", encoding="utf-8")

    real_replace = layout.os.replace

    def _flaky(s, d):
        if str(s).endswith(".analysis.json"):
            raise PermissionError("sidecar busy (simulated)")
        return real_replace(s, d)

    monkeypatch.setattr(layout.os, "replace", _flaky)
    result = layout.finalize_recording_file(
        str(src),
        started_at=datetime(2026, 9, 10, 18, 5, 24),
        ended_at=datetime(2026, 9, 10, 18, 29, 20),
        dest_dir=str(solo),
    )
    assert Path(result).is_file(), "sidecar 失败不得影响录像定稿"
    assert not src.exists()
    # sidecar 仍留在旧名（可被后续收尾扫描重建），但录像名已经是最终名
    assert (solo / "2026-09-10_18-05-24_录制中.analysis.json").is_file()


def test_move_recording_sidecars_skips_when_stem_unchanged(tmp_path):
    """stem 未变（同路径）时不误搬。"""
    import lsc.core.recording_layout as layout

    same = tmp_path / "a.mp4"
    same.write_bytes(b"x")
    assert layout.move_recording_sidecars(str(same), str(same)) == []


# ── 孤儿录制自愈（2026-09-11）───────────────────────────────────────────────
# 现场：录制 07:41:32→07:52:47（history 已写结束时间），但收尾改名失败 → 文件名仍是
# `_录制中`；而剪映草稿守卫只按文件名判"仍在录制" → 这段录像**永久导不出草稿**，
# 且 is_recording 已是 False，用户也无法靠"停止录制"再触发一次。


def _orphan(directory, stamp="2026-09-11_07-41-32", *, mtime=None, suffix="_录制中.mp4"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp}{suffix}"
    path.write_bytes(b"mp4")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_stale_in_progress_finds_idle_orphan_only(tmp_path) -> None:
    from datetime import datetime

    from lsc.core.recording_layout import stale_in_progress_recordings

    old = (datetime(2026, 9, 11, 8, 0, 0)).timestamp()
    orphan = _orphan(tmp_path, mtime=old - 600)          # 已停写 10 分钟 → 孤儿
    fresh = _orphan(tmp_path, stamp="2026-09-11_08-30-00", mtime=old - 5)  # 刚在写 → 不算
    finished = tmp_path / "2026-09-11_06-00-00_至_2026-09-11_06-10-00.mp4"
    finished.write_bytes(b"mp4")                          # 已定稿 → 不算

    found = stale_in_progress_recordings([str(tmp_path)], now=datetime(2026, 9, 11, 8, 0, 0))
    paths = [p for p, _, _ in found]
    assert paths == [orphan]
    _, started, ended = found[0]
    assert started.strftime("%Y-%m-%d_%H-%M-%S") == "2026-09-11_07-41-32"  # 文件名解析
    assert abs(ended.timestamp() - (old - 600)) < 1.0                     # 结束时间取 mtime

    # 被排除的活跃路径不算孤儿
    assert stale_in_progress_recordings(
        [str(tmp_path)], active_paths=[str(orphan)], now=datetime(2026, 9, 11, 8, 0, 0)
    ) == []
    assert fresh.is_file() and finished.is_file()


def test_heal_stale_in_progress_renames_and_moves_sidecars(tmp_path) -> None:
    from datetime import datetime

    from lsc.core.recording_layout import heal_stale_in_progress_recordings

    orphan = _orphan(tmp_path, mtime=datetime(2026, 9, 11, 7, 52, 47).timestamp())
    (tmp_path / "2026-09-11_07-41-32_录制中.analysis.json").write_text("{}", encoding="utf-8")
    (tmp_path / "2026-09-11_07-41-32_录制中.finalization.json").write_text("{}", encoding="utf-8")

    healed = heal_stale_in_progress_recordings(
        [str(tmp_path)], now=datetime(2026, 9, 11, 8, 0, 0)
    )
    assert len(healed) == 1
    new_path = Path(healed[0])
    assert new_path.name == "2026-09-11_07-41-32_至_2026-09-11_07-52-47.mp4"
    assert new_path.is_file() and not orphan.exists()
    # sidecar 必须同步改名，否则按最终录像名查会落空
    assert (tmp_path / "2026-09-11_07-41-32_至_2026-09-11_07-52-47.analysis.json").is_file()
    assert (tmp_path / "2026-09-11_07-41-32_至_2026-09-11_07-52-47.finalization.json").is_file()
    # 幂等：再跑一次已经没有孤儿了（也证明改名后不再被误判）
    assert heal_stale_in_progress_recordings(
        [str(tmp_path)], now=datetime(2026, 9, 11, 8, 0, 0)
    ) == []


def test_heal_tolerates_missing_dirs_and_bad_names(tmp_path) -> None:
    from datetime import datetime

    from lsc.core.recording_layout import heal_stale_in_progress_recordings

    _orphan(tmp_path, stamp="不是时间戳", mtime=datetime(2026, 9, 11, 7, 0, 0).timestamp())
    healed = heal_stale_in_progress_recordings(
        [str(tmp_path), str(tmp_path / "不存在")], now=datetime(2026, 9, 11, 8, 0, 0)
    )
    assert len(healed) == 1  # 名字解析失败也照样收尾（回退用 mtime）


def test_startup_heals_orphan_recordings() -> None:
    """源码守卫：启动期必须调用自愈，否则这类录像永远导不出草稿。"""
    from pathlib import Path as _Path

    source = (_Path(__file__).resolve().parents[1] / "python-backend/main.py").read_text(encoding="utf-8")
    assert "_heal_orphan_recordings(self.manager)" in source
    assert "heal_stale_in_progress_recordings" in source
    # 自愈失败不得拦住启动
    call_site = source.split("heal_orphan_recordings(self.manager)", 1)[1][:400]
    assert "except Exception" in call_site
