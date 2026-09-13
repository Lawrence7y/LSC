"""方案 A 后端契约守卫：录制镜像输出 + review 流通道删除。

契约来源：docs/plans/scheme-a-local-file-review-20260911.md §3.2
- 录制时追加第二个输出 <录制路径去扩展名>.dvr.mp4（-c copy + empty_moov fMP4），
  由 settings/LscConfig 的 dvr_mirror_enabled 控制，缺省开；镜像不可用不得牵动主输出。
- RoomSession.dvr_output_path 与 record_output_path 同生命周期，随归档改名同步改名，
  并在所有房间序列化点暴露给前端。
- 整条 review 流通道（_review_streamers / _start_recording_file_mse / review_phase /
  MSE channel='review' / RoomSession 回看字段）必须不存在，只保留声明式弃用桩。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ROOM_HANDLER_P = ROOT / "python-backend/handlers/room_handler.py"
ROOM_HANDLER = ROOM_HANDLER_P.read_text(encoding="utf-8")
RECORDING_HANDLERS = (ROOT / "python-backend/handlers/recording_handlers.py").read_text(encoding="utf-8")
CAPTURE = (ROOT / "lsc/recorder/capture.py").read_text(encoding="utf-8")
CONFIG = (ROOT / "lsc/config.py").read_text(encoding="utf-8")
SESSION = (ROOT / "lsc/core/session.py").read_text(encoding="utf-8")
ORCHESTRATOR = (ROOT / "lsc/core/orchestrator.py").read_text(encoding="utf-8")
LAYOUT = (ROOT / "lsc/core/recording_layout.py").read_text(encoding="utf-8")


# ────────────────────────── A. 录制镜像输出 ──────────────────────────


def test_capture_appends_second_dvr_output_after_primary() -> None:
    """主输出参数之后必须追加镜像输出（empty_moov fMP4，与 §3.2 参数逐字一致）。"""
    body = CAPTURE.split("def start(self, url: str", 1)[1]
    primary = body.index('frag_keyframe+faststart", output_path]')
    dvr_block = body[primary:]
    assert '"-c", "copy",' in dvr_block
    assert '"-movflags", "empty_moov+default_base_moof+frag_keyframe",' in dvr_block
    assert "dvr_path," in dvr_block
    # 镜像路径由主输出派生（去扩展名 + .dvr.mp4）
    assert "_DVR_MIRROR_SUFFIX" in CAPTURE
    assert 'dvr_path = f"{stem}{self._DVR_MIRROR_SUFFIX}"' in CAPTURE


def test_capture_dvr_mirror_is_gated_by_setting_and_writability() -> None:
    """镜像受 dvr_mirror_enabled 控制；目标不可写时返回空串（绝不牵动主输出）。"""
    body = CAPTURE.split("def _resolve_dvr_output_path", 1)[1].split("def start(self, url: str", 1)[0]
    assert 'getattr(self.config, "dvr_mirror_enabled", True)' in body
    assert "except OSError as exc:" in body
    assert "return \"\"" in body


def test_capture_exposes_dvr_output_path() -> None:
    assert "self._dvr_output_path = \"\"" in CAPTURE
    assert "def dvr_output_path(self) -> str:" in CAPTURE
    assert "self._dvr_output_path = dvr_path" in CAPTURE


def test_controller_propagates_capture_dvr_path() -> None:
    controller = (ROOT / "lsc/core/controller.py").read_text(encoding="utf-8")
    assert "self.dvr_output_path = \"\"" in controller
    assert 'str(getattr(self._capture, "dvr_output_path", "") or "")' in controller


@pytest.mark.parametrize(
    "enabled,expected_suffix",
    [(True, ".dvr.mp4"), (False, "")],
)
def test_resolve_dvr_output_path_behaviour(tmp_path, enabled, expected_suffix) -> None:
    from lsc.config import LscConfig
    from lsc.recorder.capture import StreamCapture

    cfg = LscConfig(output_path=str(tmp_path), output_dir=str(tmp_path), dvr_mirror_enabled=enabled)
    capture = StreamCapture(cfg)
    record_path = str(tmp_path / "2026-09-11_10-00-00_录制中.mp4")
    resolved = capture._resolve_dvr_output_path(record_path)
    if expected_suffix:
        assert resolved == str(tmp_path / "2026-09-11_10-00-00_录制中.dvr.mp4")
    else:
        assert resolved == ""
    # 预检不得留下残留文件
    assert not (tmp_path / "2026-09-11_10-00-00_录制中.dvr.mp4").exists()


def test_resolve_dvr_output_path_gives_up_when_target_unwritable(tmp_path) -> None:
    from lsc.config import LscConfig
    from lsc.recorder.capture import StreamCapture

    cfg = LscConfig(output_path=str(tmp_path), output_dir=str(tmp_path))
    capture = StreamCapture(cfg)
    missing_dir = tmp_path / "no_such_dir" / "x.mp4"
    assert capture._resolve_dvr_output_path(str(missing_dir)) == ""


def _fake_popen_capture(monkeypatch, tmp_path, dvr_enabled=True):
    """返回 (capture, cmd_holder)：替掉 Popen/启动探测，只检查 FFmpeg 命令行。"""
    import lsc.recorder.capture as capture_mod
    from lsc.config import LscConfig
    from lsc.recorder.capture import StreamCapture

    monkeypatch.setattr(
        "lsc.utils.process_launcher.prepare_launch",
        lambda *_a, **_k: ({}, 0, None),
    )
    monkeypatch.setattr(
        "lsc.utils.process_launcher.set_stream_nonblocking",
        lambda *_a, **_k: None,
    )
    holder: dict = {}

    class _FakeProc:
        pid = 4242
        returncode = None
        stdin = None
        stderr = None

        def poll(self):
            return None

    def _fake_popen(cmd, **_kwargs):
        holder["cmd"] = list(cmd)
        return _FakeProc()

    monkeypatch.setattr(capture_mod.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(StreamCapture, "_wait_for_startup_data", lambda self: True)
    cfg = LscConfig(
        output_path=str(tmp_path),
        output_dir=str(tmp_path),
        dvr_mirror_enabled=dvr_enabled,
    )
    return StreamCapture(cfg), holder


def test_start_command_contains_both_outputs(monkeypatch, tmp_path) -> None:
    record_path = str(tmp_path / "2026-09-11_10-00-00_录制中.mp4")
    capture, holder = _fake_popen_capture(monkeypatch, tmp_path)
    assert capture.start("https://example.com/live.m3u8", record_path, codec="copy") is True
    cmd = holder["cmd"]
    dvr_path = str(tmp_path / "2026-09-11_10-00-00_录制中.dvr.mp4")
    assert cmd[-1] == dvr_path
    assert record_path in cmd
    flags_index = cmd.index("empty_moov+default_base_moof+frag_keyframe")
    assert cmd[flags_index + 1] == dvr_path
    # 主输出的 movflags 未被镜像参数污染
    assert "frag_keyframe+faststart" in cmd
    assert capture.dvr_output_path == dvr_path


def test_start_command_has_single_output_when_mirror_disabled(monkeypatch, tmp_path) -> None:
    record_path = str(tmp_path / "2026-09-11_10-00-00_录制中.mp4")
    capture, holder = _fake_popen_capture(monkeypatch, tmp_path, dvr_enabled=False)
    assert capture.start("https://example.com/live.m3u8", record_path, codec="copy") is True
    cmd = holder["cmd"]
    assert cmd[-1] == record_path
    assert "empty_moov+default_base_moof+frag_keyframe" not in cmd
    assert capture.dvr_output_path == ""


# ────────────────────────── A. 生命周期与序列化 ──────────────────────────


def test_config_has_dvr_mirror_default_on() -> None:
    from lsc.config import LscConfig

    assert LscConfig().dvr_mirror_enabled is True
    assert "dvr_mirror_enabled: bool = True" in CONFIG
    assert '"dvr_mirror_enabled",' in CONFIG


def test_settings_default_table_has_dvr_mirror_enabled() -> None:
    assert "'dvr_mirror_enabled': True," in ROOM_HANDLER
    assert "def _apply_dvr_mirror_from_settings(settings: dict) -> None:" in ROOM_HANDLER
    assert "_apply_dvr_mirror_from_settings(load_settings())" in ROOM_HANDLER
    assert "_apply_dvr_mirror_from_settings(settings)" in ROOM_HANDLER


def test_dvr_mirror_path_derivation_and_rename(tmp_path) -> None:
    from lsc.core.recording_layout import dvr_mirror_path, move_dvr_mirror

    src = tmp_path / "2026-09-11_10-00-00_录制中.mp4"
    src.write_bytes(b"rec")
    src_dvr = tmp_path / "2026-09-11_10-00-00_录制中.dvr.mp4"
    src_dvr.write_bytes(b"dvr")
    dest = tmp_path / "2026-09-11_10-00-00_至_2026-09-11_10-10-00.mp4"
    dest.write_bytes(b"rec")

    assert dvr_mirror_path(str(src)) == str(src_dvr)
    moved = move_dvr_mirror(str(src), str(dest))
    assert moved == dvr_mirror_path(str(dest))
    assert Path(moved).read_bytes() == b"dvr"
    assert not src_dvr.exists()
    # 镜像缺失时静默返回空串（绝不抛），回看可回退到主录制文件
    assert move_dvr_mirror(str(src), str(dest)) == ""


def test_finalize_recording_file_renames_dvr_mirror(tmp_path) -> None:
    """归档改名（_录制中.mp4 -> 归档名）时镜像必须同规则改名。"""
    from lsc.core.recording_layout import dvr_mirror_path, finalize_recording_file

    src = tmp_path / "2026-09-11_10-00-00_录制中.mp4"
    src.write_bytes(b"rec")
    (tmp_path / "2026-09-11_10-00-00_录制中.dvr.mp4").write_bytes(b"dvr")

    dest = finalize_recording_file(
        str(src),
        started_at=datetime(2026, 9, 11, 10, 0, 0),
        ended_at=datetime(2026, 9, 11, 10, 10, 0),
        dest_dir=str(tmp_path),
    )
    assert Path(dest).name == "2026-09-11_10-00-00_至_2026-09-11_10-10-00.mp4"
    mirror = Path(dvr_mirror_path(dest))
    assert mirror.name == "2026-09-11_10-00-00_至_2026-09-11_10-10-00.dvr.mp4"
    assert mirror.read_bytes() == b"dvr"
    # 两条成功分支都要搬镜像（同盘 os.replace / 跨盘 copy+unlink）
    body = LAYOUT.split("def finalize_recording_file(", 1)[1]
    assert body.count("move_dvr_mirror(") >= 2


def test_orchestrator_sets_and_renews_dvr_output_path() -> None:
    assert "room.dvr_output_path = self._present_dvr_mirror(new_path)" in ORCHESTRATOR
    assert "room.dvr_output_path = self._present_dvr_mirror(committed)" in ORCHESTRATOR
    assert "room.dvr_output_path = self._present_dvr_mirror(output_path)" in ORCHESTRATOR
    assert 'str(getattr(controller, "dvr_output_path", "") or "") if ok else ""' in ORCHESTRATOR
    assert "room.dvr_output_path = \"\"" in ORCHESTRATOR


def test_room_snapshots_expose_dvr_output_path() -> None:
    room_to_dict = ROOM_HANDLER.split("def _room_to_dict", 1)[1].split("def _rooms_list", 1)[0]
    assert "'dvr_output_path': getattr(room, 'dvr_output_path', '') or ''," in room_to_dict
    patch_body = ROOM_HANDLER.split("def _queue_recording_size_patches", 1)[1].split(
        "def _", 1
    )[0]
    assert "'dvr_output_path'" in patch_body
    assert "'dvr_output_path': getattr(room, 'dvr_output_path', '') if room else ''," in RECORDING_HANDLERS


# ────────────────────────── B. review 通道必须不存在 ──────────────────────────

DELETED_SYMBOLS = (
    "_review_streamers",
    "_review_streamers_lock",
    "_MAX_CONCURRENT_REVIEWS",
    "_start_recording_file_mse",
    "_on_file_mse_error",
    "_is_normal_file_playback_end",
    "_offline_file_review_in_progress",
)


@pytest.mark.parametrize("symbol", DELETED_SYMBOLS)
def test_review_channel_symbols_are_gone(symbol) -> None:
    assert symbol not in ROOM_HANDLER, "review 通道符号应已删除: %s" % symbol


def test_review_channel_fields_are_gone() -> None:
    for gone in (
        "active_preview_channel",
        "review_session_id",
        "review_start_sec",
        "review_window_end_sec",
        "preview_review_start_sec",
    ):
        assert gone not in ROOM_HANDLER, "review 通道字段应已删除: %s" % gone
        assert gone not in SESSION


def test_review_phase_broadcast_is_gone() -> None:
    # 注意：不能直接搜 review_phase —— preview_phase 含该子串；按带引号的字面量判定。
    assert "'review_phase'" not in ROOM_HANDLER
    assert '"review_phase"' not in ROOM_HANDLER


def test_mse_channel_review_routing_is_gone() -> None:
    for gone in (
        "channel == 'review'",
        'channel="review"',
        "channel='review'",
        "stream_id=_sid",
        "is_file=True",
    ):
        assert gone not in ROOM_HANDLER
    # 推送只剩直播单通道：不再透传 channel/stream_id
    push = ROOM_HANDLER.split("def _push_mse_segment", 1)[1].split("def _room_to_dict", 1)[0]
    assert "channel" not in push
    assert "stream_id" not in push
    assert "broadcast_mse(normalized_kind, room_id, seg)" in push


def test_review_handlers_are_declarative_stubs() -> None:
    for handler in ("start_recording_review", "close_recording_review"):
        body = ROOM_HANDLER.split("@server.on('%s')" % handler, 1)[1].split("@server.on(", 1)[0]
        assert "deprecated: use local file playback" in body
        assert "'success': False" in body
        # 桩里不得残留任何流 / 会话 / 房间状态操作
        assert "_preview_stream_registry" not in body
        assert "MseStreamer" not in body
        assert "preview_mode" not in body


def test_request_mse_init_has_no_review_branch() -> None:
    body = ROOM_HANDLER.split("async def handle_request_mse_init", 1)[1].split("@server.on(", 1)[0]
    assert "_preview_stream_registry" in body
    assert "channel == 'review'" not in body
    assert body.count("_preview_stream_registry().get(room_id)") == 1


def test_mse_backpressure_drops_file_stream_exemption() -> None:
    body = ROOM_HANDLER.split("async def handle_mse_backpressure", 1)[1].split("@server.on(", 1)[0]
    assert "is_file_stream" not in body
    assert "_mse_push_paused.add(room_id)" in body


def test_handle_mse_preview_keeps_force_restart_but_no_review_branch() -> None:
    body = ROOM_HANDLER.split("async def _handle_mse_preview", 1)[1]
    head = body.split("if existing is not None and existing.is_running:", 1)[0]
    assert "is_review" not in head
    assert "force_restart" in head
    assert "recording_review" not in head
    # 强制重启仍把房间模式复位到直播（否则前端会停在旧模式）
    assert "r.preview_mode = 'live_mse'" in head


def test_shutdown_no_longer_cleans_review_streamers() -> None:
    assert "_review_streamers" not in ROOM_HANDLER
    shutdown = ROOM_HANDLER.split("def shutdown_room_handlers", 1)[1].split("stop_all_shared", 1)[0]
    assert "clear_items()" in shutdown
    assert "mse_streamers_stopped" in shutdown
