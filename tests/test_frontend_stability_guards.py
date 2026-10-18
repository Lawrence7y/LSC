"""前端稳定性的少量静态守卫。

原先这里 136 条「读 TS 源码 + assert 子串」假测试已迁到 lsc-electron 的
`*.behavior.test.ts`（真调用函数 + mock）：

  websocket.behavior.test.ts          — WS 断连队列 / 二进制帧 / payload 规范化
  mediaSourcePlayer.behavior.test.ts  — 背压 pause/resume、cleanup、goLive、sink 重建
  timelineCoords.behavior.test.ts     — 三轴换算、自动绑定、内容右沿、滑动窗
  timelineWindow.behavior.test.ts     — seek 收口、DVR 紫线、可点==可回放
  previewAudioAligner.behavior.test.ts— 采集诊断、静音覆盖与恢复
  previewLifecycle.behavior.test.ts   — 预览源重建判据、阶段文案
  clipExportPolicy.behavior.test.ts   — 导出门禁、近似判定、导出摘要
  clipExportFlow.behavior.test.ts     — 导出 payload 冻结、列表项身份
  clipNaming.behavior.test.ts         — 切片命名、手动切片墙钟快照

本文件只保留无法用 vitest 表达的静态契约：后端 Python 事件面 / Electron
单实例锁 / 跨语言字段名。改动这些名字会静默打断前后端对接，值得留一条烟测。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# ── 后端 Python 事件面契约（前端 useWebSocket 按这些名字订阅） ──


def test_shared_preview_keeps_mse_event_names() -> None:
    """后端 MSE 事件名是前后端硬契约，改名会静默丢预览。"""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")

    for name in (
        "mse_init",
        "mse_segment",
        "mse_error",
        "mse_reconnecting",
        "mse_reconnected",
        "request_mse_init",
        "mse_backpressure",
        "preview_phase",
    ):
        assert name in source


def test_backend_alignment_handler_reads_preview_diagnostics() -> None:
    """对齐诊断字段名是前端 align_preview_audio payload 的硬契约。"""
    source = (ROOT / "python-backend/handlers/alignment_handlers.py").read_text(encoding="utf-8")
    handler_body = source.split("@server.on('align_preview_audio')", 1)[1]

    for field in ("ready_state", "has_audio_track", "rms", "sample_count", "capture_reason"):
        assert field in handler_body
    # pcm_base64 体积大，禁止进诊断日志
    diag_log = handler_body.split("预览音频诊断", 1)[1].split(")", 1)[0]
    assert "pcm_base64" not in diag_log


def test_backend_mse_broadcast_uses_binary_helper() -> None:
    """预览推流走二进制帧（broadcast_mse / _push_mse_segment），禁止 base64 热路径。"""
    handler = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    server = (ROOT / "python-backend/server.py").read_text(encoding="utf-8")
    assert "def _push_mse_segment" in handler
    assert "broadcast_mse" in server
    assert "base64.b64encode(seg)" not in handler


def test_backend_mse_binary_frame_magic_matches_frontend() -> None:
    """前后端 MSE 二进制帧魔数必须一致（MSE / MS2），否则 tryParseMseBinaryFrame 全拒。"""
    backend = (ROOT / "python-backend/mse_ws_frames.py").read_text(encoding="utf-8")
    frontend = (ROOT / "lsc-electron/src/utils/mseBinary.ts").read_text(encoding="utf-8")
    assert "MSE" in backend or "0x4d" in backend
    assert "MS2" in backend or "0x32" in backend
    assert "0x4d" in frontend and "0x53" in frontend


def test_python_handler_clip_naming_helpers() -> None:
    """Python 侧命名 helper 与前端 clipNaming 对齐（_M / _R）。"""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "format_manual_clip_label" in source
    assert "format_ai_round_clip_label" in source
    assert "_M" in source
    assert "_R" in source


def test_room_handler_mute_awaits_before_broadcast() -> None:
    """静音须 await 生效后再广播，否则 rooms_updated 会带回旧值。"""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    mute_body = source.split("async def handle_set_preview_muted(data):", 1)[1].split("@server.on(", 1)[0]
    assert "bridge.manager.call(manager.set_preview_muted" in mute_body
    assert "bridge.manager.submit(manager.set_preview_muted" not in mute_body
    assert "_broadcast_rooms(force=True)" in mute_body


def test_connect_does_not_restore_persisted_rooms() -> None:
    """启动/WS 连接不得从磁盘恢复上次房间列表。"""
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    connect_body = source.split("async def handle_connect(websocket):", 1)[1].split("@server.on(", 1)[0]
    assert "load_rooms()" not in connect_body


def test_persistence_save_rooms_coalesced_without_default_fsync() -> None:
    """房间持久化写合并 + 默认不强制 fsync；关停前必须冲刷。"""
    src = (ROOT / "python-backend/persistence.py").read_text(encoding="utf-8")
    assert "def schedule_save_rooms" in src
    assert "def flush_pending_room_saves" in src
    assert "fsync: bool = False" in src
    main_src = (ROOT / "python-backend/main.py").read_text(encoding="utf-8")
    assert "flush_pending_room_saves(" in main_src.split("def stop(self)", 1)[1]


def test_electron_single_instance_lock_blocks_second_backend() -> None:
    """多开会起多份后端抢端口；ready 回调最前面必须挡住第二实例。"""
    source = (ROOT / "lsc-electron/electron/main.ts").read_text(encoding="utf-8")
    assert "app.requestSingleInstanceLock()" in source
    assert "app.on('second-instance'" in source
    ready = source.split("app.whenReady().then(", 1)[1]
    assert "if (!hasSingleInstanceLock)" in ready.split("app.commandLine.appendSwitch", 1)[0]


def test_renderer_crash_or_load_failure_recovers_the_window() -> None:
    """首帧前崩溃/生产加载失败必须有窗口级兜底（2026-09-24 修复）。

    修前 render-process-gone 只打日志、did-fail-load 重试被
    VITE_DEV_SERVER_URL 门死为 dev-only ⇒ 生产崩溃时进程+托盘活着但
    没有任何窗口，用户只能任务管理器杀进程。
    """
    source = (ROOT / "lsc-electron/electron/main.ts").read_text(encoding="utf-8")
    # 崩溃处理器必须真的恢复：重载或重建，而不是只记一行日志
    gone = source.split("'render-process-gone'", 1)[1]
    handler = gone.split("if (process.env.VITE_DEV_SERVER_URL)", 1)[0]
    assert "_recreateWindow" in handler
    assert "reloadIgnoringCache" in handler
    # did-fail-load 的重试不得再被 dev URL 门死（生产同样要能重试）
    fail = source.split("'did-fail-load'", 1)[1]
    retry_branch = fail.split("const retriable", 1)[1]
    assert "VITE_DEV_SERVER_URL" not in retry_branch.split("appLog('WARN', 'createWindow', `正在重试加载", 1)[0]
    # ready-to-show 必须计入 _readyToShowFired，崩溃处理器据此选择重建还是重载
    assert "_readyToShowFired = true" in source


def test_backend_spawn_error_clears_handle() -> None:
    """spawn 失败的 error 事件必须清理句柄（2026-09-24 修复）。

    修前 error 只打日志：Node 不保证再发 exit ⇒ backendProcess 句柄残留、
    spawnBackend 的存活守卫恒真 ⇒ 后端再也起不来，只能等 restart-backend
    的 killBackendAndWait(8s) 超时才恢复。
    """
    source = (ROOT / "lsc-electron/electron/main.ts").read_text(encoding="utf-8")
    error_handler = source.split("backendProcess.on('error'", 1)[1]
    handler_body = error_handler.split("backendProcess.on(", 1)[0]
    assert "handleBackendGone" in handler_body
    # 统一清理函数必须置空句柄（exit 与 error 共用）。区间取到 exit 注册处，
    # 即 handleBackendGone 函数体结束、事件登记开始之前。
    cleanup = source.split("const handleBackendGone = ", 1)[1]
    cleanup_body = cleanup.split("backendProcess.on('exit'", 1)[0]
    assert "backendProcess = null" in cleanup_body


def test_frontend_listens_for_backend_mse_event_names() -> None:
    """前端 useWebSocket 必须订阅后端广播的 MSE / 预览阶段事件名。"""
    hook = (ROOT / "lsc-electron/src/hooks/useWebSocket.ts").read_text(encoding="utf-8")
    for name in ("mse_init", "mse_segment", "mse_error", "preview_phase"):
        assert name in hook
