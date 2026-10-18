"""持续分析能力闸：不被支持的 (game, mode) 组合必须在启动侧就被拒（§11.18-D）。

修前的形状：前端"通用"档发 `mode='scene'`，后端照样启动循环，但粗扫分支只服务
`game='valorant' + mode='valorant_round'`，其余组合落到 `_detect_rounds_by_audio_rhythm`
这个恒返回 [] 的废弃兼容包装 ⇒ **UI 进度在走、日志每轮 WARN、结果永远 0 条**，
用户以为"正在分析"。该包装已删除，改由启动侧按 `AnalyzerCapabilities.realtime_continuous`
+ 实际扫描分支的覆盖范围如实拒绝。
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from handlers.analysis_handlers import register_analysis_handlers


class _FakeServer:
    def __init__(self) -> None:
        self.handlers: dict = {}

    def on(self, name, *args, **kwargs):
        def _deco(fn):
            self.handlers[name] = fn
            return fn
        return _deco


class _FakeBridge:
    def queue_broadcast(self, msg):
        pass


def _register(*, is_recording: bool):
    """返回 start_continuous_analysis 处理器；房间存在但**未在录制**。"""
    room = SimpleNamespace(room_id="r1", is_recording=is_recording)
    server = _FakeServer()
    register_analysis_handlers(
        server,
        bridge=_FakeBridge(),
        manager=SimpleNamespace(
            get_room=lambda rid: room,
            get_recording_status=lambda rid: {"exists": True, "is_recording": is_recording},
        ),
        bridge_executor=None,
        ai_executor=None,
        load_settings=lambda: {},
        safe_float=lambda v, d: d,
        analyze_scene_or_rounds=lambda *a, **k: [],
        validate_synced_analysis_targets=lambda *a, **k: (True, "", None, []),
        continuous_analysis_loop=None,
        auto_export_highlights=None,
        build_continuous_status_payload=lambda *a, **k: {},
        map_highlight_to_room=lambda *a, **k: {},
        recording_media_start=lambda *a, **k: 0.0,
        min_highlight_duration_for_queue=lambda *a, **k: 0.0,
        valorant_round_key=lambda h: "k",
        should_broadcast_clip_list_update=lambda *a, **k: False,
        analysis_jobs={},
        analysis_jobs_lock=threading.RLock(),
        continuous_tasks={},
        refined_round_keys=set(),
        refined_round_keys_lock=threading.Lock(),
    )
    return server.handlers["start_continuous_analysis"]


def test_scene_continuous_analysis_is_refused_at_start():
    """通用/scene：必须立刻被拒，而不是启动一个永不产出的循环。"""
    handler = _register(is_recording=True)
    result = asyncio.run(handler({"room_id": "r1", "mode": "scene", "game": "generic"}))
    assert result.get("success") is False
    assert "持续分析" in (result.get("error") or "")


def test_valorant_game_with_scene_mode_is_also_refused():
    """game 是 valorant 但模式不是回合：粗扫分支同样不覆盖，必须拒。"""
    handler = _register(is_recording=True)
    result = asyncio.run(handler({"room_id": "r1", "mode": "scene", "game": "valorant"}))
    assert result.get("success") is False
    assert "持续分析" in (result.get("error") or "")


def test_valorant_round_mode_passes_the_gate():
    """受支持组合不得被能力闸拦住：它必须继续走到后面的录制状态校验。

    这里房间 `is_recording=False`，所以拿到的应是"尚未开始录制"的错误 ——
    证明闸只挡不支持的组合，没有误伤正常路径。
    """
    handler = _register(is_recording=False)
    result = asyncio.run(handler({"room_id": "r1", "mode": "valorant_round", "game": "valorant"}))
    assert "尚未开始录制" in (result.get("error") or "")


def test_generic_plugin_declares_no_realtime_continuous():
    """闸的依据是插件自己的声明，而不是散落各处的 if。"""
    from lsc.analyzer.registry import get_analyzer

    assert get_analyzer("generic").capabilities().realtime_continuous is False
    assert get_analyzer("valorant").capabilities().realtime_continuous is True
