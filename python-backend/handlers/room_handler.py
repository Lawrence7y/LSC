"""WebSocket 消息路由器：处理前端请求并与 RoomOrchestrator 交互。

将前端的房间管理、录制、导出、预览、分析等操作路由到编排线程执行，
并通过广播将房间状态变更实时推送给前端。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil

# 添加 lsc 到 Python 路径
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import numpy as np

_LSC_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
if _LSC_ROOT not in sys.path:
    sys.path.insert(0, _LSC_ROOT)

from handlers.timeline_handlers import (
    register_timeline_handlers,
    timeline_to_dict,
)
from persistence import (
    is_analysis_stale,
    load_analysis_results,
    save_analysis_results,
    save_rooms,
)

from lsc.config import (
    ExportProfile,
    is_platform_pipeline_component_enabled,
    is_platform_v2_hard_blocked,
    load_config,
)
from lsc.core.orchestrator import (
    RoomOrchestrator,
    _get_configured_max_previews,
    _is_stream_offline_error,
)
from lsc.core.services.ingest_registry import PreviewStreamRegistry, get_shared_ingest_registry
from lsc.core.services.mse_streamer import MseStreamer, _check_nvenc
from lsc.core.services.resource_monitor import collect_system_stats, get_resource_pressure
from lsc.core.services.timeline_service import (
    build_room_snapshots_from_align,
    get_timeline_service,
)
from lsc.platforms.base import ERROR_OFFLINE
from lsc.platforms.failure import FailureKind, classify_failure
from lsc.platforms.recovery_policy import mark_failed_candidate, recovery_action
from lsc.platforms.redaction import redact_text, redact_url
from lsc.platforms.registry import detect_platform, get_display_name, parse_stream, select_quality
from lsc.utils.error_messages import humanize_error, humanize_error_with_suggestion
from lsc.utils.process_launcher import run_hidden

_log = logging.getLogger('lsc.handlers')

_MAX_ROOM_URL_LENGTH = 2048
_MAX_ROOM_URLS_PER_ADD = 12


def _should_refresh_failed_stream(error: object) -> bool:
    """Use typed media failures for candidate quarantine/URL refresh."""
    text = str(error or "")
    lowered = text.lower()
    if "preview stdout stalled" in lowered:
        return False
    if "signed http remote eof after media" in lowered:
        return False
    kind = classify_failure(text)
    if kind is FailureKind.PREVIEW_ENCODER_FAILURE:
        return False
    return kind in {
        FailureKind.CDN_FORBIDDEN,
        FailureKind.SIGNATURE_EXPIRED,
        FailureKind.CONNECTION_RESET,
        FailureKind.CONNECT_TIMEOUT,
    }


def _preview_auto_reconnect_allowed(stream_info_or_platform: object) -> bool:
    """Whether preview errors may enter the automatic MSE reconnect loop."""
    if isinstance(stream_info_or_platform, str):
        platform = stream_info_or_platform
    else:
        platform = str(getattr(stream_info_or_platform, "platform", "") or "")
    from lsc.platforms.capabilities import get_platform_capabilities

    return bool(get_platform_capabilities(platform).preview_auto_reconnect)


def _is_lease_rotation_error(
    stream_info: object | None,
    error: object,
    *,
    saw_first_ts: bool = False,
) -> bool:
    """True when a shared-upstream clean EOF should rotate the signed lease."""
    return recovery_action(stream_info, error, saw_first_ts=saw_first_ts) == "rotate_lease"


async def queue_export(
    room_id,
    start_sec,
    end_sec,
    label='clip',
    preset_id='',
    source='',
    job_id='',
    mark_in_wallclock=None,
    mark_out_wallclock=None,
    recording_start_mono=None,
    recording_media_start_mono=None,
    use_room_marks=False,
    content_offset=None,
    pre_mapped=False,
):
    """兼容旧导入路径，委托给统一导出实现。

    请求快照（mark_in_wallclock/mark_out_wallclock/recording_start_mono）和
    ``_resolve_export_range`` 的 exact/approximate 语义仍由统一实现负责；
    snap_in/snap_out/snap_rec 会优先于房间标记；
    部分墙钟快照缺失时只回退到 ``start_sec - content_offset``，无墙钟快照
    时才允许 use_room_marks。该桥接不直接读取房间当前标记。
    """
    from handlers.export_handlers import get_queue_export

    delegate = get_queue_export()
    if delegate is None:
        return {'error': 'export handler is not initialized'}
    return await delegate(
        room_id,
        start_sec,
        end_sec,
        label=label,
        preset_id=preset_id,
        source=source,
        job_id=job_id,
        mark_in_wallclock=mark_in_wallclock,
        mark_out_wallclock=mark_out_wallclock,
        recording_start_mono=recording_start_mono,
        recording_media_start_mono=recording_media_start_mono,
        use_room_marks=use_room_marks,
        content_offset=content_offset,
        pre_mapped=pre_mapped,
    )


def _invalid_room_url_result(url: str, error: str, error_code: str = "invalid_url") -> dict[str, Any]:
    """构造统一的直播间链接验证失败结果。"""
    safe_url = redact_url(url)
    return {
        "valid": False,
        "url": safe_url,
        "normalized_url": safe_url,
        "error": redact_text(error),
        "error_code": error_code,
    }


def _error_response(exc: Exception | str) -> dict[str, Any]:
    """生成带修复建议的错误响应（P1-1: 错误提示友好化）"""
    raw = redact_text(exc)
    info = humanize_error_with_suggestion(raw)
    return {'success': False, 'error': info['message'], 'suggestion': info.get('suggestion')}


def _validate_room_url_candidate(raw_url: object) -> dict[str, Any]:
    """校验 URL 格式并通过平台适配器实际解析直播间。

    该函数会进行网络解析，必须在线程池中调用，不能阻塞 WebSocket 事件循环。
    已识别平台返回 ``offline`` 时仍视为有效直播间：用户可以先添加未开播房间。
    """
    if not isinstance(raw_url, str):
        return _invalid_room_url_result("", "直播间链接必须是文本")

    url = raw_url.strip()
    if not url:
        return _invalid_room_url_result(url, "请输入直播间链接")
    if len(url) > _MAX_ROOM_URL_LENGTH:
        return _invalid_room_url_result(url, "直播间链接过长")
    if any(ch.isspace() or ord(ch) < 32 for ch in url):
        return _invalid_room_url_result(url, "链接中不能包含空格、换行或控制字符")

    try:
        parsed = urlsplit(url)
        # 访问 port 属性可同时触发非法端口格式校验。
        _ = parsed.port
    except ValueError:
        return _invalid_room_url_result(url, "链接格式无效，请检查地址是否完整")

    if parsed.scheme.lower() not in {"http", "https"}:
        return _invalid_room_url_result(url, "仅支持 http:// 或 https:// 直播间链接")
    if not parsed.hostname:
        return _invalid_room_url_result(url, "链接缺少有效的网站域名")
    if parsed.username is not None or parsed.password is not None:
        return _invalid_room_url_result(url, "直播间链接不能包含用户名或密码")

    # URL validation is also a platform-facing parse entry point.  Once all
    # V2 components are enabled for this platform, keep it on the same
    # resolver bridge so the first room add cannot silently warm the legacy
    # URL-only cache or bypass credential scoping.  Media probing and lease
    # issuance still happen at connect time; this stage only validates the
    # room identity and presents metadata to the UI.
    platform_hint = detect_platform(url)
    cfg = load_config()
    use_v2 = all(
        is_platform_pipeline_component_enabled(component, platform_hint, cfg)
        for component in (
            "unified_resolver_v2",
            "media_probe_v2",
            "stream_lease_v2",
        )
    )
    if use_v2:
        from lsc.platforms.models import ResolveRequest
        from lsc.platforms.resolver import resolve_stream_v2

        result = resolve_stream_v2(
            ResolveRequest(
                source_url=url,
                force_refresh=False,
                request_id="room-url-validation",
            )
        )
        resolved_platform = str(getattr(result, "platform", "") or platform_hint or "unknown")
        first_candidate = next(iter(getattr(result, "candidates", ()) or ()), None)
        info = SimpleNamespace(
            platform=resolved_platform,
            room_url=str(getattr(result, "room_url", "") or url),
            stream_url=str(getattr(first_candidate, "url", "") or ""),
            streamer=str(getattr(result, "anchor_name", "") or ""),
            title=str(getattr(result, "room_title", "") or ""),
            is_live=str(getattr(result, "live_status", "") or "").upper() == "LIVE",
            error=(
                str(getattr(getattr(result, "error", None), "user_message", "") or "")
                if getattr(result, "error", None) is not None
                else ""
            ),
            error_code=str(
                getattr(getattr(result, "error", None), "code", "") or ""
            ),
        )
    else:
        info = parse_stream(url)
    platform = str(info.platform or "unknown")
    platform_name = get_display_name(platform)
    base_result: dict[str, Any] = {
        "url": redact_url(url),
        "normalized_url": redact_url(str(info.room_url or url)),
        "platform": platform,
        "platform_name": platform_name,
        "streamer": redact_text(info.streamer or ""),
        "title": redact_text(info.title or ""),
        "is_live": bool(info.is_live),
        "error_code": str(info.error_code or ""),
    }

    # 已识别的直播间即使尚未开播也允许添加，避免用户必须等开播后才能建房。
    if info.error_code == ERROR_OFFLINE and platform != "unknown":
        return {
            **base_result,
            "valid": True,
            "warning": "链接有效，主播当前未开播",
        }

    if info.error:
        return {
            **base_result,
            "valid": False,
            "error": redact_text(humanize_error(str(info.error))),
        }
    if platform == "unknown" or not (info.stream_url or info.is_live):
        return {
            **base_result,
            "valid": False,
            "error": "未能识别有效的直播间或直播流",
            "error_code": str(info.error_code or "unsupported_url"),
        }

    return {
        **base_result,
        "valid": True,
        "message": f"已识别为{platform_name}直播间",
    }


# 打包后的 backend 位于 Program Files/resources，不能在代码目录旁保存可变数据。
# Electron 启动时会传入 LSC_DATA_DIR（其 userData 目录）；开发模式保留项目内
# python-backend 目录作为回退，以避免改变本地调试的既有配置位置。
_PERSISTENCE_DIR = os.environ.get('LSC_DATA_DIR') or os.path.join(os.path.dirname(__file__), '..')
SETTINGS_FILE = os.path.join(_PERSISTENCE_DIR, 'settings.json')
RECORDING_HISTORY_FILE = os.path.join(_PERSISTENCE_DIR, 'recording_history.json')


def _load_recording_history() -> list[dict[str, Any]]:
    """从文件加载录制历史，失败返回空列表。"""
    try:
        with open(RECORDING_HISTORY_FILE, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            # 裁剪至上限，防止历史文件已膨胀（#18）
            return data[-_MAX_RECORDING_HISTORY:]
    except Exception as exc:
        _log.warning("加载录制历史失败，使用空列表: %s", exc)
    return []


def _atomic_write_json(file_path: str, data: Any) -> None:
    """原子写入 JSON 文件：先写 .tmp 再 replace，防止断电损坏。"""
    tmp_path = file_path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, file_path)


def _save_recording_history(history: list[dict[str, Any]]) -> None:
    """持久化录制历史到文件（原子写入），失败时打印日志。"""
    try:
        _atomic_write_json(RECORDING_HISTORY_FILE, history)
    except Exception as exc:
        _log.error("保存录制历史失败: %s", exc)


# 录制历史上限：防止 24x7 长期运行时 JSON 无限膨胀（#18）
# 必须在 _load_recording_history() 之前定义，因为该函数在模块加载时即调用
_MAX_RECORDING_HISTORY = 500

recording_history: list[dict[str, Any]] = _load_recording_history()
# 保护 recording_history 的锁：start_handler（asyncio 线程）与 stop_handler
# （编排线程 via orchestrator.call）并发读写，无锁可丢记录或损坏列表（#17）
_recording_history_lock = threading.Lock()

# ── 切片命名工具（与前端 clipNaming.ts 保持同步）──

def _sanitize_streamer_name(name: str, max_len: int = 6) -> str:
    """清理主播名中的非法文件名字符，默认截断至 6 字符（短列表名）。"""
    cleaned = re.sub(r'[/\\:*?"<>|]', '_', (name or '未知')).strip() or '未知'
    return cleaned[:max_len]


def format_manual_clip_label(streamer: str, index: int) -> str:
    """手动切片 label：{主播}_M{NN}"""
    return f"{_sanitize_streamer_name(streamer)}_M{index:02d}"


def format_ai_round_clip_label(streamer: str, round_idx: int, index: int = 0) -> str:
    """AI 回合切片 label：{主播}_R{RR}（index 保留兼容，不写入短名）"""
    _ = index
    return f"{_sanitize_streamer_name(streamer)}_R{round_idx:02d}"


# 每房间手动切片计数器（room_id -> 当前序号）
_manual_clip_counters: dict[str, int] = {}
# 每房间 AI 回合切片计数器（room_id -> 当前序号）
_ai_clip_counters: dict[str, int] = {}


# Analytics jobs in progress: {room_id: {"progress": 0.0, "highlights": [...], "completed_at": float}}
_analysis_jobs: dict[str, dict[str, Any]] = {}
_analysis_jobs_lock = threading.RLock()
_ANALYSIS_JOB_TTL = 300.0  # 5 分钟后自动清理已完成的分析结果


def _clear_analysis_job(room_id: str) -> None:
    """移除房间的分析任务登记（失败/清理路径）。

    登记提前到 handler 线程后，任何失败路径都必须清理登记，
    否则前端会一直看到「分析中」卡住。
    """
    with _analysis_jobs_lock:
        _analysis_jobs.pop(room_id, None)

# 持续分析任务状态：room_id -> {task, last_analyzed, highlights, cancelled}
# 边录边分析：后台 asyncio 任务定期对录制文件新增段做增量场景检测
_continuous_tasks: dict[str, dict[str, Any]] = {}
_VALORANT_INCREMENTAL_LOOKBACK_SEC = 30.0  # 纯 OCR 增量回看（与 valorant_plugin 一致）
_VALORANT_MAX_CATCHUP_SEC = 480.0  # 单次 tick 最多向前追赶的新内容时长
# 录制中可分析尖端 = 墙钟 − 写缓冲。frag_keyframe 下 15s 足够避开未落盘尾部；
# 旧值 30s 导致启动后空等半分钟、UI 滞后地板也卡在 ~30s。
_RECORDING_WRITE_BUFFER_SEC = 15.0
_VALORANT_KICK_AHEAD_SEC = 8.0  # 增量 kick：相对 last_analyzed 至少攒够的新媒体秒
# 停录后 ffprobe/码率缓存允许超出最后录制墙钟的容差；超过则钳制（防 open_tail 虚高出点）
_POST_STOP_DURATION_SLACK_SEC = 30.0
# OCR 扫描 TimeoutError 后：降级纯音频追赶，避免立刻再开超大 OCR 窗压垮 DirectML
_OCR_DEGRADE_TICKS_AFTER_TIMEOUT = 5
_POST_TIMEOUT_MAX_CATCHUP_SEC = 60.0
_SCAN_ABORT_GRACE_SEC = 3.0  # 停止目标：3 秒内终止 FFmpeg 并退出当前短推理批次
_SCAN_ABORT_HARD_SEC = 30.0  # 超时后继续等待线程释放 semaphore 的硬上限，避免永久挂死任务槽
_VALORANT_MIN_LIST_DURATION_SEC = 5.0  # list_only 入列下限（短 spike 回合也须进列表待确认）
_OCR_BOUNDARY_SOURCE = "valorant_ocr_v1"  # 纯 OCR 路径产出（顶部条 + 中央横幅）
_OCR_VALID_START_BY = frozenset({"ocr_combat"})
_OCR_VALID_END_BY = frozenset({"next_prep", "open_tail", "next_combat"})
_OCR_FINALIZE_OVERLAP_SEC = 120.0
# 买枪+交战+结算(+赛事回放) 常见 90–130s；>150s 才视为异常合并/漏切
_VALORANT_MAX_ROUND_DURATION_SEC = 150.0
_MAX_SKIP_SLEEP_TICKS = 5  # 主循环连续跳过 sleep 的防御上限：超过强制 0.5s 节流，防忙循环广播风暴
_SCAN_ERROR_BACKOFF_SEC = 30.0  # 持续分析 worker 失败后的重试退避（非收尾）
_SCAN_MAX_TIMEOUT_RETRIES = 3  # 同一窗口连续超时重试上限：超过则跳过该窗口（防死循环）
_WORKER_MAX_RESTARTS = 3  # worker 崩溃重建上限：超过则终止任务，防止无限重建循环
_deferred_export_jobs: list[dict[str, Any]] = []  # 延后导出队列（先入列，压力缓解后再导出）

# ── 质量优先模式 ─────────────────────────────────────────────────────
# 质量优先（默认开启）下，资源压力不再干预持续分析与导出：不暂停分析、
# 不拉长扫描间隔、不阻塞延后导出冲刷。真实压力仍照常广播供前端展示。
# 背景（2026-08-04 实测）：录制+预览+多路解码使 CPU 频繁 ≥95%，
# critical 压力触发 pause_analysis 与 ×3~×4 间隔倍率，导致分析滞后
# 持续增长到 130s+，切片输出晚于下一回合交手。
_QUALITY_FIRST_NORMAL_PRESSURE: dict[str, Any] = {
    "level": "normal",
    "analysis_interval_multiplier": 1,
    "pause_analysis": False,
    "degrade_analysis": False,
    "analysis_window_sec": 240,
    # 1.0fps：0.5fps（2.0）会漏短促准备/结算横幅，回合迟迟不闭合 → 滞后虽低但不出片
    "ocr_sample_interval": 1.0,
}


def _quality_first_enabled() -> bool:
    """质量优先开关（appSettings.analysis_quality_first，默认 True）。"""
    try:
        settings = load_settings()
        app = settings.get('appSettings', {}) if isinstance(settings, dict) else {}
        return bool(app.get('analysis_quality_first', True))
    except Exception:
        return True


def _analysis_pressure() -> dict[str, Any]:
    """分析/导出决策用的资源压力：质量优先模式下一律返回 normal。"""
    if _quality_first_enabled():
        return dict(_QUALITY_FIRST_NORMAL_PRESSURE)
    return get_resource_pressure()


def _clip_id(room_id: str, start: float, end: float) -> str:
    """生成稳定的切片 ID（前后端同算法独立计算，用于去重）。"""
    return f"{room_id}_{int(round(start * 10))}_{int(round(end * 10))}"


# 导出任务映射：前端 job_id -> 后端 clip_id，用于取消导出时定位 FFmpeg 进程
export_jobs: dict[str, str] = {}
_export_jobs_lock = threading.Lock()
_export_job_states: dict[str, dict[str, Any]] = {}
_MAX_EXPORT_JOB_STATES = 512


def _set_export_job_state(job_id: str, status: str, **fields: Any) -> None:
    """记录可查询的导出状态，补偿 WebSocket 单次终态广播丢失。"""
    if not job_id:
        return
    with _export_jobs_lock:
        current = dict(_export_job_states.get(job_id) or {})
        current.update(fields)
        current.update({
            'job_id': job_id,
            'status': status,
            'updated_at': time.time(),
        })
        _export_job_states[job_id] = current
        if len(_export_job_states) > _MAX_EXPORT_JOB_STATES:
            stale = sorted(
                _export_job_states,
                key=lambda key: float(_export_job_states[key].get('updated_at', 0.0)),
            )[:len(_export_job_states) - _MAX_EXPORT_JOB_STATES]
            for key in stale:
                _export_job_states.pop(key, None)


def _get_export_job_states(job_ids: list[str]) -> list[dict[str, Any]]:
    with _export_jobs_lock:
        return [
            dict(_export_job_states[job_id])
            for job_id in job_ids
            if job_id in _export_job_states
        ]

# 分析 FFmpeg 串行化：确保同时只有 1 个分析任务跑 FFmpeg（音频提取+OCR），
# 避免与录制/预览/导出 FFmpeg 竞争导致 8+ 进程同时运行
_analysis_semaphore = asyncio.Semaphore(1)

# 全局导出队列：所有导出任务（手动/自动/分析）统一入队，worker 池并行消费
# 保护_export_in_flight 和连续分析状态的线程锁（防止 handler 异步与 bridge.sync 并发冲突）
_export_stats_lock = threading.Lock()

# ── P2-2: 批量导出进度显示 ─────────────────────────────────────────
_export_total = 0  # 总导出任务数（一个 batch 内的总数）
_export_completed = 0  # 已完成任务数
_export_batch_id = ""  # 当前批次 ID


def _notify_export_overall(bridge, success: bool = True) -> None:
    """广播批量导出总体进度（P2-2: 批量导出进度显示）"""
    global _export_completed
    with _export_stats_lock:
        _export_completed += 1
        total = _export_total
        completed = _export_completed
    if total > 0:
        bridge.queue_broadcast({
            'type': 'export_overall_progress',
            'data': {
                'total': total,
                'completed': completed,
                'percent': (completed / total * 100),
                'batch_id': _export_batch_id,
            },
        })


def _reset_export_batch(total: int, batch_id: str) -> None:
    """重置批量导出计数器"""
    global _export_total, _export_completed, _export_batch_id
    with _export_stats_lock:
        _export_total = total
        _export_completed = 0
        _export_batch_id = batch_id


def _get_export_max_concurrent() -> int:
    """从 settings 读取 export_max_concurrent，默认 2，合法值仅 1 或 2。"""
    try:
        val = int(load_settings().get('export_max_concurrent', 2))
        if val not in (1, 2):
            return 2
        return val
    except (TypeError, ValueError):
        return 2
_export_cancelled_jobs: set[str] = set()  # 已取消的 job_id 集合（含排队中）

# MSE streamer instances keyed by room_id
_mse_streamers: dict[str, Any] = {}
# 保护 _mse_streamers 的锁：asyncio 线程与 run_in_executor 线程池均会并发访问
_mse_streamers_lock = threading.Lock()


def _preview_stream_registry() -> PreviewStreamRegistry:
    return PreviewStreamRegistry(backing=_mse_streamers, lock=_mse_streamers_lock)


_shared_ingests = get_shared_ingest_registry()


def _ingest_diagnostics() -> dict[str, int]:
    try:
        stats = _shared_ingests.snapshot_counts()
    except Exception as exc:
        _log.debug("shared ingest diagnostics failed: %s", exc)
        stats = {
            "shared_ingests": 0,
            "recording_sinks": 0,
            "preview_subscribers": 0,
            "preview_dropped_bytes": 0,
            "preview_dropped_batches": 0,
        }
    stats["legacy_mse_streamers"] = _preview_stream_registry().active_count()
    return stats


def _stop_idle_shared_ingest(room_id: str, reason: str) -> bool:
    if room_id in _recording_starting:
        return False
    try:
        ingest = _shared_ingests.get(room_id)
    except Exception as exc:
        _log.debug("shared ingest lookup failed during cleanup room_id=%s: %s", room_id, exc)
        return False
    if ingest is None:
        return False
    if getattr(ingest, "recording_active", False):
        return False
    if getattr(ingest, "_recording_process", None) is not None:
        return False
    try:
        supervisor = getattr(_shared_ingests, "get_supervisor_if_exists", None)
        current = supervisor(room_id) if callable(supervisor) else None
        if current is not None and bool(getattr(current, "recording_requested", False)):
            return False
    except Exception as exc:
        _log.debug("shared ingest supervisor lookup failed during cleanup room_id=%s: %s", room_id, exc)
    if getattr(ingest, "preview_subscribers", 0) > 0:
        return False
    try:
        _shared_ingests.stop_room(room_id, reason=reason)
        return True
    except Exception as exc:
        _log.warning("shared ingest cleanup failed room_id=%s: %s", room_id, exc)
        return False


def _compute_preview_quality_params(data: dict | None = None) -> dict[str, Any]:
    """从 settings 和消息数据计算预览画质参数，含压力感知降级。

    降级策略：
    - 3 路以上新建/重建的非放大预览上限 854×480@20fps
    - 4 路或 pressure 时 640×360@15fps
    - critical 时拒绝新增高成本任务（调用方应检查 pressure_reject）
    """
    settings = load_settings()
    preview_quality = (data or {}).get('preview_quality') or settings.get('preview_quality', '高清')
    preset = _get_preview_quality_preset(preview_quality)
    use_nvenc = _check_nvenc()
    preset_width = preset['width']
    preset_height = preset['height']
    width = preset_width
    height = preset_height
    target_fps = 0  # 0 表示保持原画帧率
    active_mse_count = _preview_stream_registry().active_count()

    # 压力感知降级
    pressure = get_resource_pressure()
    pressure_level = pressure.get('level', 'normal')

    # 默认不限制（使用 preset 原始分辨率）
    max_w, max_h = 0, 0
    degraded = False
    reason = ''

    incoming_count = active_mse_count + 1

    if pressure_level == 'critical' or active_mse_count >= 4:
        max_w, max_h, target_fps = 640, 360, 15
        reason = '系统资源紧张' if pressure_level == 'critical' else f'多路预览（{incoming_count}路）'
    elif pressure_level == 'pressure' or active_mse_count >= 3:
        max_w, max_h, target_fps = 854, 480, 20
        reason = '系统资源压力较高' if pressure_level == 'pressure' else f'多路预览（{incoming_count}路）'

    # 仅在设置了限制时才降分辨率
    if max_w > 0 and max_h > 0:
        if width == 0 or height == 0:
            width, height = max_w, max_h
            degraded = True
        elif width > max_w or height > max_h:
            ratio = min(max_w / width, max_h / height)
            width = int(width * ratio)
            height = int(height * ratio)
            degraded = True
        if target_fps > 0:
            degraded = True
        if degraded and not reason:
            reason = f'多路预览（{incoming_count}路）'

    video_bitrate = preset['nvenc_bitrate'] if use_nvenc else preset['x264_bitrate']
    crf_value = preset['x264_crf']
    return {
        'width': width,
        'height': height,
        'use_nvenc': use_nvenc,
        'video_bitrate': video_bitrate,
        'crf_value': crf_value,
        'fps': target_fps,
        'pressure_level': pressure_level,
        'pressure_reject': pressure_level == 'critical',
        'degraded': degraded,
        'reason': reason,
        'requested_width': preset_width,
        'requested_height': preset_height,
    }


def _preview_quality_response_fields(params: dict[str, Any]) -> dict[str, Any]:
    """Extract preview quality metadata for enable_preview responses."""
    fields: dict[str, Any] = {
        'width': int(params.get('width') or 0),
        'height': int(params.get('height') or 0),
        'fps': int(params.get('fps') or 0),
        'degraded': bool(params.get('degraded')),
    }
    reason = str(params.get('reason') or '').strip()
    if reason:
        fields['reason'] = reason
    return fields


def _mse_preview_success_response(
    room_id: str,
    data: dict | None,
    *,
    note: str,
) -> dict[str, Any]:
    """Build enable_preview success payload with actual preview quality metadata."""
    preview_params = _compute_preview_quality_params(data)
    response: dict[str, Any] = {
        'success': True,
        'room_id': room_id,
        'note': note,
    }
    response.update(_preview_quality_response_fields(preview_params))
    return response


def _configure_shared_preview_quality(shared_ingest, data: dict | None = None) -> None:
    """Compute preview quality params and configure them on the shared ingest."""
    params = _compute_preview_quality_params(data)
    # 过滤掉 configure_preview 不接受的参数
    valid_keys = {'width', 'height', 'use_nvenc', 'video_bitrate', 'crf_value', 'fps'}
    filtered = {k: v for k, v in params.items() if k in valid_keys}
    shared_ingest.configure_preview(**filtered)


def _bind_shared_ingest_lease(mgr, room_id: str, ingest) -> None:
    bind = getattr(ingest, "bind_lease", None)
    if ingest is None or not callable(bind):
        return
    lease = getattr(mgr, "_stream_leases", {}).get(room_id)
    lease_manager = getattr(mgr, "_lease_managers", {}).get(room_id)
    if lease is None or lease_manager is None:
        return
    bind(lease_manager, getattr(lease, "lease_id", ""))


def _shared_preview_reconnect_ready(result: object, ingest: object | None) -> bool:
    """Reconnect is ready only when the handler succeeded and upstream is live."""
    if not isinstance(result, dict) or not result.get("success") or ingest is None:
        return False
    checker = getattr(ingest, "upstream_is_live", None)
    if callable(checker):
        return bool(checker())
    return getattr(ingest, "process_id", None) is not None


# 正在启动 MSE 的 room_id 集合，防止启动过程中重复请求
_mse_starting: set[str] = set()
_mse_starting_lock = threading.Lock()
# 已广播 preview_phase=streaming 的房间（首个 init 段到达后置位）。
# 启动/重连成功时 discard，让新 streamer 的 init 触发一次 streaming 广播。
_mse_live_phase: set[str] = set()

# MSE 预览自动重连状态: {room_id: {"attempts": int}}
_mse_reconnect_state: dict[str, dict[str, Any]] = {}
_MSE_MAX_RECONNECT = 3
_MSE_RECONNECT_BASE_DELAY = 2.0
_MSE_RECONNECT_MAX_DELAY = 30.0
DURABLE_SUCCESS_SEC = 30.0


def _reconnect_attempts_after_event(
    state: dict | None,
    *,
    event: str,
    durable_sec: float = DURABLE_SUCCESS_SEC,
    now: float,
) -> dict:
    """Update MSE reconnect budget. event: accepted | media_ready | durable | exit | user_stop."""
    next_state = dict(state or {})
    next_state.setdefault("attempts", 0)
    if event in {"accepted", "media_ready"}:
        if event == "media_ready":
            next_state["media_ready_at"] = now
        next_state.pop("durable", None)
        return next_state
    if event == "durable":
        ready_at = float(next_state.get("media_ready_at") or 0.0)
        if ready_at and (now - ready_at) >= durable_sec:
            next_state["attempts"] = 0
            next_state["durable"] = True
        return next_state
    if event == "exit":
        if not next_state.get("durable"):
            next_state["attempts"] = int(next_state.get("attempts") or 0) + 1
        return next_state
    if event == "user_stop":
        return {}
    return next_state


def _begin_mse_reconnect(prev_state: dict | None) -> dict:
    """Keep the retry budget unless the previous session already went durable."""
    if prev_state and not prev_state.get("durable"):
        next_state = dict(prev_state)
        next_state["running"] = True
        return next_state
    return {"attempts": 0, "running": True}


def _note_mse_reconnect_event(room_id: str, event: str, *, now: float | None = None) -> dict:
    stamp = time.monotonic() if now is None else now
    state = _reconnect_attempts_after_event(
        _mse_reconnect_state.get(room_id),
        event=event,
        now=stamp,
    )
    if event in {"accepted", "media_ready"}:
        state["running"] = False
    if event == "user_stop" or not state:
        _mse_reconnect_state.pop(room_id, None)
        return {}
    _mse_reconnect_state[room_id] = state
    return state


async def _watch_mse_reconnect_durable(
    room_id: str,
    *,
    ready_at: float,
    broadcast,
    durable_sec: float = DURABLE_SUCCESS_SEC,
) -> None:
    await asyncio.sleep(max(0.0, float(durable_sec)))
    state = _mse_reconnect_state.get(room_id) or {}
    if state.get("running"):
        return
    if float(state.get("media_ready_at") or 0.0) != float(ready_at):
        return
    updated = _reconnect_attempts_after_event(
        state,
        event="durable",
        now=time.monotonic(),
        durable_sec=durable_sec,
    )
    if not updated.get("durable"):
        return
    _mse_reconnect_state.pop(room_id, None)
    try:
        await broadcast()
    except Exception as exc:
        _log.debug("mse durable reconnect broadcast failed room=%s: %s", room_id, exc)


def _is_stream_info_offline(info) -> bool:
    if info is None:
        return False
    return not info.is_live or _is_stream_offline_error(info.error or '')


def _mse_offline_error_message(raw: str = '') -> str:
    if raw:
        friendly = humanize_error(raw)
        if friendly:
            return friendly
    return '主播已下线'


def _probe_stream_offline(mgr: RoomOrchestrator, room_id: str) -> tuple[bool, str]:
    """确认直播间已下播，必要时强制解析。返回 (is_offline, friendly_message)。"""
    room = mgr.get_room(room_id)
    if room is None:
        return False, ''
    if _is_stream_offline_error(room.last_error or ''):
        return True, _mse_offline_error_message(room.last_error)
    if _is_stream_info_offline(room.stream_info):
        raw = (room.stream_info.error if room.stream_info else '') or room.last_error or ''
        return True, _mse_offline_error_message(raw)
    try:
        platform = str(
            getattr(room, "platform", "")
            or getattr(room, "platform_name", "")
            or detect_platform(room.room_url)
            or ""
        )
        cfg = load_config()
        use_v2 = all(
            is_platform_pipeline_component_enabled(component, platform, cfg)
            for component in (
                "unified_resolver_v2",
                "media_probe_v2",
                "stream_lease_v2",
            )
        )
        if use_v2:
            from lsc.platforms.models import ResolveRequest
            from lsc.platforms.resolver import resolve_stream_v2

            result = resolve_stream_v2(
                ResolveRequest(
                    source_url=room.room_url,
                    force_refresh=True,
                    request_id=f"offline-probe:{room_id}",
                    network_context=dict(getattr(room, "network_context", {}) or {}),
                )
            )
            error = getattr(result, "error", None)
            if str(getattr(result, "live_status", "") or "").upper() == "OFFLINE":
                return True, _mse_offline_error_message(
                    getattr(error, "user_message", "") if error else ""
                )
            if error is not None and str(getattr(error, "category", "")) == "OFFLINE":
                return True, _mse_offline_error_message(
                    getattr(error, "user_message", "") or ""
                )
            return False, ""

        from lsc.platforms.registry import parse_stream
        info = parse_stream(room.room_url, force_refresh=True)
    except Exception as exc:
        _log.debug("offline probe parse failed for %s: %s", room_id, exc)
        return False, ''
    if _is_stream_info_offline(info):
        return True, _mse_offline_error_message(info.error or '')
    return False, ''


_offline_file_review_in_progress: set[str] = set()


def _clear_mse_push_paused(room_id: str) -> None:
    """预览停止时清除 backpressure 暂停标记，避免残留导致重开后丢帧。"""
    with _mse_push_paused_lock:
        _mse_push_paused.discard(room_id)


def _stop_live_preview_streamer(room_id: str) -> None:
    """停止房间的直播 CDN / 共享进样预览流（为文件回看让路）。"""
    old = _preview_stream_registry().pop(room_id)
    if old is not None:
        try:
            old.stop()
        except Exception as exc:
            _log.debug("停止直播预览 streamer 失败: %s", exc)
    try:
        ingest = _shared_ingests.get(room_id)
        if ingest is not None:
            stop_preview = getattr(ingest, "stop_preview_sink", None)
            if callable(stop_preview):
                try:
                    stop_preview()
                except Exception as exc:
                    _log.debug("stop_preview_sink 失败: %s", exc)
    except Exception as exc:
        _log.debug("shared ingest preview stop lookup failed: %s", exc)
    _clear_mse_push_paused(room_id)
    _stop_idle_shared_ingest(room_id, reason="offline file review cleanup")


def _is_normal_file_playback_end(error_text: str) -> bool:
    if not error_text:
        return True
    lowered = error_text.lower()
    markers = ("end of file", "eof", "播放结束", "review ended", "nothing to read")
    return any(marker in lowered for marker in markers)


async def _start_recording_file_mse(
    srv,
    mgr: RoomOrchestrator,
    bridge,
    room_id: str,
    loop,
    *,
    offline_message: str = "",
    stop_recording_if_active: bool = True,
) -> tuple[bool, str]:
    """下播确认后切换为录制文件 MSE 回看。返回 (success, error_message)。"""
    if room_id in _offline_file_review_in_progress:
        return False, "offline file review already in progress"
    _offline_file_review_in_progress.add(room_id)
    try:
        def _read_preview_state():
            room = mgr.get_room(room_id)
            if room is None:
                return None
            return {
                "preview_enabled": bool(room.preview_enabled),
                "is_recording": bool(room.is_recording),
                "record_output_path": room.record_output_path or "",
            }

        try:
            state = await loop.run_in_executor(
                _bridge_executor, lambda: bridge.manager.call(_read_preview_state)
            )
        except Exception as exc:
            _log.error("offline file review state read failed: %s", exc)
            return False, str(exc)

        if state is None:
            return False, "房间不存在"
        if not state["preview_enabled"]:
            return False, "预览未开启"

        if stop_recording_if_active and state["is_recording"]:
            def _stop_recording():
                mgr.stop_recording_async(room_id)
                return True

            try:
                await loop.run_in_executor(
                    _bridge_executor, lambda: bridge.manager.call(_stop_recording, timeout=10.0)
                )
            except Exception as exc:
                _log.warning("offline file review stop recording failed: %s", exc)
            await asyncio.sleep(0.5)

        def _stop_live():
            _stop_live_preview_streamer(room_id)
            return True

        await loop.run_in_executor(_bridge_executor, lambda: bridge.manager.call(_stop_live))

        def _validate_recording_path():
            from lsc.recorder.capture import validate_recording

            room = mgr.get_room(room_id)
            if room is None:
                return False, "", "房间不存在"
            path = room.record_output_path or ""
            valid, err = validate_recording(path)
            return valid, path, err

        try:
            valid, path, validation_err = await loop.run_in_executor(
                _recording_executor, lambda: bridge.manager.call(_validate_recording_path)
            )
        except Exception as exc:
            _log.error("offline file review validate failed: %s", exc)
            valid, path, validation_err = False, "", str(exc)

        if not valid or not path:
            friendly = validation_err or "录制文件无效，无法回看"
            if offline_message and friendly:
                friendly = f"{offline_message}（{friendly}）"
            elif offline_message:
                friendly = offline_message

            def _set_degraded():
                room = mgr.get_room(room_id)
                if room is not None:
                    room.preview_mode = "degraded"
                    room.preview_enabled = False
                    room.preview_error = friendly
                    if offline_message:
                        room.last_error = offline_message
                return True

            try:
                await loop.run_in_executor(
                    _bridge_executor, lambda: bridge.manager.call(_set_degraded)
                )
            except Exception as exc:
                _log.error("offline degraded state update failed: %s", exc)
            bridge.queue_broadcast({
                "type": "rooms_updated",
                "data": {"rooms": _rooms_list(mgr)},
            })
            await srv.broadcast("preview_phase", {"room_id": room_id, "phase": "error"})
            return False, friendly

        preview_params = _compute_preview_quality_params({})
        width = int(preview_params.get("width") or 0)
        height = int(preview_params.get("height") or 0)
        fps = int(preview_params.get("fps") or 0)
        video_bitrate = preview_params.get("video_bitrate")
        crf_value = preview_params.get("crf_value")

        async def _on_file_mse_error(err: str) -> None:
            if _is_normal_file_playback_end(err):
                _log.info("Recording file review ended quietly for %s", room_id)
                ended = _preview_stream_registry().pop(room_id)
                if ended is not None:
                    try:
                        await loop.run_in_executor(_bridge_executor, ended.stop)
                    except Exception as exc:
                        _log.debug("file review streamer stop failed: %s", exc)
                await srv.broadcast("preview_phase", {"room_id": room_id, "phase": "idle"})
                return
            _log.warning("Recording file review error for %s: %s", room_id, err)
            def _set_degraded():
                room = mgr.get_room(room_id)
                if room is not None:
                    room.preview_mode = "degraded"
                    room.preview_enabled = False
                    room.preview_error = err or "录制回看失败"
                return True

            try:
                await loop.run_in_executor(
                    _bridge_executor, lambda: bridge.manager.call(_set_degraded)
                )
            except Exception as exc:
                _log.debug("file review degraded update failed: %s", exc)
            bridge.queue_broadcast({
                "type": "rooms_updated",
                "data": {"rooms": _rooms_list(mgr)},
            })
            await srv.broadcast("preview_phase", {"room_id": room_id, "phase": "error"})

        def _start_file_streamer():
            try:
                streamer = MseStreamer(
                    url=path,
                    is_file=True,
                    width=width,
                    height=height,
                    fps=fps,
                    video_bitrate=video_bitrate,  # type: ignore[arg-type]
                    crf_value=crf_value,  # type: ignore[arg-type]
                    on_init_segment=lambda seg, _room_id=room_id: _push_mse_segment(  # type: ignore[misc]
                        srv, loop, 'mse_init', _room_id, seg
                    ),
                    on_media_segment=lambda seg, _room_id=room_id: _push_mse_segment(  # type: ignore[misc]
                        srv, loop, 'mse_segment', _room_id, seg
                    ),
                    on_error=lambda err, _room_id=room_id: asyncio.run_coroutine_threadsafe(  # type: ignore[misc,arg-type]
                        _on_file_mse_error(err), loop
                    ),
                )
                ok = streamer.start(startup_probe_timeout=5.0)
                if ok:
                    _preview_stream_registry().set_legacy(room_id, streamer)
                    return True, ""
                stderr_tail = ""
                try:
                    stderr_tail = (streamer._last_stderr or "").strip()[:300]
                except AttributeError:
                    pass
                try:
                    streamer.stop()
                except Exception as exc:
                    _log.debug("停止失败的文件 streamer 失败: %s", exc)
                return False, stderr_tail or "文件预览启动失败"
            except Exception as exc:
                _log.error("file MSE start failed: %s", exc)
                return False, str(exc)

        try:
            started, start_err = await loop.run_in_executor(
                _recording_executor, _start_file_streamer
            )
        except Exception as exc:
            started, start_err = False, str(exc)

        if not started:
            def _set_degraded():
                room = mgr.get_room(room_id)
                if room is not None:
                    room.preview_mode = "degraded"
                    room.preview_enabled = False
                    room.preview_error = start_err or "录制回看启动失败"
                return True

            try:
                await loop.run_in_executor(
                    _bridge_executor, lambda: bridge.manager.call(_set_degraded)
                )
            except Exception as exc:
                _log.debug("file review start degraded update failed: %s", exc)
            bridge.queue_broadcast({
                "type": "rooms_updated",
                "data": {"rooms": _rooms_list(mgr)},
            })
            await srv.broadcast("preview_phase", {"room_id": room_id, "phase": "error"})
            return False, start_err or "录制回看启动失败"

        def _set_review_mode():
            room = mgr.get_room(room_id)
            if room is not None:
                new_epoch = uuid4().hex
                room.preview_enabled = True
                room.preview_mode = "recording_review"
                room.preview_error = ""
                room.preview_epoch_id = new_epoch
                get_timeline_service().on_preview_epoch_change(room_id, new_epoch)
            return True

        try:
            await loop.run_in_executor(
                _bridge_executor, lambda: bridge.manager.call(_set_review_mode)
            )
        except Exception as exc:
            leak = _preview_stream_registry().pop(room_id)
            if leak is not None:
                try:
                    await loop.run_in_executor(_bridge_executor, leak.stop)
                except Exception as stop_exc:
                    _log.debug("file review leak cleanup failed: %s", stop_exc)
            return False, f"预览状态同步失败：{exc}"

        _mse_reconnect_state.pop(room_id, None)
        bridge.queue_broadcast({
            "type": "rooms_updated",
            "data": {"rooms": _rooms_list(mgr)},
        })
        await srv.broadcast("preview_phase", {"room_id": room_id, "phase": "streaming"})
        _log.info("Switched room %s to recording file MSE review: %s", room_id, path)
        return True, ""
    finally:
        _offline_file_review_in_progress.discard(room_id)


def _invalidate_room_timeline(room_id: str, reason: str = "") -> None:
    """若房间绑定了活动 TimelineContext，则使其失效（不删除 ClipSnapshot）。"""
    svc = get_timeline_service()
    ctx = svc.get_active_timeline_for_room(room_id)
    if ctx is None:
        return
    svc.invalidate_timeline(ctx.timeline_id, reason or f"room_lifecycle:{room_id}")


# 专用线程池：录制操作（HTTP 刷新 + FFmpeg 启动）可阻塞 30s+，独立线程池避免饿死快操作
_recording_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix='rec')
# 快操作线程池：disconnect/mute/seek 等 orchestrator.call 操作，预期 <1s 完成
_bridge_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix='bridge')
# AI 分析专用线程池：CPU/GPU 密集型，独立线程池避免与录制/导出竞争
_ai_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='ai')
# FFprobe 探针专用线程池：-probesize 5M + -analyzeduration 2M 可阻塞 10s+，
# 独立线程池避免阻塞 bridge 快操作（disconnect/mute/seek）和录制操作（#20）
_probe_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='probe')

# 录制并发限流：最多同时启动 2 路录制，避免 6 路同时 HTTP 刷新 + FFmpeg 启动耗尽线程和 CPU
_recording_semaphore = asyncio.Semaphore(2)
# 正在提交录制启动的 room_id 集合，防止同一房间重复提交
_recording_starting: set[str] = set()
# 等待录制并发槽位的 room_id 队列（Semaphore 已满时）
_recording_wait_queue: list[str] = []


def shutdown_room_handlers(timeout_sec: float = 10.0) -> dict[str, int]:
    """Stop handler-owned background work before backend process exit."""
    stats = {
        "continuous_tasks_cancelled": 0,
        "mse_streamers_stopped": 0,
        "shared_ingests_stopped": 0,
        "executors_shutdown": 0,
    }

    with _analysis_jobs_lock:
        for room_id, state in list(_continuous_tasks.items()):
            state["cancelled"] = True
            task = state.get("task")
            cancel = getattr(task, "cancel", None)
            if callable(cancel):
                try:
                    cancel()
                except Exception as exc:
                    _log.debug("cancel continuous task failed room_id=%s: %s", room_id, exc)
            stats["continuous_tasks_cancelled"] += 1
        _continuous_tasks.clear()
        _analysis_jobs.clear()

    with _mse_starting_lock:
        _mse_starting.clear()
    _mse_reconnect_state.clear()
    _mse_live_phase.clear()
    _recording_starting.clear()
    _recording_wait_queue.clear()
    with _export_jobs_lock:
        export_jobs.clear()

    streamers = _preview_stream_registry().clear_items()
    for room_id, streamer in streamers:
        stop = getattr(streamer, "stop", None)
        if callable(stop):
            try:
                stop()
                stats["mse_streamers_stopped"] += 1
            except Exception as exc:
                _log.warning("stop MSE streamer failed room_id=%s: %s", room_id, exc)

    stop_all_shared = getattr(_shared_ingests, "stop_all", None)
    if callable(stop_all_shared):
        try:
            stats["shared_ingests_stopped"] = int(stop_all_shared(reason="handler shutdown") or 0)
        except Exception as exc:
            _log.warning("stop shared ingests failed during shutdown: %s", exc)

    for name, executor in (
        ("recording", _recording_executor),
        ("bridge", _bridge_executor),
        ("ai", _ai_executor),
        ("probe", _probe_executor),
    ):
        shutdown = getattr(executor, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown(wait=False, cancel_futures=True)
                stats["executors_shutdown"] += 1
            except TypeError:
                shutdown(wait=False)
                stats["executors_shutdown"] += 1
            except Exception as exc:
                _log.warning("shutdown %s executor failed: %s", name, exc)

    _log.info("room handlers shutdown complete timeout_sec=%.1f stats=%s", timeout_sec, stats)
    return stats


def _wait_for_recording_file(room, timeout_sec: float = 8.0) -> bool:
    """等待录制文件物理创建（FFmpeg 启动延迟）。

    录制启动后 record_output_path 会立即设置，但 FFmpeg 子进程需要
    2-5 秒才真正创建文件。此函数在超时内轮询等待文件出现。
    若录制进程已退出但文件仍未出现，立即返回 False 避免无效等待。
    """
    manifest_path = getattr(room, "record_manifest_path", "") or ""
    path = getattr(room, "record_output_path", "") or ""

    def _asset_ready() -> bool:
        if manifest_path and os.path.isfile(manifest_path):
            try:
                from lsc.recorder.assets import RecordingAsset

                return bool(RecordingAsset.recover(manifest_path).segment_paths())
            except (OSError, ValueError, RuntimeError):
                return False
        return bool(path and os.path.isfile(path))

    if _asset_ready():
        return True
    if not path and not manifest_path:
        return False

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if not getattr(room, "is_recording", False):
            _log.warning("等待录制文件时录制已停止: room=%s", getattr(room, "room_id", "?"))
            return _asset_ready()
        room_refreshed = getattr(room, "record_output_path", "")
        if room_refreshed and os.path.isfile(room_refreshed) or _asset_ready():
            return True
    return False


def _shared_ingest_v2_enabled(manager: RoomOrchestrator, room_id: str) -> bool:
    """Enable the shared supervisor path even when the legacy global switch is off."""
    try:
        cfg = load_config()
        room = manager.get_room(room_id)
        platform = ""
        if room is not None:
            platform = str(
                getattr(room, "platform", "")
                or getattr(room, "platform_name", "")
                or detect_platform(str(getattr(room, "room_url", "") or ""))
                or ""
            )
        if is_platform_v2_hard_blocked(platform):
            _log.info(
                "platform V2 hard-blocked, using legacy ingest platform=%s room=%s",
                platform,
                room_id,
            )
            return False
        if bool(getattr(cfg, "shared_ingest_enabled", False)):
            return True
        if room is None:
            return False
        return is_platform_pipeline_component_enabled(
            "ingest_supervisor_v2",
            platform,
            cfg,
        )
    except Exception as exc:
        _log.debug("shared ingest V2 gate lookup failed room=%s: %s", room_id, redact_text(exc))
        return False


def _validate_synced_analysis_targets(
    manager,
    main_room_id,
    target_room_ids,
    wait_for_file: bool = False,
) -> tuple[bool, str, Any | None, list[Any]]:
    main_room = manager.get_room(main_room_id)
    if main_room is None:
        return False, "主房间不存在", None, []

    def _has_recording_asset(room: Any) -> bool:
        manifest_path = getattr(room, "record_manifest_path", "") or ""
        if manifest_path and os.path.isfile(manifest_path):
            try:
                from lsc.recorder.assets import RecordingAsset

                return bool(RecordingAsset.recover(manifest_path).segment_paths())
            except (OSError, ValueError, RuntimeError):
                return False
        record_path = getattr(room, "record_output_path", "") or ""
        return bool(record_path and os.path.isfile(record_path))

    if not _has_recording_asset(main_room) and (
        not wait_for_file or not _wait_for_recording_file(main_room)
    ):
        return False, "主房间录制文件不存在", None, []

    seen: set[str] = set()
    unique_target_ids: list[str] = []
    if main_room_id:
        seen.add(main_room_id)
        unique_target_ids.append(main_room_id)
    for room_id in target_room_ids or []:
        if not room_id or room_id in seen:
            continue
        seen.add(room_id)
        unique_target_ids.append(room_id)

    multi_room = len(unique_target_ids) > 1
    main_group = getattr(main_room, "align_group_id", "") or ""
    if multi_room and not main_group:
        return False, "主房间未对齐，请先一键对齐", None, []

    target_rooms: list[Any] = []
    for room_id in unique_target_ids:
        room = manager.get_room(room_id)
        if room is None:
            return False, f"目标房间不存在: {room_id}", None, []
        if not _has_recording_asset(room) and (
            not wait_for_file or not _wait_for_recording_file(room)
        ):
            return False, f"目标房间录制文件不存在: {room_id}", None, []
        if multi_room and (getattr(room, "align_group_id", "") or "") != main_group:
            return False, f"房间 {room_id} 与主房间不在同一对齐组，请重新一键对齐", None, []
        target_rooms.append(room)

    return True, "", main_room, target_rooms


def _recording_media_start(room: Any) -> float:
    """返回录制文件时间轴真正的媒体起点，旧会话回退到进程启动时间。"""
    return float(
        getattr(room, "recording_media_start_mono", None)
        or getattr(room, "recording_start_mono", 0.0)
        or 0.0
    )


def _map_highlight_to_room(highlight, main_room, target_room) -> dict[str, Any]:
    source_start = float(highlight.get("start", 0) or 0)
    source_end = float(highlight.get("end", 0) or 0)
    main_rec = _recording_media_start(main_room)
    target_rec = _recording_media_start(target_room)
    delta = (main_rec - target_rec) + (
        float(getattr(main_room, "content_offset", 0.0) or 0.0)
        - float(getattr(target_room, "content_offset", 0.0) or 0.0)
    )
    mapped = dict(highlight)
    mapped.update({
        "start": max(0.0, source_start + delta),
        "end": max(0.0, source_end + delta),
        "room_id": getattr(target_room, "room_id", ""),
        "source_room_id": getattr(main_room, "room_id", ""),
        "source_start": source_start,
        "source_end": source_end,
        "offset_delta": delta,
    })
    return mapped


def _map_highlights_by_room(highlights, main_room, target_rooms) -> dict[str, list[dict[str, Any]]]:
    mapped_by_room: dict[str, list[dict[str, Any]]] = {
        getattr(room, "room_id", ""): [] for room in target_rooms
    }
    for highlight in highlights:
        source_start = float(highlight.get("start", 0) or 0)
        source_end = float(highlight.get("end", 0) or 0)
        if source_start >= source_end:
            continue
        for room in target_rooms:
            room_id = getattr(room, "room_id", "")
            mapped = _map_highlight_to_room(highlight, main_room, room)
            if float(mapped.get("start", 0) or 0) < float(mapped.get("end", 0) or 0):
                mapped_by_room[room_id].append(mapped)
    return mapped_by_room


def _detect_audio_energy_peaks(*args, **kwargs):
    """兼容包装：逻辑已迁至 lsc.analyzer.scene_analysis。"""
    from lsc.analyzer.scene_analysis import detect_audio_energy_peaks
    return detect_audio_energy_peaks(*args, **kwargs)


def _detect_rounds_by_audio_rhythm(*args, **kwargs):
    """兼容包装：音频回合检测已随纯 OCR 简化移除，恒返回空列表。"""
    _log.warning("detect_rounds_by_audio_rhythm 已废弃（纯 OCR 路径），返回空结果")
    return []


def _new_rounds(
    prev: list[dict[str, Any]],
    current: list[dict[str, Any]],
    overlap_tol: float = 5.0,
) -> list[dict[str, Any]]:
    """全量重扫下的回合级去重：返回 current 中与 prev 无时间重叠的全新回合。

    持续分析每轮 detect_valorant_rounds 全量重扫产出从头到当前的完整回合集，
    大部分回合与上一轮重复（边界可能微调）。本函数按时间区间重叠判定：current
    中某回合若与 prev 任一回合有实质重叠（重叠 > overlap_tol 秒），视为已存在，
    否则视为新回合。仅用于前端"新增 N 个高光"增量提示，不影响累计集本身。
    """
    if not prev:
        return list(current)
    fresh: list[dict[str, Any]] = []
    for cur in current:
        cs, ce = cur.get('start', 0.0), cur.get('end', 0.0)
        overlaps = False
        for p in prev:
            ps, pe = p.get('start', 0.0), p.get('end', 0.0)
            inter = min(ce, pe) - max(cs, ps)
            if inter > overlap_tol:
                # A pending round may receive a confirmed OCR end on a later
                # trailing scan; let that update become a new export candidate.
                if (
                    not _is_auto_exportable_valorant_round(p)
                    and _is_auto_exportable_valorant_round(cur)
                ):
                    if p.get("round_key") and not cur.get("round_key"):
                        cur = dict(cur)
                        cur["round_key"] = p["round_key"]
                    continue
                overlaps = True
                break
        if not overlaps:
            fresh.append(cur)
    return fresh


def _drop_open_tail_rounds(
    rounds: list[dict[str, Any]],
    current_dur: float,
    tail_margin: float = 5.0,
) -> list[dict[str, Any]]:
    """持续分析只发布已闭合回合：过滤仍贴着录制尾部的未闭合回合。

    - end >= current_dur - 3s：尾部数据不完整，直接丢弃
    - end >= current_dur - 20s：回合可能仍在进行中，标记为 phase="pending" 保留
    - 明确闭合（end_by 在 _OCR_VALID_END_BY）的回合按 tip 距离保留：
      结算帧本来就贴近扫描尖端，误删会导致「有交战内容但 0 回合」。
    """
    if not rounds:
        return []
    cleaned = list(rounds)
    last = cleaned[-1]
    try:
        end = float(last.get("end", 0.0))
    except (TypeError, ValueError):
        end = 0.0
    if last.get("tail_by") == "open_tail":
        last["phase"] = "pending"
        return cleaned
    end_by = str(last.get("end_by", "") or "")
    if end_by in _OCR_VALID_END_BY:
        return cleaned
    if end >= current_dur - tail_margin:
        return cleaned[:-1]
    pending_margin = 20.0
    if end >= current_dur - pending_margin:
        last["phase"] = "pending"
    return cleaned


def _valorant_vision_shadow_enabled() -> bool:
    """Pre-cutover shadow: run OCR detection but skip clip_queued / listing."""
    return os.environ.get("LSC_VALORANT_VISION_SHADOW", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _is_ocr_round(round_data: dict[str, Any]) -> bool:
    """纯 OCR 路径（顶部条 + 中央横幅）产出的回合。"""
    return round_data.get("boundary_source") == _OCR_BOUNDARY_SOURCE


def _hybrid_clip_metadata(round_data: dict[str, Any]) -> dict[str, Any]:
    """Return the small evidence payload forwarded to clip_queued."""
    keys = (
        "boundary_source",
        "boundary_evidence",
        "model_version",
        "start_confidence",
        "end_confidence",
        "boundary_confidence",
    )
    return {key: round_data[key] for key in keys if key in round_data}


def _min_highlight_duration_for_queue(*, list_only: bool) -> float:
    """入列下限：纯 OCR 确认的回合（入点+出点齐备）直接入列导出。"""
    if list_only:
        return _VALORANT_MIN_LIST_DURATION_SEC
    return _VALORANT_MIN_LIST_DURATION_SEC


def _is_listable_ocr_round(round_data: dict[str, Any]) -> bool:
    """纯 OCR 回合入列条件：boundary_source + confirm_status + 有效边界。"""
    if not _is_ocr_round(round_data):
        return False
    if round_data.get("confirm_status") not in ("vision_confirmed", "pending"):
        return False
    try:
        start = float(round_data.get("start", 0.0))
        end = float(round_data.get("end", 0.0))
    except (TypeError, ValueError):
        return False
    if end <= start:
        return False
                # 异常时长守卫：过长回合不得自动导出，降级 pending 待人工复核（仍入列）
    if end - start > _VALORANT_MAX_ROUND_DURATION_SEC:
        round_data['duration_anomaly'] = True
        if round_data.get('confirm_status') == 'vision_confirmed':
            round_data['confirm_status'] = 'pending'
            _log.warning(
                "OCR 回合时长异常 %.1fs > %.0fs，降级 pending 待复核: round=%s",
                end - start,
                _VALORANT_MAX_ROUND_DURATION_SEC,
                _valorant_round_key(round_data),
            )
    start_by = str(round_data.get("start_by", "") or "")
    end_by = str(round_data.get("end_by", "") or "")
    return start_by in _OCR_VALID_START_BY and end_by in _OCR_VALID_END_BY


def _is_auto_exportable_valorant_round(round_data: dict[str, Any]) -> bool:
    """Return whether an OCR round is exportable (vision_confirmed + valid boundaries)."""
    if not _is_ocr_round(round_data):
        return False
    try:
        start = float(round_data.get("start", 0.0))
        end = float(round_data.get("end", 0.0))
    except (TypeError, ValueError):
        return False
    if end <= start:
        return False
    if round_data.get("confirm_status") != "vision_confirmed":
        return False
    start_by = str(round_data.get("start_by", "") or "")
    end_by = str(round_data.get("end_by", "") or "")
    return start_by in _OCR_VALID_START_BY and end_by in _OCR_VALID_END_BY


def _valorant_round_key(round_data: dict[str, Any]) -> str:
    """Return a boundary-stable key for one Valorant round."""
    existing = str(round_data.get("round_key") or "").strip()
    if existing:
        return existing
    try:
        start = float(round_data.get("start", 0.0))
    except (TypeError, ValueError):
        start = 0.0
    # ponytail: quantize the start only to absorb small OCR boundary drift.
    return f"round-{int(round(start / 10.0)):06d}"


# pending 切片边界自动 upsert 阈值（秒）；小于此抖动不广播
_CLIP_BOUNDS_UPSERT_THRESHOLD = 0.3


def _should_broadcast_clip_list_update(
    listed_key: str,
    round_key: str,
    start: float,
    end: float,
    confirm_status: str,
    *,
    listed_ids: dict[str, None],
    exported_ids: dict[str, None],
    refined_keys: set[str],
    listed_bounds: dict[str, tuple[float, float, str]],
    deleted_keys: dict[str, None] | None = None,
) -> str:
    """决定 list_only 路径是否广播 clip_queued。

    Returns
    -------
    ``"first"`` | ``"upsert"`` | ``"skip"``
    """
    if deleted_keys and listed_key in deleted_keys:
        return "skip"
    if round_key in refined_keys:
        return "skip"
    if listed_key in exported_ids:
        return "skip"
    if listed_key not in listed_ids:
        return "first"
    prev = listed_bounds.get(listed_key)
    if prev is None:
        return "upsert"
    prev_start, prev_end, prev_status = prev
    status_changed = (confirm_status or "") != (prev_status or "")
    bounds_changed = (
        abs(float(start) - float(prev_start)) >= _CLIP_BOUNDS_UPSERT_THRESHOLD
        or abs(float(end) - float(prev_end)) >= _CLIP_BOUNDS_UPSERT_THRESHOLD
    )
    if status_changed or bounds_changed:
        return "upsert"
    return "skip"


def _should_skip_continuous_scan_kick(
    state: dict[str, Any],
    scan_range: tuple[float, float],
    *,
    full_rescan: bool,
    use_ocr: bool,
    finalize: bool,
) -> bool:
    """同 scan_range 且 OCR 意图未变时跳过 kick；finalize 永不跳过。"""
    if finalize:
        return False
    # OCR/扫描失败后必须允许同窗重试，否则会永久卡在 skip-kick
    if state.get("last_scan_error"):
        return False
    phase = "full" if full_rescan else "incremental"
    return (
        state.get("scan_range") == scan_range
        and state.get("scan_phase") == phase
        and bool(state.get("refine_with_ocr")) == bool(use_ocr)
    )


def _is_timeout_scan_error(err: Any) -> bool:
    """识别扫描 TimeoutError（str() 常为空，须看 repr / 类型名）。"""
    if err is None:
        return False
    if isinstance(err, (TimeoutError, asyncio.TimeoutError)):
        return True
    text = err if isinstance(err, str) else repr(err)
    return "TimeoutError" in text


def _is_model_contract_error(err: Any) -> bool:
    text = err if isinstance(err, str) else repr(err)
    return "ModelContractError" in text


def _apply_scan_timeout_backoff(state: dict[str, Any]) -> None:
    """超时后仅记录失败计数（结果优先：不做降级，计数用于同窗重试上限判断）。"""
    failures = int(state.get("consecutive_scan_timeouts") or 0) + 1
    state["consecutive_scan_timeouts"] = failures


def _apply_scan_budget_degrade(
    state: dict[str, Any],
    *,
    scan_range: tuple[float, float],
    last_analyzed: float,
    use_ocr: bool,
) -> tuple[bool, tuple[float, float]]:
    """结果优先：任何情况下都不降级（不关 OCR、不缩窗），原样返回。"""
    return use_ocr, (float(scan_range[0]), float(scan_range[1]))


def _note_successful_scan_after_degrade(state: dict[str, Any]) -> None:
    """成功消费一次扫描后清除超时计数（不再有降级恢复流程）。"""
    state["consecutive_scan_timeouts"] = 0


def _continuous_listed_clip_snapshot(task: dict[str, Any]) -> list[dict[str, Any]]:
    listed_clip_state = task.get("listed_clips") or {}
    if isinstance(listed_clip_state, dict):
        return [dict(item) for item in listed_clip_state.values() if isinstance(item, dict)]
    if isinstance(listed_clip_state, list):
        return [dict(item) for item in listed_clip_state if isinstance(item, dict)]
    return []


def _build_continuous_status_payload(
    task: dict[str, Any],
    *,
    room_id: str,
    recorded_duration: float | None = None,
    analysis_stage: str | None = None,
    phase: str | None = None,
    all_highlights: list | None = None,
    last_analyzed: float | None = None,
    current_dur: float | None = None,
    effective_interval: float | None = None,
    include_listed: bool = True,
) -> dict[str, Any]:
    """构造 continuous_analysis_status / GET 共用载荷。"""
    highlights = all_highlights if all_highlights is not None else task.get("highlights", [])
    analyzed = float(
        last_analyzed if last_analyzed is not None else task.get("last_analyzed", 0.0) or 0.0
    )
    rec_dur = float(
        recorded_duration
        if recorded_duration is not None
        else task.get("recorded_duration", analyzed) or 0.0
    )
    cur = float(current_dur if current_dur is not None else rec_dur)
    stage = analysis_stage if analysis_stage is not None else task.get("analysis_stage", "分析中")
    # confirmed_rounds 缺省必须是 0，禁止回退 len(highlights)
    confirmed = int(task.get("confirmed_rounds", 0) or 0)
    pending = int(task.get("pending_rounds", 0) or 0)
    if "pending_rounds" not in task and highlights:
        pending = max(0, len(highlights) - confirmed)
    finalizing = bool(task.get("finalizing") or phase == "finalizing")
    resolved_phase = phase or ("finalizing" if finalizing else "running")
    # 高频 tick 广播（include_listed=False）不带全量 listed_clips，仅 GET/恢复路径带权威快照，
    # 避免每 3s 序列化整个 clip 列表（前端不消费该字段，切片列表由 clip_queued 事件驱动）。
    listed_clips = _continuous_listed_clip_snapshot(task) if include_listed else []
    # stopping/completed/idle/error 对前端均视为非「分析运行中」；stopping 另由 phase 驱动忙碌态。
    running = resolved_phase in ("running", "finalizing")
    payload: dict[str, Any] = {
        "running": running,
        "room_id": room_id,
        "target_room_ids": task.get("target_room_ids", []),
        "mode": task.get("mode", "scene"),
        "analyzed_duration": analyzed,
        "recorded_duration": rec_dur,
        "confirmed_rounds": confirmed,
        "pending_rounds": pending,
        "analysis_stage": stage,
        "total_highlights": len(highlights) if highlights is not None else 0,
        # Full authoritative snapshot: reconnecting/reloaded renderers reconcile
        # their ephemeral clip store instead of relying on one-shot broadcasts.
        "listed_clip_count": len(listed_clips),
        "phase": resolved_phase,
        "updated_at": time.time(),
        "scan_mode": task.get("scan_phase", "incremental"),
        "scan_range": (
            list(task.get("scan_range", (0.0, 0.0)))
            if isinstance(task.get("scan_range"), (list, tuple))
            else [0.0, 0.0]
        ),
        # 每轮扫描的实际输入范围，供状态栏明确提示本轮扫入/扫出位置。
        "scan_in_sec": task.get("scan_in_sec"),
        "scan_out_sec": task.get("scan_out_sec"),
        # 最近一次识别并入列（或待确认）的回合边界；无结果时为 null，不能
        # 复用旧值伪装成本轮新发现。
        "last_detected_in_sec": task.get("last_detected_in_sec"),
        "last_detected_out_sec": task.get("last_detected_out_sec"),
        "scan_timeout": task.get("scan_timeout", 120),
        "full_rescan": bool(task.get("full_rescan", False)),
        "refine_with_ocr": bool(task.get("refine_with_ocr", False)),
        "progress": (
            min(100.0, max(0.0, (analyzed / max(cur, 1.0)) * 100.0)) if cur else 0.0
        ),
        "scan_phase": "running" if task.get("scan_running") else task.get("scan_phase"),
        "scan_reason": "scanning" if task.get("scan_running") else task.get("scan_reason"),
        "scan_elapsed_sec": (
            round(time.monotonic() - task.get("_scan_start_mono", time.monotonic()), 1)
            if task.get("scan_running")
            else 0
        ),
        "scan_running": bool(task.get("scan_running", False)),
        "valorant_profile": task.get("valorant_profile"),
        "finalizing": finalizing,
        "completed": bool(task.get("completed", False)),
        "status": task.get("status", "running"),
        "analysis_lag_sec": max(0.0, rec_dur - analyzed) if rec_dur else 0.0,
        # 多房间同步详细错误（P3: 多房间同步详细错误）
        "mapping_error": task.get("mapping_error"),
        "last_scan_error": task.get("last_scan_error"),
        "degraded_mode": task.get("degraded_mode"),
        "consecutive_scan_timeouts": int(task.get("consecutive_scan_timeouts", 0) or 0),
    }
    if task.get("shadow_mode"):
        payload["shadow_mode"] = True
        payload["shadow_rounds_detected"] = int(task.get("shadow_rounds_detected", 0) or 0)
        payload["shadow_listable_rounds"] = int(task.get("shadow_listable_rounds", 0) or 0)
        payload["shadow_vision_confirmed"] = int(task.get("shadow_vision_confirmed", 0) or 0)
    if effective_interval is not None:
        payload["effective_interval"] = effective_interval
    if include_listed:
        payload["listed_clips"] = listed_clips
    return payload


def _is_ocr_quality_round(round_data: dict[str, Any]) -> bool:
    """OCR 边界回合（含未完全可导的 pending），合并时优先于纯音频。"""
    if round_data.get("ocr_confirmed"):
        return True
    start_by = str(round_data.get("start_by", "") or "")
    end_by = str(round_data.get("end_by", "") or "")
    return start_by in {"ocr_buy_exit", "ocr_prev_end", "ocr"} or end_by in {
        "ocr_result", "next_buy",
    }


def _merge_round_windows(
    existing: list[dict[str, Any]],
    window_rounds: list[dict[str, Any]],
    overlap_tol: float = 5.0,
) -> list[dict[str, Any]]:
    """Merge Valorant incremental window rounds with stable prior rounds.

    Window analysis may shift the same round by several seconds as the local
    audio threshold changes. If a new window round substantially overlaps an
    existing one, treat it as the newer boundary for that same round rather
    than keeping both.
    """
    if not existing:
        merged = [dict(item) for item in window_rounds]
        for item in merged:
            item.setdefault("round_key", _valorant_round_key(item))
        return merged
    if not window_rounds:
        return [dict(item) for item in existing]

    def _span(item: dict[str, Any]) -> tuple[float, float]:
        try:
            return float(item.get("start", 0.0)), float(item.get("end", 0.0))
        except (TypeError, ValueError):
            return 0.0, 0.0

    # OCR 回合优先：后续纯音频/full_round 不得覆盖；对照实测 round-000033 被吃掉。
    window_use: list[dict[str, Any]] = []
    superseded_old_keys: set[str] = set()
    for new in window_rounds:
        new_item = dict(new)
        new_start, new_end = _span(new_item)
        replaced_confirmed = False
        for old in existing:
            old_start, old_end = _span(old)
            if min(old_end, new_end) - max(old_start, new_start) <= overlap_tol:
                continue
            if old.get("round_key") and not new_item.get("round_key"):
                new_item["round_key"] = old["round_key"]
            old_hybrid = _is_ocr_round(old) or _is_listable_ocr_round(old)
            new_hybrid = _is_ocr_round(new_item) or _is_listable_ocr_round(new_item)
            if old_hybrid and not new_hybrid:
                replaced_confirmed = True
                break
            if new_hybrid or not old_hybrid:
                key = str(old.get("round_key") or "")
                if key:
                    superseded_old_keys.add(key)
                else:
                    superseded_old_keys.add(_valorant_round_key(old))
        if not replaced_confirmed:
            window_use.append(new_item)

    kept: list[dict[str, Any]] = []
    for old in existing:
        old_key = str(old.get("round_key") or _valorant_round_key(old))
        if old_key in superseded_old_keys:
            continue
        old_start, old_end = _span(old)
        overlaps_window = False
        for new in window_use:
            new_start, new_end = _span(new)
            if min(old_end, new_end) - max(old_start, new_start) > overlap_tol:
                overlaps_window = True
                break
        if not overlaps_window:
            kept.append(dict(old))

    merged = sorted(
        kept + window_use,
        key=lambda item: _span(item)[0],
    )
    for item in merged:
        item.setdefault("round_key", _valorant_round_key(item))
    # 轻微重叠：邻接对齐，禁止直接丢后段（丢回合）
    cleaned: list[dict[str, Any]] = []
    for item in merged:
        start, end = _span(item)
        if end - start < 5.0:
            continue
        if cleaned:
            prev_start, prev_end = _span(cleaned[-1])
            if start < prev_end:
                # 后段整体被前段覆盖 → 保留前段（通常是更早确认的 OCR）
                if end <= prev_end + overlap_tol:
                    continue
                # 轻微重叠：把后段起点推到前段终点
                start = prev_end
                if end - start < 5.0:
                    continue
                item = dict(item)
                item["start"] = round(start, 3)
        cleaned.append(dict(item))
    return cleaned


def _round_lists_changed(
    prev: list[dict[str, Any]],
    current: list[dict[str, Any]],
    tol: float = 0.5,
) -> bool:
    if len(prev) != len(current):
        return True
    for old, new in zip(prev, current, strict=False):
        try:
            old_start = float(old.get("start", 0.0))
            old_end = float(old.get("end", 0.0))
            new_start = float(new.get("start", 0.0))
            new_end = float(new.get("end", 0.0))
        except (TypeError, ValueError):
            return True
        if abs(old_start - new_start) > tol or abs(old_end - new_end) > tol:
            return True
    return False


def _continuous_valorant_refine_with_ocr(*args, **kwargs):
    """纯 OCR 路径恒开 OCR；保留恒 False 契约（无 legacy OCR refine 开关）。"""
    return False


def _finalize_scan_timeout(duration_sec: float, attempt: int = 1) -> int:
    """全文件 OCR 收尾超时（秒）。

    实测约 10 分钟录像 OCR 精修需 ~3 分钟；旧公式 ``dur/180*12+90``
    对 614s 只给 ~130s，会 TimeoutError 丢弃结果并把状态卡在「收尾中」。
    """
    dur = max(1.0, float(duration_sec))
    try:
        attempt_n = max(1, int(attempt))
    except (TypeError, ValueError):
        attempt_n = 1
    # 每分钟录像约 25s 预算 + 180s 基线；重试再加 120s；夹在 5–30 分钟
    base = int(dur / 60.0 * 25.0 + 180.0) + (attempt_n - 1) * 120
    return int(min(1800, max(300, base)))


def _clamp_post_stop_duration(
    probed_dur: float,
    last_recording_wallclock: float,
    *,
    slack_sec: float = _POST_STOP_DURATION_SLACK_SEC,
) -> float:
    """停录后钳制虚高 probe/码率估算时长，避免 finalize open_tail 出点飞到未来。"""
    probed = max(0.0, float(probed_dur or 0.0))
    wall = max(0.0, float(last_recording_wallclock or 0.0))
    slack = max(0.0, float(slack_sec))
    if wall > 0.0 and probed > wall + slack:
        return wall
    return probed


def _window_scan_timeout(*args, **kwargs):
    """兼容 tests/test_continuous_analysis_guards.py。"""
    from lsc.analyzer.valorant_plugin import window_scan_timeout
    return window_scan_timeout(*args, **kwargs)


def _continuous_valorant_scan_budget(*args, **kwargs):
    """兼容 tests/test_continuous_analysis_guards.py。"""
    from lsc.analyzer.valorant_plugin import compute_valorant_scan_budget
    return compute_valorant_scan_budget(*args, **kwargs)


def _continuous_effective_interval(
    interval: int,
    last_analyzed: float,
    valorant_incremental: bool,
    pressure: dict[str, Any] | None,
    *,
    round_phase: str | None = None,
    consecutive_timeouts: int = 0,
    ocr_degraded_remaining: int = 0,
) -> tuple[int, bool]:
    """Return continuous-analysis delay and whether this pass should skip.

    智能恢复逻辑（P2: 扫描超时智能恢复）：
    - 正常情况：基础间隔 * 压力倍率
    - 降级期（ocr_degraded_remaining > 0）：逐步恢复，每次成功减少降级计数
    - 连续超时：指数退避，但上限封顶
    - 买枪期：降低扫描频率（间隔拉长）
    - 战斗期：提高扫描频率（间隔缩短）
    """
    base_interval = max(5 if valorant_incremental else 10, int(interval))
    effective_interval = base_interval

    pressure = pressure or {}
    multiplier = pressure.get("analysis_interval_multiplier", 1)
    try:
        multiplier = max(1, int(multiplier))
    except (TypeError, ValueError):
        multiplier = 1

    if pressure.get("pause_analysis"):
        retry_after = pressure.get("retry_after_sec", effective_interval)
        try:
            return max(10, int(retry_after)), True
        except (TypeError, ValueError):
            return effective_interval * multiplier, True

    # 降级期：逐步恢复 OCR
    if ocr_degraded_remaining > 0:
        # 降级期使用较长间隔，但比完全跳过更积极
        effective_interval = max(effective_interval, 15)
        return effective_interval * multiplier, False

    # 连续超时退避（但不超过基础间隔的 4 倍）
    if consecutive_timeouts > 0:
        backoff = min(2 ** consecutive_timeouts, 4)
        effective_interval = int(effective_interval * backoff)

    # 基于相位的动态调整
    if round_phase and valorant_incremental:
        if round_phase == "buy":
            # 买枪期：降低频率（OCR 扫描买枪文字意义不大）
            effective_interval = int(effective_interval * 1.5)
        elif round_phase == "combat":
            # 战斗期：提高频率（需要精确捕捉回合边界）
            effective_interval = max(5, int(effective_interval * 0.7))
        elif round_phase in ("post_combat", "intermission"):
            # 等待结束/局间：正常频率
            pass

    return effective_interval * multiplier, False


def _cleanup_segments(segments: list[dict[str, Any]], min_duration: float = 5.0) -> list[dict[str, Any]]:
    """清理片段列表：过滤过短段、移除重叠、按时间排序。"""
    if not segments:
        return []

    # 过滤 < 5s 的垃圾片段
    filtered = [s for s in segments if s.get('end', 0) - s.get('start', 0) >= 5]
    if not filtered:
        return []

    # 按开始时间排序
    filtered.sort(key=lambda s: s.get('start', 0.0))

    # 移除重叠：前一片段 end 裁剪到后一片段 start - 1
    cleaned: list[dict[str, Any]] = [dict(filtered[0])]
    for seg in filtered[1:]:
        if seg['start'] < cleaned[-1]['end']:
            cleaned[-1]['end'] = seg['start'] - 1.0
            if cleaned[-1]['end'] - cleaned[-1]['start'] < min_duration:
                cleaned.pop()
        cleaned.append(dict(seg))

    return cleaned


def _scene_ocr_detection(*args, **kwargs):
    """兼容包装：逻辑已迁至 lsc.analyzer.scene_analysis。"""
    from lsc.analyzer.scene_analysis import scene_ocr_detection
    return scene_ocr_detection(*args, **kwargs)


def _merge_scene_and_ocr(*args, **kwargs):
    """兼容包装：逻辑已迁至 lsc.analyzer.scene_analysis。"""
    from lsc.analyzer.scene_analysis import merge_scene_and_ocr
    return merge_scene_and_ocr(*args, **kwargs)


def _run_scene_analysis(*args, **kwargs):
    """兼容包装：逻辑已迁至 lsc.analyzer.scene_analysis。"""
    from lsc.analyzer.scene_analysis import run_scene_analysis
    return run_scene_analysis(*args, **kwargs)


def _analyze_scene_or_rounds(
    video_path: str,
    game: str,
    threshold: float,
    progress_callback: Callable[[str, float, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict[str, Any]] | None:
    """scene 模式的统一分析入口：Valorant 优先回合分割，其余走场景检测。

    对一次性分析（录制已结束的完整文件）：
    - game="valorant" 时经 AnalyzerRegistry → detect_valorant_rounds_ocr；
    - 无结果或非 valorant，回退到 generic scene 分析。

    Returns:
        高光段列表；被取消时返回 None（与 _run_scene_analysis 语义一致）。
    """
    from lsc.analyzer.registry import get as get_analyzer

    _cfg = load_config()
    _ffmpeg = _cfg.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
    options = {
        "threshold": threshold,
        "ffmpeg_path": _ffmpeg,
    }

    if game == "valorant":
        try:
            if progress_callback:
                progress_callback("round_detect", 0.0, "Valorant OCR 回合检测中...")
            plugin = get_analyzer("valorant")
            highlights = plugin.analyze_file(
                video_path,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                options=options,
            )
            if cancel_check and cancel_check():
                return None
            if highlights:
                _log.info(
                    "Valorant OCR 回合检测: %d 回合 (path=%s)",
                    len(highlights),
                    os.path.basename(video_path),
                )
                return highlights
            _log.info("Valorant OCR 检测无结果，回退到场景检测")
        except Exception as exc:
            _log.warning("Valorant OCR 检测失败，回退到场景检测: %s", exc)

    return get_analyzer("generic").analyze_file(
        video_path,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
        options={"threshold": threshold},
    )


# ── Duration 缓存：避免持续分析每轮都启动 ffprobe ──────────────
# 结构: {path: (duration_sec, file_size_bytes, monotonic_ts)}
_duration_cache: dict[str, tuple[float, int, float]] = {}
_duration_cache_lock = threading.Lock()
_DURATION_CACHE_TTL = 30.0  # 缓存有效期（秒）


def _get_video_duration(video_path: str) -> float:
    """Get video duration in seconds, with caching and size-based estimation.

    对正在写入的录制文件，首次 ffprobe 获取精确时长后，
    后续通过文件大小增量 / 码率估算新时长，避免每轮都 fork ffprobe。
    """
    now = time.monotonic()

    # 检查缓存
    try:
        cur_size = os.path.getsize(video_path)
    except OSError:
        return 0.0

    with _duration_cache_lock:
        cached = _duration_cache.get(video_path)
        if cached is not None:
            dur, cached_size, cached_at = cached
            age = now - cached_at
            if age < _DURATION_CACHE_TTL and dur > 0:
                # 文件未增长 → 直接返回缓存
                if cur_size <= cached_size:
                    return dur
                # 文件在增长 → 用码率估算新时长
                if cached_size > 0 and dur > 0:
                    bitrate = cached_size / dur  # bytes/sec
                    estimated = cur_size / bitrate
                    # 估算增长物理校验：写入中文件时长增量不可能超过实际经过时间
                    # 的 1.5 倍（码率波动容差）。防止码率估算自举漂移（每次估算基于
                    # 上次估算结果，漂移会逐步放大）导致时长虚高、seek 超界抽帧失败。
                    max_est = dur + max(15.0, age * 1.5)
                    if estimated <= max_est and estimated <= dur + _DURATION_CACHE_TTL * 2:
                        # 更新缓存中的 size 和估算时长
                        _duration_cache[video_path] = (estimated, cur_size, now)
                        return estimated

    # 缓存未命中或过期 → 执行 ffprobe
    _cfg2 = load_config()
    _ffprobe = _cfg2.ffprobe_path or shutil.which("ffprobe") or "ffprobe"

    try:
        result = run_hidden(
            [
                _ffprobe,
                "-v", "error",
                "-probesize", "5M",
                "-analyzeduration", "2M",
                "-show_entries", "format=duration",
                "-of", "json",
                video_path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        if duration > 0:
            with _duration_cache_lock:
                _duration_cache[video_path] = (duration, cur_size, now)
                # 清理过期条目（保留最近 50 条）
                if len(_duration_cache) > 50:
                    oldest = sorted(_duration_cache, key=lambda k: _duration_cache[k][2])[:25]
                    for k in oldest:
                        del _duration_cache[k]
        return duration
    except Exception as exc:
        _log.debug("获取视频时长失败 (%s): %s", video_path, exc)
        # 失败时尝试返回旧缓存
        if cached is not None and cached[0] > 0:
            return cached[0]
        return 0.0


def _expand_user_path(path: str) -> str:
    if path.startswith('~'):
        return os.path.expanduser(path)
    return path


def _is_allowed_output_dir(path: str) -> bool:
    """导出目录合法性：拒绝系统关键路径，允许用户数据目录（含非系统盘）。

    历史策略「仅允许 home」会误伤 D 盘等合法导出目录；且 save_settings 被
    所有设置操作共用（如画质切换），历史 output_dir 一旦被判非法会阻塞
    一切设置保存。改为黑名单：仅拒绝操作系统关键目录。
    """
    if not isinstance(path, str) or not path.strip():
        return False
    stripped = path.strip()
    # POSIX 系统路径须在 realpath 前判断：Windows 会把 /etc 解析到当前盘符下
    posix_like = stripped.replace("\\", "/")
    for root in ("/etc", "/boot", "/sys", "/proc", "/dev", "/sbin", "/bin", "/usr"):
        if posix_like == root or posix_like.startswith(root + "/"):
            return False
    try:
        real = os.path.normcase(os.path.realpath(os.path.expanduser(stripped)))
    except (OSError, ValueError):
        return False
    system_roots = [
        os.environ.get("SYSTEMROOT", r"C:\Windows"),
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
    ]
    for root in system_roots:
        root_norm = os.path.normcase(os.path.realpath(root))
        if real == root_norm or real.startswith(root_norm + os.sep):
            return False
    return True


def _parse_fps(framerate: str) -> float:
    """Parse framerate string to float. Returns 0.0 for 原画/auto."""
    if not framerate or framerate == '原画':
        return 0.0
    try:
        return float(framerate)
    except (ValueError, TypeError):
        return 0.0


def _safe_float(value, default: float = 0.0) -> float:
    """安全地将值转换为 float，转换失败时返回默认值。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_export_preset(preset_id: str) -> dict[str, Any] | None:
    """Get export preset configuration by ID.

    优先查找用户自定义预设（存于 settings.json 的 appSettings.custom_export_presets），
    未命中再回退到内置预设。
    """
    # 自定义预设优先
    try:
        custom_presets = load_settings().get('appSettings', {}).get('custom_export_presets', [])
        if isinstance(custom_presets, list):
            for cp in custom_presets:
                if isinstance(cp, dict) and cp.get('id') == preset_id:
                    return {
                        'codec': cp.get('codec', 'h264_nvenc'),
                        'crf': cp.get('crf', 23),
                        'resolution': cp.get('resolution', ''),
                        'framerate': cp.get('framerate', '原画'),
                        'audio_bitrate': cp.get('audio_bitrate', '128k'),
                        'vertical_crop': bool(cp.get('vertical_crop', False)),
                    }
    except Exception as exc:
        _log.warning("读取自定义导出预设失败，回退内置预设: %s", exc)

    presets = {
        'douyin_vertical': {
            'codec': 'h264_nvenc',
            'crf': 23,
            'resolution': '1080:1920',
            'framerate': '30',
            'audio_bitrate': '128k',
            'vertical_crop': True,
        },
        'bilibili_horizontal': {
            'codec': 'h264_nvenc',
            'crf': 23,
            'resolution': '1920:1080',
            'framerate': '30',
            'audio_bitrate': '128k',
            'vertical_crop': False,
        },
        'original': {
            'codec': 'copy',
            'crf': 0,
            'resolution': '',
            'framerate': '原画',
            'audio_bitrate': '128k',
            'vertical_crop': False,
        },
        'high_quality': {
            'codec': 'h264_nvenc',
            'crf': 18,
            'resolution': '',
            'framerate': '60',
            'audio_bitrate': '256k',
            'vertical_crop': False,
        },
        'small_file': {
            'codec': 'hevc_nvenc',
            'crf': 28,
            'resolution': '1280:720',
            'framerate': '24',
            'audio_bitrate': '96k',
            'vertical_crop': False,
        },
    }
    return presets.get(preset_id)


_settings_cache: dict[str, Any] | None = None
_settings_cache_mtime: float = 0.0
_settings_cache_ttl: float = 5.0
_settings_cache_time: float = 0.0


def load_settings():
    """从 settings.json 加载应用设置，失败时返回默认值。

    带文件修改时间缓存：5 秒内重复调用且文件未修改时直接返回缓存，
    减少批量导出等场景的冗余磁盘 IO。
    """
    global _settings_cache, _settings_cache_mtime, _settings_cache_time
    now = time.time()
    if _settings_cache is not None and (now - _settings_cache_time) < _settings_cache_ttl:
        return _settings_cache
    if os.path.exists(SETTINGS_FILE):
        try:
            mtime = os.path.getmtime(SETTINGS_FILE)
            if mtime == _settings_cache_mtime:
                _settings_cache_time = now
                return _settings_cache
            with open(SETTINGS_FILE, encoding='utf-8') as f:
                _settings_cache = json.load(f)
            _settings_cache_mtime = mtime
            _settings_cache_time = now
            return _settings_cache
        except Exception as exc:
            _log.warning("加载设置文件失败，使用默认值: %s", exc)
    _settings_cache = {
        'output_dir': os.path.join(os.path.expanduser('~'), 'LSC', 'output'),
        'theme': 'dark',
        'encoder': 'h264_nvenc',
        'quality': '原画',
        'param_mode': 'CRF 质量',
        'crf': 23,
        'bitrate': 8000,
        'bitrate_unit': 'kbps',
        'resolution': '原画',
        'framerate': '原画',
        'audio_bitrate': '128k',
        'preview_quality': '高清',
        'default_export_preset': 'douyin_vertical',
        'export_max_concurrent': 2,
        'ocr_accel': 'dml',
        'jianying_draft_dir': '',  # 空 = 自动探测 %LOCALAPPDATA%\JianyingPro\...
    }
    _settings_cache_time = now
    return _settings_cache


# 预览画质预设：分辨率 + NVENC 码率 + libx264 CRF/码率
_PREVIEW_QUALITY_PRESETS: dict[str, dict[str, Any]] = {
    '原画': {'width': 0, 'height': 0, 'nvenc_bitrate': '8000k', 'x264_crf': 20, 'x264_bitrate': '6000k'},
    '高清': {'width': 1280, 'height': 720, 'nvenc_bitrate': '2500k', 'x264_crf': 26, 'x264_bitrate': '1800k'},
    '标清': {'width': 854, 'height': 480, 'nvenc_bitrate': '1500k', 'x264_crf': 30, 'x264_bitrate': '1000k'},
    '流畅': {'width': 640, 'height': 360, 'nvenc_bitrate': '800k', 'x264_crf': 32, 'x264_bitrate': '600k'},
}


def _get_preview_quality_preset(quality: str) -> dict[str, Any]:
    """返回预览画质预设参数，未知值回退到 '高清'。"""
    return _PREVIEW_QUALITY_PRESETS.get(quality, _PREVIEW_QUALITY_PRESETS['高清'])


def _apply_shared_ingest_from_settings(settings: dict) -> None:
    """将 settings.json 中的共享进样开关同步到运行时 LscConfig 单例。

    设置页写入 settings.json；预览/录制读 load_config()。二者必须同步，
    否则 UI 开关对运行时无效。
    """
    if 'shared_ingest_enabled' not in settings:
        return
    try:
        enabled = bool(settings.get('shared_ingest_enabled'))
        cfg = load_config()
        if bool(getattr(cfg, 'shared_ingest_enabled', False)) != enabled:
            cfg.shared_ingest_enabled = enabled
            _log.info("运行时 shared_ingest_enabled 已同步为 %s", enabled)
    except Exception as exc:
        _log.warning("同步 shared_ingest_enabled 到 LscConfig 失败: %s", exc)


def _normalize_settings_ocr_accel(settings: dict) -> dict:
    from lsc.analyzer.ocr_accel import normalize_ocr_accel

    out = dict(settings)
    out['ocr_accel'] = normalize_ocr_accel(out.get('ocr_accel', 'dml'))
    return out


def _normalize_jianying_draft_dir(settings: dict) -> dict:
    from lsc.exporter.jianying_draft import validate_draft_dir

    out = dict(settings)
    raw = out.get('jianying_draft_dir', '')
    if raw is None:
        raw = ''
    if not isinstance(raw, str):
        raise ValueError("jianying_draft_dir 必须是字符串")
    stripped = raw.strip()
    if not stripped:
        out['jianying_draft_dir'] = ''
        return out
    if not os.path.isdir(stripped) and not validate_draft_dir(stripped):
        raise ValueError("剪映草稿目录无效或不可写")
    out['jianying_draft_dir'] = stripped
    return out


def save_settings(settings: dict):
    output_dir = settings.get('output_dir')
    if isinstance(output_dir, str) and not _is_allowed_output_dir(output_dir):
        raise ValueError("导出目录不在允许范围内")
    try:
        from lsc.analyzer.ocr_accel import normalize_ocr_accel

        previous = load_settings()
        prev_ocr_accel = normalize_ocr_accel(previous.get('ocr_accel', 'dml'))
        settings = _normalize_settings_ocr_accel(settings)
        settings = _normalize_jianying_draft_dir(settings)
        new_ocr_accel = settings.get('ocr_accel', 'dml')
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        _atomic_write_json(SETTINGS_FILE, settings)
        global _settings_cache, _settings_cache_mtime, _settings_cache_time
        _settings_cache = settings
        _settings_cache_mtime = os.path.getmtime(SETTINGS_FILE) if os.path.exists(SETTINGS_FILE) else 0.0
        _settings_cache_time = time.time()
        _apply_shared_ingest_from_settings(settings)
        if new_ocr_accel != prev_ocr_accel:
            from lsc.analyzer.ocr_detector import invalidate_ocr

            invalidate_ocr()
    except OSError as exc:
        _log.error("保存设置失败: %s", exc)
        raise


def get_storage_info():
    """获取输出目录存储信息（总大小、磁盘总量、切片数量）。"""
    settings = load_settings()
    output_dir = _expand_user_path(settings.get('output_dir', os.path.join(os.path.expanduser('~'), 'LSC', 'output')))
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    total_size = 0
    clip_count = 0
    for dirpath, _dirnames, filenames in os.walk(output_dir):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.exists(fp):
                total_size += os.path.getsize(fp)
                if f.endswith(('.mp4', '.mkv', '.flv', '.mov', '.ts')):
                    clip_count += 1

    try:
        disk_usage = shutil.disk_usage(output_dir)
        total_disk = disk_usage.total / (1024 ** 3)
    except Exception:
        total_disk = 50

    return {
        'used': total_size / (1024 ** 3),
        'total': total_disk,
        'clip_count': clip_count,
    }


def get_disk_usage_info():
    """获取输出目录磁盘使用情况（总容量、已用、可用）。"""
    settings = load_settings()
    output_dir = _expand_user_path(settings.get('output_dir', os.path.join(os.path.expanduser('~'), 'LSC', 'output')))
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    try:
        total, used, free = shutil.disk_usage(output_dir)
    except Exception:
        return {'total': 0, 'used': 0, 'free': 0}

    return {'total': total, 'used': used, 'free': free}


_mse_push_paused: set[str] = set()
_mse_push_paused_lock = threading.Lock()


def _push_mse_segment(ws_server: Any, loop: asyncio.AbstractEventLoop, kind: str, room_id: str, seg: bytes) -> None:
    """从 FFmpeg 回调线程调度 MSE 二进制广播（无 base64）。

    前端 mse_backpressure=pause 时丢弃 media 段，仍读 FFmpeg 管道避免死锁。
    init 始终推送。
    """
    normalized_kind = {
        "mse_init": "init",
        "mse_segment": "segment",
        "media": "segment",
    }.get(kind, kind)
    if normalized_kind == "segment" and room_id in _mse_push_paused:
        return
    asyncio.run_coroutine_threadsafe(
        ws_server.broadcast_mse(normalized_kind, room_id, seg),
        loop,
    )
    if normalized_kind == "init" and room_id not in _mse_live_phase:
        # phase 'streaming' 延迟到首个 init 段产出；先入队 init，保持旧事件顺序。
        _mse_live_phase.add(room_id)
        asyncio.run_coroutine_threadsafe(
            ws_server.broadcast('preview_phase', {'room_id': room_id, 'phase': 'streaming'}),
            loop,
        )


def _room_to_dict(room: Any, *, redact_sensitive: bool = True) -> dict[str, Any]:
    """将 Room 对象序列化为前端可消费的字典。"""
    from lsc.core.services.ingest_registry import get_shared_ingest_registry
    from lsc.core.services.runtime_health import build_room_health

    stream_url = ''
    if room.stream_info and room.stream_info.stream_url:
        stream_url = room.stream_info.stream_url

    started_at = None
    if room.record_started_at is not None:
        if isinstance(room.record_started_at, datetime):
            started_at = room.record_started_at.isoformat()
        else:
            started_at = datetime.fromtimestamp(float(room.record_started_at)).isoformat()

    room_id = room.room_id
    supervisor = get_shared_ingest_registry().get_supervisor_if_exists(room_id)
    output_room_url = redact_url(room.room_url) if redact_sensitive else room.room_url
    # A signed media URL is ephemeral and is never needed to restore a room;
    # keep it redacted even for the internal persistence projection.
    output_stream_url = redact_url(stream_url)
    return {
        'room_id': room_id,
        # Room snapshots are sent through WebSocket/HTTP to the UI.  Keep the
        # internal signed URL in the runtime only; user-visible payloads get
        # the same redaction policy as diagnostics.
        'room_url': output_room_url,
        'platform': room.platform,
        'canonical_room_id': getattr(room, 'canonical_room_id', '') or '',
        'platform_name': room.platform_name,
        'streamer_name': redact_text(room.streamer_name),
        'stream_title': redact_text(room.stream_title),
        'stream_url': output_stream_url,
        'is_connecting': room.is_connecting,
        'is_connected': room.is_connected,
        'is_recording': room.is_recording,
        'is_recording_starting': room_id in _recording_starting,
        'is_recording_queued': room_id in _recording_wait_queue,
        'recording_queue_position': (
            _recording_wait_queue.index(room_id) + 1 if room_id in _recording_wait_queue else 0
        ),
        'is_reconnecting': getattr(room, 'is_reconnecting', False),
        'record_output_path': room.record_output_path or "",
        'record_manifest_path': getattr(room, 'record_manifest_path', '') or '',
        'record_started_at': started_at,
        'record_size_mb': room.record_size_mb,
        'last_error': redact_text(room.last_error),
        'preview_enabled': room.preview_enabled,
        'preview_paused': room.preview_paused,
        'preview_muted': room.preview_muted,
        'preview_mode': getattr(room, 'preview_mode', None) or 'live_mse',
        'preview_quality': getattr(room, 'preview_quality', '') or '',
        'mark_in': room.mark_in,
        'mark_out': room.mark_out,
        'mark_in_wallclock': room.mark_in_wallclock,
        'mark_out_wallclock': room.mark_out_wallclock,
        'recording_start_mono': room.recording_start_mono,
        'recording_media_start_mono': getattr(room, 'recording_media_start_mono', None),
        'preview_latency': room.preview_latency,
        'content_offset': getattr(room, 'content_offset', 0.0),
        'align_group_id': getattr(room, 'align_group_id', '') or '',
        'category': getattr(room, 'category', '') or '',
        'preview_epoch_id': getattr(room, 'preview_epoch_id', '') or '',
        'recording_id': getattr(room, 'recording_id', '') or '',
        'pipeline_health': build_room_health(room, supervisor=supervisor),
    }


# _timeline_to_dict 已迁移至 handlers.timeline_handlers.timeline_to_dict


def _rooms_list(manager: RoomOrchestrator, *, redact_sensitive: bool = True):
    """将 manager 中的所有房间序列化为字典列表。"""
    return [
        _room_to_dict(r, redact_sensitive=redact_sensitive)
        for r in manager.list_rooms()
    ]


def _persist_current_rooms(manager: RoomOrchestrator) -> bool:
    """将当前 manager 中的房间列表持久化到 JSON（1s 写合并）。"""
    from persistence import schedule_save_rooms
    # Persistence is an internal store and must retain the original room URL
    # so restart/reconnect can work.  Public HTTP/WebSocket snapshots use the
    # redacted default above.
    schedule_save_rooms(_rooms_list(manager, redact_sensitive=False))
    return True


def restore_persisted_rooms(manager: RoomOrchestrator) -> int:
    """后端启动时恢复房间配置，但不恢复连接、预览或录制等瞬时状态。

    只在后端生命周期内执行一次；不能放在 WebSocket ``on_connect`` 中，
    否则前端重连会重复创建房间。兼容 handler 的 ``room_url`` 完整快照和
    orchestrator 旧格式的 ``url`` 字段。
    """
    from persistence import load_rooms

    saved_rooms = load_rooms()
    if not saved_rooms:
        return 0

    def _restore_on_orchestrator_thread() -> int:
        existing_urls = {
            str(getattr(room, "room_url", "") or "").strip().rstrip("/").lower()
            for room in manager.list_rooms()
        }
        restored = 0
        for item in saved_rooms:
            if not isinstance(item, dict):
                continue
            url = str(item.get("room_url") or item.get("url") or "").strip()
            normalized_url = url.rstrip("/").lower()
            if not url or normalized_url in existing_urls:
                continue
            room = manager.add_room(url)
            if room is None:
                continue
            existing_urls.add(normalized_url)
            restored += 1

            for field in ("mark_in", "mark_out", "content_offset"):
                value = item.get(field)
                if value is None:
                    continue
                try:
                    setattr(room, field, float(value))
                except (TypeError, ValueError):
                    pass
            for field in ("align_group_id", "category"):
                value = item.get(field)
                if isinstance(value, str):
                    setattr(room, field, value)
            if "preview_muted" in item:
                room.preview_muted = bool(item["preview_muted"])
            if "include_in_cut" in item:
                room.include_in_cut = bool(item["include_in_cut"])

        return restored

    call = getattr(manager, "call", None)
    restored = (
        int(call(_restore_on_orchestrator_thread))
        if callable(call)
        else _restore_on_orchestrator_thread()
    )
    _log.info("后端启动恢复房间完成: restored=%d, saved=%d", restored, len(saved_rooms))
    return restored


def _get_current_pos(room: Any) -> float:
    """获取当前播放/录制位置（秒）。"""
    if room.controller is not None:
        pos = getattr(room.controller, 'current_sec', 0)
        # Electron 模式下 current_sec 可能恒为 0，回退到录制时长
        if pos is not None and pos > 0:
            return float(pos)
    if room.is_recording and room.record_started_at is not None:
        if isinstance(room.record_started_at, datetime):
            return (datetime.now() - room.record_started_at).total_seconds()
        return 0.0
    return 0.0


class _RoomsThrottle:
    """rooms_updated 广播节流：首次立即发送，300ms 内合并后续更新。"""
    _MERGE_WINDOW_SEC = 0.3

    def __init__(self) -> None:
        self._last_send_time = 0.0
        self._pending = False

    def should_send_immediate(self) -> bool:
        """首次立即发送；后续在合并窗口外也立即发送。"""
        now = time.monotonic()
        if self._last_send_time == 0.0:
            self._last_send_time = now
            return True
        if now - self._last_send_time >= self._MERGE_WINDOW_SEC:
            self._last_send_time = now
            return True
        self._pending = True
        return False

    def mark_pending(self) -> None:
        """标记有未发送的更新。"""
        self._pending = True

    @property
    def has_pending(self) -> bool:
        return self._pending


def _resolve_export_range(
    start_sec,
    end_sec,
    *,
    source='',
    content_offset=0.0,
    snap_in=None,
    snap_out=None,
    snap_rec=None,
    use_room_marks=False,
    room_mark_in=None,
    room_mark_out=None,
    room_rec_start=None,
):
    """解析导出入/出点与精度。

    优先级：
    1. source == 'ai_highlight' → 直接用 start/end（忽略快照）
    2. 完整墙钟快照 (snap_in/out/rec) → exact
    3. use_room_marks + 房间当前墙钟 → exact
    4. 否则 start/end - content_offset → approximate
    """
    content_offset = float(content_offset or 0.0)
    start_sec = float(start_sec)
    end_sec = float(end_sec)

    if source == 'ai_highlight':
        return max(0.0, start_sec), max(0.0, end_sec), 'exact'

    if snap_in is not None and snap_out is not None and snap_rec is not None:
        return (
            max(0.0, float(snap_in) - float(snap_rec) - content_offset),
            max(0.0, float(snap_out) - float(snap_rec) - content_offset),
            'exact',
        )

    if use_room_marks:
        if (
            room_mark_in is not None
            and room_mark_out is not None
            and room_rec_start is not None
        ):
            return (
                max(0.0, float(room_mark_in) - float(room_rec_start) - content_offset),
                max(0.0, float(room_mark_out) - float(room_rec_start) - content_offset),
                'exact',
            )
        return (
            max(0.0, start_sec - content_offset - _PREVIEW_LATENCY_FALLBACK),
            max(0.0, end_sec - content_offset - _PREVIEW_LATENCY_FALLBACK),
            'approximate',
        )

    if snap_in is not None or snap_out is not None or snap_rec is not None:
        return (
            max(0.0, start_sec - content_offset),
            max(0.0, end_sec - content_offset),
            'approximate',
        )

    return (
        max(0.0, start_sec - content_offset - _PREVIEW_LATENCY_FALLBACK),
        max(0.0, end_sec - content_offset - _PREVIEW_LATENCY_FALLBACK),
        'approximate',
    )


# §8.2 降级补偿：无墙钟快照时用固定 preview_latency 近似预览流相对录制流的延迟
_PREVIEW_LATENCY_FALLBACK = 2.0



def _purge_stale_analysis_jobs() -> None:
    """#99: periodic TTL-based purge of completed analysis jobs."""
    now = time.time()
    with _analysis_jobs_lock:
        stale = [rid for rid, job in list(_analysis_jobs.items())
                 if job.get('completed_at') and now - job['completed_at'] > _ANALYSIS_JOB_TTL]
        for rid in stale:
            _analysis_jobs.pop(rid, None)
    if stale:
        _log.debug("purged %d stale analysis jobs", len(stale))


async def _ensure_export_queue():
    """Module-level backward-compat entry (server.py imports this).

    The queue implementation owns the hot-reload guard: it only replaces the
    semaphore while ``_export_queue.empty()`` and ``_export_in_flight == 0``.
    Keep that contract visible at this compatibility boundary so callers and
    static regression checks do not accidentally reintroduce an in-flight
    export race when the implementation remains delegated.
    """
    from handlers.export_handlers import ensure_export_queue
    await ensure_export_queue()


# Compatibility note for integrations that still inspect the historical
# handler location: ``async def handle_export_clip`` now lives in
# ``handlers.export_handlers``.  Its payload continues to carry
# ``content_offset`` through the delegated ``queue_export`` signature.


def register_room_handlers(server, bridge):
    manager: RoomOrchestrator = bridge.manager

    # 启动时把 settings.json 的共享进样开关灌入运行时配置
    try:
        _apply_shared_ingest_from_settings(load_settings())
    except Exception as exc:
        _log.debug("启动同步 shared_ingest 失败: %s", exc)

    # rooms_updated 广播节流：首次立即发送，300ms 内合并后续更新
    _rooms_throttle = _RoomsThrottle()
    _rooms_throttle_task: asyncio.Task | None = None

    def _broadcast_rooms(*, force: bool = False):
        """广播 rooms_updated。统一走 bridge.queue_broadcast，由 drain_merge_broadcasts 做 last-value coalesce。"""
        nonlocal _rooms_throttle_task
        if force:
            _rooms_throttle._pending = False
            _rooms_throttle._last_send_time = time.monotonic()
            # 取消待发的 _flush task，防止 force 发送后 _flush 再发一遍（#103）
            if _rooms_throttle_task is not None and not _rooms_throttle_task.done():
                _rooms_throttle_task.cancel()
                _rooms_throttle_task = None
            bridge.queue_broadcast({
                'type': 'rooms_updated',
                'data': {'rooms': _rooms_list(manager)},
            })
            return
        if _rooms_throttle.should_send_immediate():
            # 取消待发的 _flush task，防止立即发送后 _flush 再发一遍（#103）
            if _rooms_throttle_task is not None and not _rooms_throttle_task.done():
                _rooms_throttle_task.cancel()
                _rooms_throttle_task = None
            bridge.queue_broadcast({
                'type': 'rooms_updated',
                'data': {'rooms': _rooms_list(manager)},
            })
            return
        if _rooms_throttle_task is not None and not _rooms_throttle_task.done():
            return
        async def _flush():
            try:
                await asyncio.sleep(_RoomsThrottle._MERGE_WINDOW_SEC)
            except asyncio.CancelledError:
                return
            if _rooms_throttle.has_pending:
                _rooms_throttle._pending = False
                _rooms_throttle._last_send_time = time.monotonic()
                bridge.queue_broadcast({
                    'type': 'rooms_updated',
                    'data': {'rooms': _rooms_list(manager)},
                })
            _rooms_throttle_task = None
        _rooms_throttle_task = asyncio.create_task(_flush())

    async def _shared_mse_on_error(room_id: str, err: str, loop) -> None:
        """共享进样预览出错后自动重连：刷新 CDN URL 并重启预览。

        旧逻辑只广播 mse_error 并清理，前端会立刻把 preview_enabled 置 false，
        导致无法恢复。此处对齐 legacy MseStreamer 的 _on_mse_error 重连策略。
        """
        _log.info("Shared MSE error for room %s: %s", room_id, err)

        # 启动期忽略：_handle_mse_preview 启动（含 MseStreamer 硬解→软解回退
        # 重试）期间 FFmpeg 立即退出会触发 on_error；此时重连循环若介入，
        # 会与软解重试并行创建第二个 FFmpeg 进程互相覆盖。启动成败由
        # _handle_mse_preview 统一处理（_mse_starting finally 中 discard）。
        if room_id in _mse_starting:
            _log.debug(
                "Shared MSE error during startup, ignored (room=%s): %s",
                room_id, err,
            )
            return

        def _lease_rotation_decision():
            room = manager.get_room(room_id)
            if room is None:
                return False
            ingest = get_shared_ingest_registry().get(room_id)
            saw_first_ts = bool(getattr(ingest, "_upstream_has_produced_data", False))
            rotating = bool(
                callable(getattr(ingest, "is_lease_rotating", None))
                and ingest.is_lease_rotating()
            )
            return _is_lease_rotation_error(
                getattr(room, "stream_info", None),
                err,
                saw_first_ts=saw_first_ts or rotating,
            )

        try:
            rotating = await loop.run_in_executor(
                _bridge_executor, lambda: bridge.manager.call(_lease_rotation_decision)
            )
        except Exception:
            rotating = False
        if rotating:
            _log.info(
                "Shared MSE lease rotation for room %s; keep preview sink: %s",
                room_id,
                err,
            )

            def _rotate_lease():
                room = manager.get_room(room_id)
                if room is None:
                    return False
                start = getattr(manager, "_start_supervised_recovery", None)
                if callable(start):
                    return bool(start(room, err))
                recover = getattr(manager, "_recover_shared_upstream_in_place", None)
                return bool(callable(recover) and recover(room))

            try:
                await loop.run_in_executor(
                    _bridge_executor, lambda: bridge.manager.call(_rotate_lease)
                )
            except Exception as exc:
                _log.debug("Shared MSE lease rotation coordinator failed: %s", exc)
            return

        # 防重入：与 legacy 版一致，退避等待期间重复 on_error 由在途循环接管
        prev_state = _mse_reconnect_state.get(room_id)
        if prev_state and prev_state.get('running'):
            _log.info(
                "Shared MSE reconnect loop already running for room %s, "
                "ignoring duplicate error: %s", room_id, err,
            )
            return
        _mse_reconnect_state[room_id] = _begin_mse_reconnect(prev_state)

        old_handle = _preview_stream_registry().pop(room_id)
        if old_handle is not None:
            try:
                old_handle.stop()
            except Exception as exc:
                _log.debug("停止旧 shared preview handle 失败: %s", exc)
        _stop_idle_shared_ingest(room_id, reason="shared mse error cleanup")

        current_error = err
        mse_failure_reason = 'network'

        async def _finalize(error_text: str, reason: str, timeline_reason: str) -> None:
            _mse_reconnect_state.pop(room_id, None)
            _mse_live_phase.discard(room_id)
            await server.broadcast('mse_error', {
                'room_id': room_id,
                'error': error_text,
                'reason': reason,
            })

            def _clear_preview():
                room = manager.get_room(room_id)
                if room is not None:
                    room.preview_enabled = False
                    room.preview_mode = 'live_mse'
                _invalidate_room_timeline(room_id, reason=timeline_reason)

            try:
                await loop.run_in_executor(
                    _bridge_executor, lambda: bridge.manager.call(_clear_preview)
                )
            except Exception as exc:
                _log.error("Shared MSE error cleanup failed: %s", exc)
            bridge.queue_broadcast({
                'type': 'rooms_updated',
                'data': {'rooms': _rooms_list(manager)},
            })

        def _peek_reconnect_platform():
            room = manager.get_room(room_id)
            if room is None:
                return ""
            return str(
                getattr(room, "platform", "")
                or getattr(getattr(room, "stream_info", None), "platform", "")
                or ""
            )

        try:
            reconnect_platform = await loop.run_in_executor(
                _bridge_executor, lambda: bridge.manager.call(_peek_reconnect_platform)
            )
        except Exception:
            reconnect_platform = ""
        if not _preview_auto_reconnect_allowed(reconnect_platform):
            _mse_reconnect_state.pop(room_id, None)
            await _finalize(
                current_error or "预览失败",
                mse_failure_reason,
                f"mse_no_auto_reconnect:{room_id}",
            )
            return

        while True:
            def _check_preview():
                room = manager.get_room(room_id)
                if room is None:
                    return False
                return bool(room.preview_enabled)

            try:
                still_previewing = await loop.run_in_executor(
                    _bridge_executor, lambda: bridge.manager.call(_check_preview)
                )
            except Exception:
                still_previewing = False

            if not still_previewing:
                _mse_reconnect_state.pop(room_id, None)
                return

            state = _mse_reconnect_state.get(room_id, {'attempts': 0})
            if state['attempts'] >= _MSE_MAX_RECONNECT:
                _log.warning(
                    "Shared MSE reconnect exhausted for room %s (%d attempts)",
                    room_id, state['attempts'],
                )
                _mse_reconnect_state.pop(room_id, None)  # 清理状态，避免残留影响后续手动重开
                exhausted_msg = (
                    _mse_offline_error_message(current_error)
                    if mse_failure_reason == 'offline'
                    else '预览重连失败，已达到最大重试次数，请手动重新开启预览'
                )
                await _finalize(
                    exhausted_msg,
                    mse_failure_reason,
                    f"mse_reconnect_exhausted:{room_id}",
                )
                return

            delay = min(
                _MSE_RECONNECT_BASE_DELAY * (2 ** state['attempts']),
                _MSE_RECONNECT_MAX_DELAY,
            )
            state['attempts'] += 1
            _mse_reconnect_state[room_id] = state
            _log.info(
                "Shared MSE reconnect attempt %d/%d for room %s (delay=%.1fs, error=%s)",
                state['attempts'], _MSE_MAX_RECONNECT, room_id, delay, current_error,
            )
            await server.broadcast('mse_reconnecting', {
                'room_id': room_id,
                'attempt': state['attempts'],
                'max_attempts': _MSE_MAX_RECONNECT,
                'delay': delay,
            })
            await asyncio.sleep(delay)

            try:
                still_previewing = await loop.run_in_executor(
                    _bridge_executor, lambda: bridge.manager.call(_check_preview)
                )
            except Exception:
                still_previewing = False
            if not still_previewing:
                _mse_reconnect_state.pop(room_id, None)
                return

            # 403/签名错误才强制刷新；预览编码器/stdout 卡住只重启 sink。
            if _should_refresh_failed_stream(current_error):
                try:
                    def _mark_failed_candidate():
                        room = manager.get_room(room_id)
                        if room is not None:
                            mark_failed_candidate(room.stream_info, current_error)
                    await loop.run_in_executor(
                        _bridge_executor,
                        lambda: bridge.manager.call(_mark_failed_candidate),
                    )
                except Exception as exc:
                    _log.debug("Shared MSE reconnect candidate policy failed: %s", exc)
                try:
                    refresh_ok = await loop.run_in_executor(
                        _recording_executor,
                        lambda: manager.refresh_stream_url(room_id, force=True),
                    )
                except Exception as exc:
                    _log.error("Shared MSE reconnect URL refresh failed: %s", exc)
                    refresh_ok = False
            else:
                refresh_ok = True

            if not refresh_ok:
                try:
                    offline, offline_msg = await loop.run_in_executor(
                        _recording_executor,
                        lambda: _probe_stream_offline(manager, room_id),
                    )
                except Exception as exc:
                    _log.debug("Shared MSE reconnect offline probe failed: %s", exc)
                    offline, offline_msg = False, ''
                if offline:
                    mse_failure_reason = 'offline'
                    await _finalize(
                        offline_msg or _mse_offline_error_message(),
                        'offline',
                        f"mse_offline:{room_id}",
                    )
                    return
                current_error = '流地址刷新失败'
                continue

            try:
                result = await _handle_mse_preview(
                    server, manager, room_id, True, None, force_restart=True,
                )
            except Exception as exc:
                _log.warning("Shared MSE reconnect start failed for %s: %s", room_id, exc)
                result = {'success': False, 'error': redact_text(exc)}

            if not isinstance(result, dict):
                result = {'success': False, 'error': '预览重连失败'}
            ingest = None
            try:
                ingest = _shared_ingests.get(room_id)
            except Exception as exc:
                _log.debug("Shared MSE reconnect ingest lookup failed: %s", exc)
            if _shared_preview_reconnect_ready(result, ingest):
                _mse_live_phase.discard(room_id)
                _log.info("Shared MSE reconnect succeeded for room %s", room_id)
                ready_state = _note_mse_reconnect_event(room_id, "media_ready")

                def _rotate_shared_epoch_on_reconnect():
                    room = manager.get_room(room_id)
                    if room is None:
                        return False
                    new_epoch = uuid4().hex
                    room.preview_epoch_id = new_epoch
                    get_timeline_service().on_preview_epoch_change(room_id, new_epoch)
                    return True

                try:
                    await loop.run_in_executor(
                        _bridge_executor, lambda: bridge.manager.call(_rotate_shared_epoch_on_reconnect)
                    )
                except Exception as exc:
                    _log.debug("Shared MSE reconnect epoch rotate failed: %s", exc)

                asyncio.create_task(
                    _watch_mse_reconnect_durable(
                        room_id,
                        ready_at=float(ready_state.get("media_ready_at") or time.monotonic()),
                        broadcast=lambda: server.broadcast('mse_reconnected', {'room_id': room_id}),
                    )
                )
                _broadcast_rooms()
                return

            current_error = result.get('error') or '预览重连未拉起上游'
            _log.warning(
                "Shared MSE reconnect failed for room %s: %s",
                room_id, current_error,
            )

    def _attach_shared_preview_handle(room_id: str, shared_ingest, loop):
        def on_init(seg):
            return _push_mse_segment(server, loop, 'mse_init', room_id, seg)

        def on_media(seg):
            return _push_mse_segment(server, loop, 'mse_segment', room_id, seg)

        def on_error(err):
            return asyncio.run_coroutine_threadsafe(
                _shared_mse_on_error(room_id, err, loop), loop
            )
        try:
            room = manager.get_room(room_id)
            platform = (
                (getattr(room, "platform", "") or getattr(room, "platform_name", ""))
                if room is not None else ""
            )
            if is_platform_pipeline_component_enabled("ingest_supervisor_v2", platform):
                supervisor = _shared_ingests.get_supervisor(
                    room_id,
                    url=getattr(shared_ingest, "url", ""),
                    headers=getattr(shared_ingest, "headers", {}),
                    network_context=dict(getattr(room, "network_context", {}) or {})
                    if room is not None else {},
                )
                _bind_shared_ingest_lease(manager, room_id, shared_ingest)
                # Preview refreshes can happen while recording keeps the
                # shared upstream alive.  In that case the registry must not
                # silently overwrite the URL; explicitly switch the lease
                # generation so recording and preview consume the same new
                # candidate.
                current_info = getattr(room, "stream_info", None) if room is not None else None
                current_url = str(getattr(current_info, "stream_url", "") or "")
                current_headers = dict(getattr(current_info, "headers", {}) or {})
                current_context = dict(getattr(room, "network_context", {}) or {}) if room is not None else {}
                ingest_url = str(getattr(getattr(supervisor, "ingest", None), "url", "") or "")
                ingest_headers = dict(getattr(getattr(supervisor, "ingest", None), "headers", {}) or {})
                ingest_context = dict(getattr(getattr(supervisor, "ingest", None), "network_context", {}) or {})
                if current_url and (
                    current_url != ingest_url
                    or current_headers != ingest_headers
                    or current_context != ingest_context
                ):
                    switch_upstream = getattr(supervisor, "switch_upstream", None)
                    if callable(switch_upstream):
                        lease = getattr(manager, "_stream_leases", {}).get(room_id)
                        if not switch_upstream(
                            current_url,
                            headers=current_headers,
                            network_context=current_context,
                            generation=getattr(lease, "generation", None),
                            reason_code="PREVIEW_LEASE_REFRESH",
                        ):
                            raise RuntimeError(
                                str((supervisor.health() or {}).get(
                                    "last_error",
                                    "preview upstream switch failed",
                                ) or "preview upstream switch failed")
                            )
                handle = supervisor.attach_preview(
                    on_init_segment=on_init,
                    on_media_segment=on_media,
                    on_error=on_error,
                )
                _preview_stream_registry().set_legacy(room_id, handle)
                return handle
        except RuntimeError:
            raise
        except Exception as exc:
            _log.debug("V2 preview supervisor attach fallback: room=%s error=%s", room_id, exc)
        return _preview_stream_registry().attach_shared(
            room_id,
            shared_ingest,
            on_init_segment=on_init,
            on_media_segment=on_media,
            on_error=on_error,
        )

    async def _reattach_shared_preview_after_recording_start(room_id: str, preview_enabled: bool) -> bool:
        shared_enabled = _shared_ingest_v2_enabled(manager, room_id)
        if not shared_enabled or not preview_enabled:
            return False

        shared_ingest = _shared_ingests.get(room_id)
        if (
            shared_ingest is None
            or not getattr(shared_ingest, 'recording_active', False)
            or getattr(shared_ingest, 'is_stopped', True)
        ):
            return False

        existing = _preview_stream_registry().get(room_id)
        if (
            existing is not None
            and getattr(existing, '_ingest', None) is shared_ingest
            and getattr(shared_ingest, 'last_init_segment', None)
        ):
            try:
                existing.replay_init()
            except Exception as exc:
                _log.debug("shared preview init replay failed during recording start: %s", exc)
            return True

        loop = asyncio.get_running_loop()
        shared_handle = None
        try:
            _configure_shared_preview_quality(shared_ingest)
            shared_handle = _attach_shared_preview_handle(room_id, shared_ingest, loop)
            shared_handle.replay_init()
        except Exception as exc:
            _log.warning(
                "shared preview reattach after recording start failed: room_id=%s, error=%s",
                room_id,
                exc,
            )
            if shared_handle is not None:
                try:
                    shared_handle.stop()
                except Exception as stop_exc:
                    _log.debug("shared preview cleanup failed after reattach error: %s", stop_exc)
            return False

        if existing is not None and existing is not shared_handle:
            def _stop_existing_preview():
                try:
                    existing.stop()
                except Exception as exc:
                    _log.debug("legacy preview stop failed after shared reattach: %s", exc)
                return True

            await asyncio.get_running_loop().run_in_executor(_bridge_executor, _stop_existing_preview)
            _stop_idle_shared_ingest(room_id, reason="preview reattached to shared recording")

        def _set_preview_on():
            current = manager.get_room(room_id)
            if current is not None:
                current.preview_enabled = True
            return True

        try:
            await asyncio.get_running_loop().run_in_executor(
                _bridge_executor, lambda: bridge.manager.call(_set_preview_on)
            )
        except Exception as exc:
            _log.debug("preview state sync failed after shared reattach: %s", exc)
        return True

    # 不要在注册期调用 asyncio.get_event_loop()：Python 3.12+ 在无运行中
    # 事件循环时会抛 RuntimeError，导致 handler 注册整体失败。改为惰性解析，
    # 并在 on_connect / 首个异步入口捕获真正的 WS 循环。
    _ws_loop_holder: dict[str, asyncio.AbstractEventLoop | None] = {'loop': None}

    def _queue_rooms_update(*_args, **_kwargs):
        """编排线程 tick 回调：走 _broadcast_rooms 节流/coalesce，避免高频 tick 绕过 300ms 合并。"""
        loop = _ws_loop_holder.get('loop')
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(_broadcast_rooms)
                return
            except Exception as exc:
                _log.debug("schedule _broadcast_rooms failed: %s", exc)
        # WS loop 尚未就绪时降级直发（仍由 drain_merge 做 last-value coalesce）
        bridge.queue_broadcast({
            'type': 'rooms_updated',
            'data': {'rooms': _rooms_list(manager)},
        })

    def _queue_recording_size_patches(*_args, **_kwargs):
        """中频 tick：仅对录制中房间推送增量 room_updated，避免全量 rooms_updated。"""
        from lsc.core.services.ingest_registry import get_shared_ingest_registry
        from lsc.core.services.runtime_health import build_room_health

        ingest_registry = get_shared_ingest_registry()
        patches: list[dict[str, Any]] = []
        for room in manager.list_rooms():
            if not getattr(room, 'is_recording', False) and not getattr(room, 'is_reconnecting', False):
                continue
            room_id = getattr(room, 'room_id', '') or ''
            if not room_id:
                continue
            patches.append({
                'room_id': room_id,
                'record_size_mb': getattr(room, 'record_size_mb', 0) or 0,
                'record_output_path': getattr(room, 'record_output_path', '') or '',
                'record_manifest_path': getattr(room, 'record_manifest_path', '') or '',
                'is_recording': bool(getattr(room, 'is_recording', False)),
                'is_reconnecting': bool(getattr(room, 'is_reconnecting', False)),
                'is_recording_starting': room_id in _recording_starting,
                'last_error': getattr(room, 'last_error', '') or '',
                'pipeline_health': build_room_health(
                    room,
                    supervisor=ingest_registry.get_supervisor_if_exists(room_id),
                ),
            })
        if not patches:
            return

        def _emit() -> None:
            for patch in patches:
                bridge.queue_broadcast({'type': 'room_updated', 'data': patch})

        loop = _ws_loop_holder.get('loop')
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(_emit)
                return
            except Exception as exc:
                _log.debug("schedule room_updated patches failed: %s", exc)
        _emit()

    # 连接/录制均通过后台 worker 异步完成，状态变更发生在 EventBus emit 时，
    # 必须在此补充 rooms_updated 广播，否则前端房间卡片状态永远停留在旧值。
    # BroadcastHub 已订阅 room_connect_finished / batch_record_progress / recording_stopped
    # 做 WS 专用 payload；此处再订同一事件做 rooms_updated（与旧 bridge+handler 双 connect 同构）。
    # 纪律：订阅回调在编排线程同步执行，禁止再 orch.call 写操作（重入死锁风险）。
    bus = manager.bus
    bus.subscribe("room_connect_finished", lambda *_a: _queue_rooms_update())
    bus.subscribe("batch_record_progress", lambda *_a: _queue_rooms_update())
    bus.subscribe("batch_record_finished", lambda *_a: _queue_rooms_update())
    # 中频 tick：增量刷新录制文件大小；低频 tick：全量 snapshot 愈合漂移
    bus.subscribe("medium_tick", lambda: _queue_recording_size_patches())
    bus.subscribe("low_tick", lambda: _queue_rooms_update())

    def _capture_ws_loop(loop: asyncio.AbstractEventLoop | None = None) -> asyncio.AbstractEventLoop | None:
        if loop is not None and not loop.is_closed():
            _ws_loop_holder['loop'] = loop
            return loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None and not running.is_closed():
            _ws_loop_holder['loop'] = running
            return running
        cached = _ws_loop_holder.get('loop')
        if cached is not None and not cached.is_closed():
            return cached
        return None

    def _on_manager_recording_stopped_offline(room_id: str, reason: str, message: str) -> None:
        """录制侧重连判定下播时，若预览仍开启则切换为文件回看。"""
        if reason != 'offline':
            return
        loop = _capture_ws_loop()
        if loop is None or not loop.is_running():
            _log.debug(
                "skip offline→file preview: no running WS loop (room_id=%s)",
                room_id,
            )
            return
        asyncio.run_coroutine_threadsafe(
            _start_recording_file_mse(
                server,
                manager,
                bridge,
                room_id,
                loop,
                offline_message=message,
                stop_recording_if_active=False,
            ),
            loop,
        )

    bus.subscribe("recording_stopped", _on_manager_recording_stopped_offline)

    def _on_recording_reconnected(room_id: str) -> None:
        """录制重连成功后重新挂载共享进样预览。

        录制重连（快速路径）会 stop 整个 SharedRoomIngest 并清空预览订阅者，
        而 MSE 自动重连循环可能先行耗尽导致 preview_enabled 被置 false。
        此处兜底重新 attach 预览，恢复画面而无需用户手动重开。
        """
        loop = _capture_ws_loop()
        if loop is None or not loop.is_running():
            _log.debug(
                "skip shared preview reattach on reconnect: no running WS loop (room_id=%s)",
                room_id,
            )
            return
        asyncio.run_coroutine_threadsafe(
            _reattach_shared_preview_after_recording_start(room_id, True),
            loop,
        )

    bus.subscribe("recording_reconnected", _on_recording_reconnected)

    def _broadcast_system_stats():
        """广播系统资源快照到前端。"""
        try:
            settings = load_settings()
            output_dir = _expand_user_path(settings.get('output_dir', ''))
            stats = collect_system_stats(output_dir, extra=_ingest_diagnostics())
            bridge.queue_broadcast({'type': 'system_stats', 'data': stats})
        except Exception as exc:
            _log.debug("System stats broadcast failed: %s", exc)

    bus.subscribe("low_tick", lambda: _broadcast_system_stats())

    @server.on_connect
    async def handle_connect(websocket):
        """新客户端连接时推送当前房间列表与设置。

        不再从磁盘 load_rooms 恢复上次会话：每次启动工作台从空房间开始。
        若本进程内已有房间（例如前端热重连），则推送内存中的当前列表。
        """
        _capture_ws_loop()

        # 推送 manager 中的房间（不恢复持久化）
        await websocket.send(json.dumps({
            'type': 'rooms_loaded',
            'data': {'rooms': _rooms_list(manager)},
        }))
        # 推送已保存的设置（含 appSettings 主题/语言等），确保前端启动时恢复记忆
        try:
            saved = load_settings()
            await websocket.send(json.dumps({
                'type': 'settings_loaded',
                'data': saved,
            }))
        except Exception as exc:
            _log.error("Push settings on connect failed: %s", exc)

        # 推送当前持续分析状态（如果有）
        with _analysis_jobs_lock:
            _ct_items = list(_continuous_tasks.items())
        if _ct_items:
            active_room_id, task = _ct_items[0]
            phase = 'finalizing' if task.get('finalizing') else ('completed' if task.get('completed') else 'running')
            scan_range = task.get('scan_range', (0.0, 0.0))
            await websocket.send(json.dumps({
                'type': 'continuous_analysis_status',
                'data': {
                    'running': phase != 'completed',
                    'room_id': active_room_id,
                    'target_room_ids': task.get('target_room_ids', []),
                    'mode': task.get('mode', 'scene'),
                    'analyzed_duration': task.get('last_analyzed', 0.0),
                    'total_highlights': len(task.get('highlights', [])),
                    'phase': phase,
                    'updated_at': time.time(),
                    'scan_mode': 'full' if task.get('full_rescan') else 'incremental',
                    'scan_range': list(scan_range) if isinstance(scan_range, tuple) else scan_range,
                    'scan_timeout': task.get('scan_timeout', 120),
                    'full_rescan': bool(task.get('full_rescan', False)),
                    'refine_with_ocr': bool(task.get('refine_with_ocr', False)),
                    'progress': min(100.0, max(0.0, (task.get('last_analyzed', 0.0) / max(float(scan_range[1]) if isinstance(scan_range, tuple) and len(scan_range) > 1 else 1.0, 1.0)) * 100.0)),
                },
            }))

        # 预检测 NVENC 可用性（在后台线程中执行，不阻塞连接流程）
        # 首次预览时无需再等待 NVENC 检测，减少 1-3 秒延迟
        def _precheck_nvenc():
            try:
                from lsc.core.services.mse_streamer import _check_nvenc
                _check_nvenc()
            except Exception as exc:
                _log.warning("NVENC precheck failed: %s", exc)
        asyncio.get_running_loop().run_in_executor(None, _precheck_nvenc)

    @server.on('get_rooms')
    async def handle_get_rooms(data):
        """获取当前所有房间列表。"""
        def _do_get():
            return {'rooms': _rooms_list(manager)}

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _bridge_executor, lambda: bridge.manager.call(_do_get)
        )
        _log.debug("获取房间列表: %d 个", len(result['rooms']))
        return result

    @server.on('refresh_room_status')
    async def handle_refresh_room_status(data):
        """刷新房间状态：清除错误标记，不阻断正在进行的录制/预览/分析。

        只清除 last_error / preview_error 等瞬态错误字段，
        不触碰 is_recording / is_reconnecting / is_connecting 等运行状态。
        正在重连的房间保留错误信息（用户需要看到重连进度）。
        """
        room_id = data.get('room_id')

        def _do_refresh():
            rooms_to_refresh = []
            if room_id:
                room = manager.get_room(room_id)
                if room:
                    rooms_to_refresh = [room]
            else:
                rooms_to_refresh = list(manager._rooms.values())
            refreshed = 0
            for room in rooms_to_refresh:
                if getattr(room, 'is_reconnecting', False):
                    continue
                if getattr(room, 'last_error', None):
                    room.last_error = None  # type: ignore[assignment]
                    refreshed += 1
                if getattr(room, 'preview_error', None):
                    room.preview_error = None  # type: ignore[assignment]
                    refreshed += 1
            return refreshed

        loop = asyncio.get_running_loop()
        refreshed = await loop.run_in_executor(
            _bridge_executor, lambda: bridge.manager.call(_do_refresh)
        )
        _broadcast_rooms()
        _log.info("刷新房间状态: %d 个房间错误已清除", refreshed)
        return {'success': True, 'refreshed': refreshed}

    @server.on('save_rooms')
    async def handle_save_rooms(data):
        """保存前端传入的房间列表。"""
        rooms = data.get('rooms', [])
        if not isinstance(rooms, list):
            _log.warning("save_rooms 校验失败: rooms 不是列表")
            return {'success': False, 'error': 'rooms 必须是列表'}
        for room in rooms:
            if not isinstance(room, dict):
                _log.warning("save_rooms 校验失败: 房间数据不是对象")
                return {'success': False, 'error': '房间数据必须是对象'}
            if not isinstance(room.get('room_id'), str):
                _log.warning("save_rooms 校验失败: room_id 不是字符串")
                return {'success': False, 'error': 'room_id 必须是字符串'}
            if not isinstance(room.get('room_url'), str):
                _log.warning("save_rooms 校验失败: room_url 不是字符串")
                return {'success': False, 'error': 'room_url 必须是字符串'}
        success = save_rooms(rooms)
        _log.info("save_rooms: 保存 %d 个房间, success=%s", len(rooms), success)
        return {'success': success}

    async def _validate_url_with_timeout(url: object) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    _recording_executor,
                    _validate_room_url_candidate,
                    url,
                ),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            clean_url = url.strip() if isinstance(url, str) else ""
            _log.warning("验证直播间链接超时: url=%s", redact_url(clean_url))
            return _invalid_room_url_result(
                clean_url,
                "验证直播间链接超时，请检查网络后重试",
                "validation_timeout",
            )
        except Exception as exc:
            clean_url = url.strip() if isinstance(url, str) else ""
            _log.error("验证直播间链接异常: url=%s, error=%s", redact_url(clean_url), redact_text(exc))
            return _invalid_room_url_result(
                clean_url,
                redact_text(humanize_error(str(exc))),
                "validation_failed",
            )

    @server.on('validate_room_url')
    async def handle_validate_room_url(data):
        """验证单个直播间链接，不创建房间。"""
        result = await _validate_url_with_timeout(data.get('url', ''))
        return {
            'success': bool(result.get('valid')),
            **result,
        }

    @server.on('validate_room_urls')
    async def handle_validate_room_urls(data):
        """并行验证一批直播间链接；任一无效时整批不应进入添加流程。"""
        urls = data.get('urls', [])
        if not isinstance(urls, list):
            return {
                'success': False,
                'valid': False,
                'results': [],
                'error': 'urls 必须是链接列表',
            }
        if not urls:
            return {
                'success': False,
                'valid': False,
                'results': [],
                'error': '请输入直播间链接',
            }
        if len(urls) > _MAX_ROOM_URLS_PER_ADD:
            return {
                'success': False,
                'valid': False,
                'results': [],
                'error': f'一次最多验证 {_MAX_ROOM_URLS_PER_ADD} 个直播间链接',
            }

        results = await asyncio.gather(*(_validate_url_with_timeout(url) for url in urls))
        all_valid = all(bool(result.get('valid')) for result in results)
        invalid_count = sum(1 for result in results if not result.get('valid'))
        _log.info(
            "直播间链接验证完成: total=%d, valid=%d, invalid=%d",
            len(results),
            len(results) - invalid_count,
            invalid_count,
        )
        response: dict[str, Any] = {
            'success': all_valid,
            'valid': all_valid,
            'results': results,
        }
        if not all_valid:
            response['error'] = (
                results[0].get('error', '直播间链接无效')
                if len(results) == 1
                else f'有 {invalid_count} 个链接未通过验证，请修正后重试'
            )
        return response

    @server.on('add_room')
    async def handle_add_room(data):
        """添加新房间（通过直播间 URL）。"""
        url = data.get('url', '').strip()
        if not url:
            return {'success': False, 'error': '请输入直播间链接'}
        _log.info("添加房间: url=%s", redact_url(url))

        def _add():
            return manager.add_room(url)

        try:
            room = await asyncio.get_running_loop().run_in_executor(
                _bridge_executor, lambda: bridge.manager.call(_add, timeout=30.0)
            )
        except TimeoutError:
            _log.warning("添加房间超时: url=%s", redact_url(url))
            return {'success': False, 'error': '添加房间超时，请重试', 'suggestion': '请检查网络连接，或稍后重试'}
        except Exception as exc:
            _log.error("添加房间异常: url=%s, error=%s", redact_url(url), redact_text(exc))
            return _error_response(exc)

        if room is None:
            _log.warning("添加房间失败（达上限）: url=%s", redact_url(url))
            return {'success': False, 'error': '房间数已达上限'}

        _broadcast_rooms()
        _persist_current_rooms(manager)
        _log.info("房间添加成功: room_id=%s, platform=%s, streamer=%s", room.room_id, room.platform, room.streamer_name)
        return {'success': True, 'room_id': room.room_id}

    @server.on('connect_room')
    async def handle_connect_room(data):
        """连接指定房间的直播间。

        async_mode 下本响应仅表示「是否受理」后台连接任务，真正结果由
        ``room_connect_finished`` 广播。契约：
        - 受理成功: ``{success: True, accepted: True, async: True, room_id}``
        - 已在连接 / 启动失败: ``{success: False, accepted: False, error, room_id}``
        """
        room_id = data.get('room_id')
        if not room_id:
            return {'success': False, 'accepted': False, 'error': 'room_id is required'}
        _log.info("连接房间: room_id=%s", room_id)

        def _connect():
            settings = load_settings()
            return manager.connect_room(room_id, async_mode=True, quality_preset=settings.get('quality', '原画'))

        try:
            accepted = await asyncio.get_running_loop().run_in_executor(
                _bridge_executor, lambda: bridge.manager.call(_connect)
            )
        except Exception as exc:
            _log.error("连接房间异常: room_id=%s, error=%s", room_id, exc)
            return {
                'success': False,
                'accepted': False,
                'error': humanize_error(str(exc)),
                'room_id': room_id,
            }
        _broadcast_rooms(force=True)
        if not accepted:
            _log.info("连接房间未受理: room_id=%s (已在连接中或房间不存在)", room_id)
            return {
                'success': False,
                'accepted': False,
                'error': '房间不存在或已在连接中',
                'room_id': room_id,
            }
        _log.info("连接房间已受理(异步): room_id=%s", room_id)
        return {
            'success': True,
            'accepted': True,
            'room_id': room_id,
            'async': True,
        }

    @server.on('disconnect_room')
    async def handle_disconnect_room(data):
        """断开指定房间的连接。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        _log.info("断开房间连接: room_id=%s", room_id)

        # 清理 MSE streamer，防止僵尸 streamer 阻止后续预览启动
        stale_streamer = _preview_stream_registry().pop(room_id)
        if stale_streamer is not None:
            _log.info("清理断开房间的 MSE streamer: room_id=%s", room_id)
            def _stop_streamer():
                try:
                    stale_streamer.stop()
                except Exception as exc:
                    _log.debug("停止 streamer 失败 (disconnect): %s", exc)
            await asyncio.get_running_loop().run_in_executor(_bridge_executor, _stop_streamer)
            _stop_idle_shared_ingest(room_id, reason="room disconnected")

        # 房间断开时：仅当断开的房间是参考房或对齐组剩余不足 2 个房间时，
        # 才全组失效 TimelineContext；否则仅从对齐组移除该房间，保留公共轴
        def _soft_disconnect():
            svc = get_timeline_service()
            ctx = svc.get_active_timeline_for_room(room_id)
            if ctx is None:
                return {'invalidated': False, 'reason': 'no_timeline'}

            # 检查断开的房间是否为参考房
            if ctx.reference_room_id == room_id:
                svc.invalidate_timeline(ctx.timeline_id, f"reference_disconnected:{room_id}")
                return {'invalidated': True, 'reason': 'reference_room_disconnected'}

            # 检查剩余的活跃房间数量（排除当前断房的 timeline 绑定）
            remaining = [rid for rid in ctx.room_snapshots if rid != room_id]
            if len(remaining) < 2:
                svc.invalidate_timeline(ctx.timeline_id, f"rooms_below_minimum:{room_id}")
                return {'invalidated': True, 'reason': 'insufficient_rooms'}

            # 非参考房且剩余 >=2：仅清除该房间的 align_group_id，保留 TimelineContext
            room = manager.get_room(room_id)
            if room is not None:
                room.align_group_id = ''
                room.content_offset = 0.0
            _log.info(
                "非参考房断开，保留公共轴: room_id=%s, timeline_id=%s, remaining=%d",
                room_id, ctx.timeline_id, len(remaining),
            )
            return {'invalidated': False, 'reason': 'room_removed_from_group'}

        try:
            result = await asyncio.get_running_loop().run_in_executor(
                _bridge_executor, lambda: bridge.manager.call(_soft_disconnect),
            )
            if result.get('invalidated'):
                _invalidate_msg = (
                    "参考房断开，公共时间轴已失效，请重新一键对齐"
                    if result.get('reason') == 'reference_room_disconnected'
                    else "对齐组房间不足 2 个，公共时间轴已失效，请重新一键对齐"
                )
                bridge.queue_broadcast({
                    'type': 'timeline_invalidated_broadcast',
                    'data': {'message': _invalidate_msg, 'reason': result.get('reason')},
                })
            else:
                _log.info("断房保留公共轴: room_id=%s, msg=%s", room_id, result.get('reason'))
                bridge.queue_broadcast({
                    'type': 'timeline_room_removed',
                    'data': {
                        'room_id': room_id,
                        'message': f'已断开房间 {room_id[:8]}...，公共时间轴仍可用',
                    },
                })
        except Exception as exc:
            _log.warning("软断房判断失败，回退全组失效: room_id=%s, error=%s", room_id, exc)
            _invalidate_room_timeline(room_id, reason=f"room_disconnected_fallback:{room_id}")

        manager.submit(manager.disconnect_room, room_id)
        _broadcast_rooms(force=True)
        _log.info("断开连接指令已提交: room_id=%s", room_id)
        return {'success': True}

    @server.on('set_preview_muted')
    async def handle_set_preview_muted(data):
        """设置房间预览静音状态。"""
        room_id = data.get('room_id')
        muted = bool(data.get('muted', False))
        if not room_id:
            return {'error': 'room_id is required'}
        _log.debug("设置静音: room_id=%s, muted=%s", room_id, muted)

        # 必须等编排线程写完 preview_muted 再广播，否则会用旧值覆盖前端乐观更新
        try:
            await asyncio.get_running_loop().run_in_executor(
                _bridge_executor,
                lambda: bridge.manager.call(manager.set_preview_muted, room_id, muted),
            )
        except Exception as exc:
            _log.warning("设置静音失败: room_id=%s, error=%s", room_id, exc)
            return {'success': False, 'error': humanize_error(str(exc)), 'room_id': room_id}
        _broadcast_rooms(force=True)
        return {'success': True, 'room_id': room_id}

    @server.on('set_preview_quality')
    async def handle_set_preview_quality(data):
        """保存预览画质并立即重启预览以生效（支持录制中切换）。"""
        room_id = data.get('room_id')
        quality = data.get('quality')
        if not room_id or not quality:
            return {'success': False, 'error': 'room_id and quality are required'}
        _quality_map = {
            'original': '原画', 'hd': '高清', 'sd': '标清', 'ld': '流畅',
            'high': '高清', 'medium': '标清', 'low': '流畅',
            '原画': '原画', '高清': '高清', '标清': '标清', '流畅': '流畅',
        }
        quality = _quality_map.get(quality, quality)
        _log.info("保存预览画质: room_id=%s, quality=%s", room_id, quality)

        # 1. 保存到全局 settings
        settings = load_settings()
        settings['preview_quality'] = quality
        save_settings(settings)

        # 2. 更新 room.preview_quality（前端可感知当前画质）
        def _set_room_quality():
            room = manager.get_room(room_id)
            if room is not None:
                room.preview_quality = quality
            return room is not None
        try:
            await asyncio.get_running_loop().run_in_executor(
                _bridge_executor, lambda: bridge.manager.call(_set_room_quality)
            )
        except Exception as exc:
            _log.warning("更新 room.preview_quality 失败: room_id=%s, error=%s", room_id, exc)

        # 3. 如果预览正在运行，重启预览以应用新画质
        def _check_preview():
            room = manager.get_room(room_id)
            if room is not None:
                return room.preview_enabled and room.is_connected
            return False
        try:
            was_preview_enabled = await asyncio.get_running_loop().run_in_executor(
                _bridge_executor, lambda: bridge.manager.call(_check_preview)
            )
        except Exception:
            was_preview_enabled = False

        if was_preview_enabled:
            _log.info("重启预览以应用新画质: room_id=%s, quality=%s", room_id, quality)
            # 停止预览（等 stop 完成再启动，消除竞态）
            try:
                await _handle_mse_preview(server, manager, room_id, False, {'mode': 'mse'})
            except Exception as exc:
                _log.warning("停止预览失败(画质切换): room_id=%s, error=%s", room_id, exc)
            # 重新启动预览（force_restart=True 确保录制中也重启 FFmpeg）
            try:
                await _handle_mse_preview(server, manager, room_id, True, {'mode': 'mse'}, force_restart=True)
            except Exception as exc:
                _log.error("重启预览失败: room_id=%s, error=%s", room_id, exc)

        _broadcast_rooms()
        return {'success': True}

    @server.on('start_recording')
    async def handle_start_recording(data):
        """开始录制指定房间（支持并发限流，最多同时 2 路）。"""
        _rec_log = logging.getLogger('lsc.recording')
        room_id = data.get('room_id')
        _rec_log.info("[录制] start_recording request for room_id=%s", room_id)
        if not room_id:
            return {'error': 'room_id is required'}

        settings = load_settings()
        requested_spec = data.get('recording_spec')
        if not isinstance(requested_spec, dict):
            requested_spec = {}

        def _spec_choice(name, default, allowed):
            value = requested_spec.get(name, default)
            return value if value in allowed else default

        output_dir = _expand_user_path(settings.get('output_dir', os.path.join(os.path.expanduser('~'), 'LSC', 'output')))
        encoder = _spec_choice(
            'encoder',
            settings.get('encoder', 'h264_nvenc'),
            {
                'h264_nvenc', 'hevc_nvenc', 'h264_qsv', 'h264_amf',
                'libx264', 'libx265', 'copy',
                'H.264 NVENC', 'H.265 NVENC', 'H.264 CPU', 'H.265 CPU', 'Copy',
            },
        )
        try:
            crf = max(0, min(51, int(requested_spec.get('crf', settings.get('crf', 23)))))
        except (TypeError, ValueError):
            crf = 23
        param_mode = _spec_choice(
            'param_mode',
            settings.get('param_mode', 'CRF 质量'),
            {'CRF 质量', '自定义码率', '码率限制', '不限制'},
        )
        raw_bitrate = str(requested_spec.get('bitrate', settings.get('bitrate', 8000)))
        bitrate = raw_bitrate if raw_bitrate.replace('.', '', 1).isdigit() else '8000'
        bitrate_unit = _spec_choice(
            'bitrate_unit', settings.get('bitrate_unit', 'kbps'), {'kbps', 'Mbps'},
        )
        resolution = _spec_choice(
            'resolution',
            settings.get('resolution', '原画'),
            {'原画', '1920:1080', '1280:720', '854:480'},
        )
        framerate = _spec_choice(
            'framerate', settings.get('framerate', '原画'), {'原画', '60', '30', '24'},
        )
        audio_bitrate = _spec_choice(
            'audio_bitrate', settings.get('audio_bitrate', '128k'), {'128k', '192k', '256k'},
        )
        _rec_log.info(
            "[录制] effective spec room=%s encoder=%s mode=%s crf=%s bitrate=%s%s resolution=%s fps=%s audio=%s",
            room_id, encoder, param_mode, crf, bitrate, bitrate_unit,
            resolution, framerate, audio_bitrate,
        )

        def _start():
            # 注意：不通过 orchestrator.call 切回编排线程，而是在 executor 线程中
            # 直接调用 manager.start_recording。这与 _BatchRecordWorker 的模式
            # 一致（manager 仅做属性读写 + subprocess 启动，无需编排线程串行化），
            # 避免刷新流 URL（HTTP 请求最长 36s）和 FFmpeg 首帧探测（最长 5s）
            # 阻塞编排线程导致预览/心跳冻结。项目记忆硬约束：
            # "Recording start/preview reconnect operations must be executed in
            #  background threads to prevent main thread blocking"。
            return manager.start_recording(
                room_id, output_dir, encoder, crf,
                param_mode=param_mode, bitrate=bitrate, bitrate_unit=bitrate_unit,
                resolution=resolution, framerate=framerate, audio_bitrate=audio_bitrate,
                _run_in_background=True,
            )

        # 防重复提交：同一房间正在启动录制时拒绝重复请求
        if room_id in _recording_starting:
            _rec_log.warning("[录制] room %s already starting", room_id)
            return {
                'success': False,
                'error': '该房间正在启动录制中，请稍候',
                'room_id': room_id,
            }
        _recording_starting.add(room_id)
        # 立即广播 is_recording_starting，让前端按钮立刻进入 loading
        _broadcast_rooms(force=True)
        success = False
        error_msg: str | None = None
        try:
            # 并发限流：最多 2 路同时启动。Semaphore 已满，或已有 ≥2 路正在启动
            # （本房间已计入 _recording_starting，故 len > 2）时标为排队，避免假死。
            should_queue = _recording_semaphore.locked() or len(_recording_starting) > 12
            if should_queue:
                if room_id not in _recording_wait_queue:
                    _recording_wait_queue.append(room_id)
                position = _recording_wait_queue.index(room_id) + 1
                bridge.queue_broadcast({
                    'type': 'recording_queue',
                    'data': {'room_id': room_id, 'position': position, 'waiting': True},
                })
                _broadcast_rooms(force=True)

            _rec_log.info("[录制] acquiring semaphore for room %s", room_id)
            await _recording_semaphore.acquire()
            try:
                if room_id in _recording_wait_queue:
                    _recording_wait_queue.remove(room_id)
                _broadcast_rooms(force=True)
                _rec_log.info("[录制] semaphore acquired, submitting to executor for room %s", room_id)
                success = await asyncio.get_running_loop().run_in_executor(_recording_executor, _start)
                _rec_log.info("[录制] executor returned success=%s for room %s", success, room_id)
            finally:
                _recording_semaphore.release()
        except Exception as exc:
            _rec_log.error("[录制] exception for room %s: %s", room_id, exc, exc_info=True)
            error_msg = humanize_error(str(exc))
            success = False
        finally:
            _recording_starting.discard(room_id)
            if room_id in _recording_wait_queue:
                _recording_wait_queue.remove(room_id)

        # 录制启动本身在后台线程执行。此处只需要读取最终状态，不能再通过
        # orchestrator.call() 排队等待主编排线程；主线程正忙时会把已成功启动的
        # 录制误报为 "orchestrator call timed out"。
        def _read_recording_status() -> dict[str, Any]:
            status_reader = getattr(manager, 'get_recording_status', None)
            if callable(status_reader):
                try:
                    status = status_reader(room_id)
                    if isinstance(status, dict):
                        return status
                except Exception as exc:
                    _rec_log.warning(
                        "[录制] get_recording_status failed, fallback to room snapshot: room=%s, error=%s",
                        room_id,
                        exc,
                    )

            # 兼容轻量 manager / 迁移期间的管理器实现。状态读取失败不能让已经
            # 成功启动的录制 handler 直接抛异常，也不能跳过共享预览重挂载。
            room = manager.get_room(room_id)
            if room is None:
                return {}
            return {
                'is_recording': bool(getattr(room, 'is_recording', False)),
                'last_error': str(getattr(room, 'last_error', '') or ''),
                'streamer_name': str(getattr(room, 'streamer_name', '') or ''),
                'platform_name': str(getattr(room, 'platform_name', '') or ''),
                'preview_enabled': bool(getattr(room, 'preview_enabled', False)),
            }

        recording_status = await asyncio.get_running_loop().run_in_executor(
            _recording_executor, _read_recording_status
        )
        is_recording = bool(recording_status.get('is_recording'))
        last_err = str(recording_status.get('last_error') or '')
        # 启动函数返回成功并不等于录制进程仍在工作。例如 FFmpeg 在首帧后立刻
        # 退出时，旧逻辑会把房间显示成“录制中”，持续分析则只能一直等待空文件。
        # 以最终状态为准，禁止把这种情况当作录制成功。
        if success and not is_recording:
            success = False
            error_msg = last_err or '录制进程未保持运行，未写入有效录像文件'
            _rec_log.warning("[录制] startup reported success but recording is inactive: room=%s, error=%s", room_id, error_msg)
        if is_recording:
            with _recording_history_lock:
                recording_history.append({
                    'title': recording_status.get('streamer_name') or '未知主播',
                    'platform': recording_status.get('platform_name') or '',
                    'start_time': datetime.now().isoformat(),
                    'room_id': room_id,
                })
                # 裁剪至上限，防止 24x7 长期运行时无限膨胀（#18）
                if len(recording_history) > _MAX_RECORDING_HISTORY:
                    del recording_history[:len(recording_history) - _MAX_RECORDING_HISTORY]
                _save_recording_history(recording_history)
            if success:
                await _reattach_shared_preview_after_recording_start(
                    room_id, bool(recording_status.get('preview_enabled'))
                )

        _broadcast_rooms(force=True)
        if error_msg is not None:
            return {'success': False, 'error': error_msg, 'room_id': room_id}
        if not success:
            fail_msg = last_err or '录制启动失败，请检查房间状态'
            _rec_log.warning("[录制] failed for room %s, last_error=%s", room_id, fail_msg)
            return {'success': False, 'error': humanize_error(fail_msg), 'room_id': room_id}
        return {'success': True, 'room_id': room_id}

    @server.on('stop_recording')
    async def handle_stop_recording(data):
        """停止录制指定房间。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        _log.info("停止录制: room_id=%s", room_id)

        def _stop_async():
            return manager.stop_recording_async(room_id)

        try:
            success = await asyncio.get_running_loop().run_in_executor(
                _bridge_executor, lambda: bridge.manager.call(_stop_async, timeout=5.0)
            )
        except Exception as exc:
            _log.error("停止录制异常: room_id=%s, error=%s", room_id, exc)
            _broadcast_rooms()
            return {'success': False, 'error': humanize_error(str(exc))}
        _log.info("停止录制完成: room_id=%s, success=%s", room_id, success)

        with _recording_history_lock:
            for record in reversed(recording_history):
                if record.get('room_id') == room_id and 'end_time' not in record:
                    record['end_time'] = datetime.now().isoformat()
                    start = datetime.fromisoformat(record['start_time'])
                    end = datetime.fromisoformat(record['end_time'])
                    duration = (end - start).total_seconds()
                    record['duration'] = f"{int(duration // 3600):02d}:{int((duration % 3600) // 60):02d}:{int(duration % 60):02d}"
                    break
            _save_recording_history(recording_history)

        _broadcast_rooms()
        return {'success': bool(success)}

    @server.on('remove_room')
    async def handle_remove_room(data):
        """移除指定房间。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        _log.info("移除房间: room_id=%s", room_id)

        stale_streamer = _preview_stream_registry().pop(room_id)
        if stale_streamer is not None:
            _log.info("清理移除房间的 MSE streamer: room_id=%s", room_id)
            def _stop_streamer():
                try:
                    stale_streamer.stop()
                except Exception as exc:
                    _log.debug("停止 streamer 失败 (remove): %s", exc)
            await asyncio.get_running_loop().run_in_executor(_bridge_executor, _stop_streamer)
            _stop_idle_shared_ingest(room_id, reason="room removed")

        # 房间移除时：采用与 disconnect 相同的软失效策略
        def _soft_remove():
            svc = get_timeline_service()
            ctx = svc.get_active_timeline_for_room(room_id)
            if ctx is None:
                return {'invalidated': False, 'reason': 'no_timeline'}
            if ctx.reference_room_id == room_id:
                svc.invalidate_timeline(ctx.timeline_id, f"reference_removed:{room_id}")
                return {'invalidated': True, 'reason': 'reference_room_removed'}
            remaining = [rid for rid in ctx.room_snapshots if rid != room_id]
            if len(remaining) < 2:
                svc.invalidate_timeline(ctx.timeline_id, f"rooms_below_minimum_removal:{room_id}")
                return {'invalidated': True, 'reason': 'insufficient_rooms'}
            room = manager.get_room(room_id)
            if room is not None:
                room.align_group_id = ''
                room.content_offset = 0.0
            return {'invalidated': False, 'reason': 'room_removed_from_group'}

        try:
            result = await asyncio.get_running_loop().run_in_executor(
                _bridge_executor, lambda: bridge.manager.call(_soft_remove),
            )
            if result.get('invalidated'):
                _invalidate_msg = (
                    "参考房被移除，公共时间轴已失效，请重新一键对齐"
                    if result.get('reason') == 'reference_room_removed'
                    else "对齐组房间不足 2 个，公共时间轴已失效，请重新一键对齐"
                )
                bridge.queue_broadcast({
                    'type': 'timeline_invalidated_broadcast',
                    'data': {'message': _invalidate_msg, 'reason': result.get('reason')},
                })
            else:
                bridge.queue_broadcast({
                    'type': 'timeline_room_removed',
                    'data': {
                        'room_id': room_id,
                        'message': f'已移除房间 {room_id[:8]}...，公共时间轴仍可用',
                    },
                })
        except Exception as exc:
            _log.warning("软移除判断失败，回退全组失效: room_id=%s, error=%s", room_id, exc)
            _invalidate_room_timeline(room_id, reason=f"room_removed_fallback:{room_id}")

        await asyncio.get_running_loop().run_in_executor(
            _bridge_executor, lambda: bridge.manager.call(manager.remove_room, room_id)
        )
        _broadcast_rooms()
        _persist_current_rooms(manager)
        _log.info("房间已移除: room_id=%s", room_id)
        return {'success': True}

    @server.on('seek')
    async def handle_seek(data):
        """跳转到指定时间位置。"""
        room_id = data.get('room_id')
        time_pos = _safe_float(data.get('time', 0))
        if not room_id:
            return {'error': 'room_id is required'}
        _log.debug("seek: room_id=%s, time=%.2f", room_id, time_pos)

        def _seek():
            room = manager.get_room(room_id)
            if room is None or room.controller is None:
                return False
            controller = room.controller
            controller.current_sec = time_pos
            widget = room.preview_widget
            if widget is not None:
                seek_fn = getattr(widget, 'seek', None)
                if callable(seek_fn):
                    try:
                        seek_fn(time_pos)
                        return True
                    except Exception as exc:
                        _log.debug("操作异常（已忽略）: %s", exc)
            return True

        success = await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.manager.call(_seek))
        return {'success': bool(success)}

    @server.on('set_mark_in')
    async def handle_set_mark_in(data):
        """设置入点（剪辑起始标记）。

        live=True（默认）：实时按键标记，捕获 wallclock 用于精确导出映射。
        live=False：时间线拖动标记，不捕获 wallclock，导出时走降级路径。
        """
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        time_value = data.get('time')
        live = data.get('live', True)
        _log.debug("设置入点: room_id=%s, time=%s, live=%s", room_id, time_value, live)

        # 实时标记才捕获 wallclock；拖动标记不捕获（wallclock 不代表内容时刻）
        captured_wallclock = time.monotonic() if live else None
        # 删除入点 (time: null) 场景不需要 wallclock
        if time_value is None and 'time' in data:
            captured_wallclock = None

        def _mark():
            room = manager.get_room(room_id)
            if room is None:
                return None
            if time_value is None and 'time' in data:
                # time: null → 删除入点
                room.mark_in = None
                room.mark_in_wallclock = None
                return None
            if time_value is not None:
                room.mark_in = float(time_value)
            else:
                room.mark_in = _get_current_pos(room)
            room.mark_in_wallclock = captured_wallclock
            return room.mark_in

        value = await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.manager.call(_mark))
        _broadcast_rooms()
        return {'success': True, 'mark_in': value}

    @server.on('set_mark_out')
    async def handle_set_mark_out(data):
        """设置出点（剪辑结束标记）。

        live=True（默认）：实时按键标记，捕获 wallclock 用于精确导出映射。
        live=False：时间线拖动标记，不捕获 wallclock，导出时走降级路径。
        """
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        time_value = data.get('time')
        live = data.get('live', True)
        _log.debug("设置出点: room_id=%s, time=%s, live=%s", room_id, time_value, live)

        captured_wallclock = time.monotonic() if live else None
        if time_value is None and 'time' in data:
            captured_wallclock = None

        def _mark():
            room = manager.get_room(room_id)
            if room is None:
                return None
            if time_value is None and 'time' in data:
                # time: null → 删除出点
                room.mark_out = None
                room.mark_out_wallclock = None
                return None
            if time_value is not None:
                room.mark_out = float(time_value)
            else:
                room.mark_out = _get_current_pos(room)
            room.mark_out_wallclock = captured_wallclock
            return room.mark_out

        value = await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.manager.call(_mark))
        _broadcast_rooms()
        return {'success': True, 'mark_out': value}

    @server.on('toggle_play_pause')
    async def handle_toggle_play_pause(data):
        """切换预览播放/暂停。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        _log.debug("切换播放/暂停: room_id=%s", room_id)

        def _toggle():
            room = manager.get_room(room_id)
            if room is None:
                return False
            room.preview_paused = not room.preview_paused
            _log.debug("toggle_play_pause: room_id=%s, paused=%s", room_id, room.preview_paused)
            widget = room.preview_widget
            if widget is not None:
                pause_fn = getattr(widget, 'pause', None)
                if callable(pause_fn):
                    try:
                        pause_fn(room.preview_paused)
                        return True
                    except Exception as exc:
                        _log.debug("操作异常（已忽略）: %s", exc)
            return True

        success = await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.manager.call(_toggle))
        _broadcast_rooms()
        return {'success': bool(success)}

    @server.on('get_disk_usage')
    async def handle_get_disk_usage(data):
        """获取磁盘使用情况。"""
        _log.debug("获取磁盘使用情况")
        return get_disk_usage_info()

    @server.on('get_system_stats')
    async def handle_get_system_stats(data):
        """获取系统资源快照。"""
        _log.debug("获取系统资源快照")
        settings = load_settings()
        output_dir = _expand_user_path(settings.get('output_dir', ''))
        stats = collect_system_stats(output_dir, extra=_ingest_diagnostics())
        return {'type': 'system_stats', 'data': stats}

    @server.on('get_settings')
    async def handle_get_settings(data):
        """获取应用设置。"""
        _log.debug("获取应用设置")
        settings = dict(load_settings())
        # UI 缺省时回填运行时真值，避免开关显示与实际模式不一致
        if 'shared_ingest_enabled' not in settings:
            settings['shared_ingest_enabled'] = bool(
                getattr(load_config(), 'shared_ingest_enabled', False)
            )
        if 'ocr_accel' not in settings:
            settings['ocr_accel'] = 'dml'
        if 'jianying_draft_dir' not in settings:
            settings['jianying_draft_dir'] = ''
        return settings

    @server.on('save_settings')
    async def handle_save_settings(data):
        """保存应用设置。"""
        if not isinstance(data, dict):
            _log.warning("save_settings 校验失败: data 不是对象")
            return {'success': False, 'error': '设置数据必须是对象'}
        if not isinstance(data.get('output_dir'), str):
            _log.warning("save_settings 校验失败: output_dir 不是字符串")
            return {'success': False, 'error': 'output_dir 必须是字符串'}
        if not _is_allowed_output_dir(data.get('output_dir', '')):
            _log.warning("save_settings 校验失败: output_dir 不在允许范围内")
            return {'success': False, 'error': '导出目录不在允许范围内'}
        if data.get('jianying_draft_dir') is not None and not isinstance(data.get('jianying_draft_dir'), str):
            _log.warning("save_settings 校验失败: jianying_draft_dir 不是字符串")
            return {'success': False, 'error': 'jianying_draft_dir 必须是字符串'}
        try:
            save_settings(data)
        except ValueError as exc:
            _log.warning("save_settings 校验失败: %s", exc)
            return {'success': False, 'error': str(exc)}
        except OSError as exc:
            _log.error("保存设置失败: %s", exc)
            return {'success': False, 'error': humanize_error(str(exc))}
        _log.info(
            "设置已保存: output_dir=%s, shared_ingest_enabled=%s",
            data.get('output_dir', ''),
            data.get('shared_ingest_enabled'),
        )
        return {'success': True}

    @server.on('get_douyin_cookie_status')
    async def handle_get_douyin_cookie_status(data):
        """查询抖音 Cookie 是否已配置。"""
        from lsc.platforms.cookie_helper import get_douyin_cookie_status
        try:
            return {'success': True, **get_douyin_cookie_status()}
        except Exception as exc:
            _log.warning("get_douyin_cookie_status failed: %s", redact_text(exc))
            return {'success': False, 'error': redact_text(exc), 'configured': False, 'count': 0}

    @server.on('save_douyin_cookies')
    async def handle_save_douyin_cookies(data):
        """保存用户粘贴的抖音 Cookie（JSON / Cookie 头）。"""
        from lsc.platforms.cookie_helper import save_douyin_cookies_from_text
        raw = ''
        if isinstance(data, dict):
            raw = str(data.get('cookies') or data.get('text') or '')
        if not raw.strip():
            return {'success': False, 'error': '请粘贴 Cookie 内容'}
        # 限制 Cookie 输入大小，防止超大 payload 导致 OOM（正常 Cookie < 16KB）
        _MAX_COOKIE_BYTES = 1 * 1024 * 1024  # 1 MB
        if len(raw) > _MAX_COOKIE_BYTES:
            _log.warning("抖音 Cookie 输入过大: %d bytes (limit %d)", len(raw), _MAX_COOKIE_BYTES)
            return {'success': False, 'error': f'Cookie 内容过大（{len(raw)} 字节），请检查输入'}
        try:
            status = save_douyin_cookies_from_text(raw)
            _log.info("抖音 Cookie 已保存: count=%s", status.get('count'))
            credential_status = ''
            try:
                from lsc.platforms.credentials import get_default_credential_provider

                credential_status = get_default_credential_provider().refresh(
                    'douyin'
                ).status.value
            except Exception as exc:
                _log.warning(
                    "refresh douyin credential state failed: %s",
                    redact_text(exc),
                )
            return {
                'success': True,
                **status,
                'credential_status': credential_status or 'INVALID',
            }
        except (ValueError, json.JSONDecodeError) as exc:
            return {'success': False, 'error': redact_text(exc)}
        except OSError as exc:
            _log.error("保存抖音 Cookie 失败: %s", redact_text(exc))
            return {'success': False, 'error': humanize_error(str(exc))}

    @server.on('get_bilibili_cookie_status')
    async def handle_get_bilibili_cookie_status(data):
        """查询 B 站 Cookie 是否已配置。"""
        from lsc.platforms.cookie_helper import get_bilibili_cookie_status
        try:
            return {'success': True, **get_bilibili_cookie_status()}
        except Exception as exc:
            _log.warning("get_bilibili_cookie_status failed: %s", redact_text(exc))
            return {'success': False, 'error': redact_text(exc), 'configured': False, 'count': 0}

    @server.on('get_platform_credential_status')
    async def handle_get_platform_credential_status(data):
        """Return redacted credential state for any registered platform."""
        from lsc.platforms.capabilities import all_platform_capabilities
        from lsc.platforms.credentials import get_default_credential_provider

        platform = str(data.get('platform') or '').strip().lower() if isinstance(data, dict) else ''
        account_ref = str(data.get('account_ref') or 'default').strip() if isinstance(data, dict) else 'default'
        if platform not in all_platform_capabilities():
            return {'success': False, 'error': '不支持的平台', 'status': 'NOT_CONFIGURED'}
        if not account_ref or len(account_ref) > 128:
            return {'success': False, 'error': 'account_ref 无效', 'status': 'INVALID'}
        try:
            provider = get_default_credential_provider()
            status = provider.get_status(platform, account_ref)
            capabilities = all_platform_capabilities()[platform]
            return {
                'success': True,
                'platform': platform,
                'account_ref': account_ref,
                'status': status.value,
                'available': status.value in {'AVAILABLE', 'EXPIRING'},
                'support_level': capabilities.support_level,
                'credential_kinds': list(capabilities.credential_kinds),
            }
        except Exception as exc:
            _log.warning("platform credential status failed platform=%s: %s", platform, redact_text(exc))
            return {'success': False, 'error': '读取凭据状态失败', 'status': 'INVALID'}

    @server.on('save_bilibili_cookies')
    async def handle_save_bilibili_cookies(data):
        """保存用户粘贴的 B 站 Cookie（JSON / Cookie 头）。"""
        from lsc.platforms.cookie_helper import save_bilibili_cookies_from_text
        raw = ''
        if isinstance(data, dict):
            raw = str(data.get('cookies') or data.get('text') or '')
        if not raw.strip():
            return {'success': False, 'error': '请粘贴 Cookie 内容'}
        _MAX_COOKIE_BYTES = 1 * 1024 * 1024  # 1 MB
        if len(raw) > _MAX_COOKIE_BYTES:
            _log.warning("B站 Cookie 输入过大: %d bytes (limit %d)", len(raw), _MAX_COOKIE_BYTES)
            return {'success': False, 'error': f'Cookie 内容过大（{len(raw)} 字节），请检查输入'}
        try:
            status = save_bilibili_cookies_from_text(raw)
            _log.info("B站 Cookie 已保存: count=%s", status.get('count'))
            credential_status = ''
            try:
                from lsc.platforms.credentials import get_default_credential_provider

                credential_status = get_default_credential_provider().refresh(
                    'bilibili'
                ).status.value
            except Exception as exc:
                _log.warning(
                    "refresh bilibili credential state failed: %s",
                    redact_text(exc),
                )
            return {
                'success': True,
                **status,
                'credential_status': credential_status or 'INVALID',
            }
        except (ValueError, json.JSONDecodeError) as exc:
            return {'success': False, 'error': redact_text(exc)}
        except OSError as exc:
            _log.error("保存 B站 Cookie 失败: %s", redact_text(exc))
            return {'success': False, 'error': humanize_error(str(exc))}

    @server.on('get_huya_cookie_status')
    async def handle_get_huya_cookie_status(data):
        """查询虎牙 Cookie 是否已配置。"""
        from lsc.platforms.cookie_helper import get_huya_cookie_status
        try:
            return {'success': True, **get_huya_cookie_status()}
        except Exception as exc:
            _log.warning("get_huya_cookie_status failed: %s", redact_text(exc))
            return {'success': False, 'error': redact_text(exc), 'configured': False, 'count': 0}

    @server.on('save_huya_cookies')
    async def handle_save_huya_cookies(data):
        """保存用户粘贴的虎牙 Cookie（JSON / Cookie 头）。"""
        from lsc.platforms.cookie_helper import save_huya_cookies_from_text
        raw = ''
        if isinstance(data, dict):
            raw = str(data.get('cookies') or data.get('text') or '')
        if not raw.strip():
            return {'success': False, 'error': '请粘贴 Cookie 内容'}
        _MAX_COOKIE_BYTES = 1 * 1024 * 1024  # 1 MB
        if len(raw) > _MAX_COOKIE_BYTES:
            _log.warning("虎牙 Cookie 输入过大: %d bytes (limit %d)", len(raw), _MAX_COOKIE_BYTES)
            return {'success': False, 'error': f'Cookie 内容过大（{len(raw)} 字节），请检查输入'}
        try:
            status = save_huya_cookies_from_text(raw)
            _log.info("虎牙 Cookie 已保存: count=%s", status.get('count'))
            credential_status = ''
            try:
                from lsc.platforms.credentials import get_default_credential_provider

                credential_status = get_default_credential_provider().refresh(
                    'huya'
                ).status.value
            except Exception as exc:
                _log.warning(
                    "refresh huya credential state failed: %s",
                    redact_text(exc),
                )
            return {
                'success': True,
                **status,
                'credential_status': credential_status or 'INVALID',
            }
        except (ValueError, json.JSONDecodeError) as exc:
            return {'success': False, 'error': redact_text(exc)}
        except OSError as exc:
            _log.error("保存虎牙 Cookie 失败: %s", redact_text(exc))
            return {'success': False, 'error': humanize_error(str(exc))}

    # ⚠️ 死代码：本 handler 与 align_preview_audio 已被 alignment_handlers.py 后注册覆盖。
    # 改动此处无效，须改 alignment_handlers.py（register_room_handlers 按顺序注册，
    # server.on 后注册覆写前者）。保留仅供比对，勿改。
    @server.on('set_content_offset')
    async def handle_set_content_offset(data):
        """设置房间的音频互相关内容偏移量（由前端音频对齐后回传）。"""
        room_id = data.get('room_id')
        offset = float(data.get('offset', 0.0))
        if not room_id:
            return {'error': 'room_id is required'}
        _log.info("设置 content_offset: room_id=%s, offset=%.4fs", room_id, offset)
        def _set():
            room = manager.get_room(room_id)
            if room is not None:
                room.content_offset = offset
        await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.manager.call(_set))
        return {'success': True}

    @server.on('align_preview_audio')
    async def handle_align_preview_audio(data):
        """多房间预览音频互相关对齐（同步，基于前端发送的 PCM 数据）。

        前端通过 Web Audio API 从 <video> 元素捕获音频 PCM，base64 编码后
        发送到后端。后端解码后直接运行互相关计算，返回偏移量。

        参数:
            data: 需包含 rooms 列表，每项包含 room_id, sample_rate, pcm_base64。

        返回:
            {'success': True, 'offsets': {...}, 'reference_room_id': '...', 'scores': {...}}
            或 {'success': False, 'error': '错误信息'}
        """
        rooms_data = data.get('rooms', [])
        _align_log = logging.getLogger('lsc.align')
        _align_log.info("收到预览音频对齐请求: rooms=%d", len(rooms_data))
        if len(rooms_data) < 2:
            _align_log.warning("预览音频对齐请求房间数不足: %d", len(rooms_data))
            return {'success': False, 'error': '至少需要 2 个房间'}
        # 限制房间数与单路 PCM 大小，防止超大 payload 导致 OOM
        _MAX_ALIGN_ROOMS = 64
        _MAX_PCM_BASE64_BYTES = 20 * 1024 * 1024  # 20 MB per room
        if len(rooms_data) > _MAX_ALIGN_ROOMS:
            _align_log.warning("预览音频对齐房间数过多: %d (limit %d)", len(rooms_data), _MAX_ALIGN_ROOMS)
            return {'success': False, 'error': f'房间数过多（{len(rooms_data)}），上限 {_MAX_ALIGN_ROOMS}'}
        try:
            import base64

            from lsc.editor.audio_aligner import align_audio_map

            # 解码 PCM 数据
            audio_map: dict[str, np.ndarray] = {}
            for rd in rooms_data:
                room_id = rd.get('room_id', '')
                sample_rate = int(rd.get('sample_rate', 16000))
                pcm_b64 = rd.get('pcm_base64', '')
                diagnostics = rd.get('diagnostics') or {}
                _align_log.info(
                    "预览音频诊断: room_id=%s, current_time=%s, buffer=%s-%s, ingest_mode=%s, "
                    "ready_state=%s, has_audio_track=%s, rms=%s, sample_count=%s, capture_reason=%s",
                    room_id,
                    diagnostics.get('current_time'),
                    diagnostics.get('buffer_start'),
                    diagnostics.get('buffer_end'),
                    diagnostics.get('ingest_mode'),
                    diagnostics.get('ready_state'),
                    diagnostics.get('has_audio_track'),
                    diagnostics.get('rms'),
                    diagnostics.get('sample_count'),
                    diagnostics.get('capture_reason'),
                )
                if not room_id or not pcm_b64:
                    _align_log.warning("预览音频对齐跳过: room_id=%s, 缺少数据", room_id)
                    continue
                # 限制单路 PCM 大小，防止超大 base64 解码导致 OOM
                if len(pcm_b64) > _MAX_PCM_BASE64_BYTES:
                    _align_log.warning("预览音频对齐跳过: room_id=%s, PCM 过大=%d bytes (limit %d)",
                                       room_id, len(pcm_b64), _MAX_PCM_BASE64_BYTES)
                    continue
                try:
                    raw = base64.b64decode(pcm_b64)
                    samples = np.frombuffer(raw, dtype=np.float32)
                    if samples.size < sample_rate:  # 至少1秒
                        _align_log.warning("预览音频对齐跳过: room_id=%s, 样本过少=%d", room_id, samples.size)
                        continue
                    audio_map[room_id] = samples
                    _align_log.info("解码预览音频: room_id=%s, samples=%d (%.2fs), rate=%d",
                                    room_id, samples.size, samples.size / sample_rate, sample_rate)
                except Exception as exc:
                    _align_log.warning("预览音频解码失败: room_id=%s, error=%s", room_id, exc)

            valid_ids = list(audio_map.keys())
            if len(valid_ids) < 2:
                _align_log.warning("有效预览音频不足 2 路: %s", valid_ids)
                return {'success': False, 'error': '有效音频不足 2 路，无法互相关对齐'}

            # 互相关 FFT 可能阻塞数百 ms～数秒，卸载到线程池避免堵死 WS 事件循环
            result = await asyncio.get_running_loop().run_in_executor(
                _recording_executor,
                lambda: align_audio_map(audio_map, sample_rate, method='preview_audio'),
            )
            if not result.success:
                def _clear_on_align_fail():
                    for rid in audio_map:
                        room = manager.get_room(rid)
                        if room is None:
                            continue
                        room.align_group_id = ''
                    return True

                try:
                    await asyncio.get_running_loop().run_in_executor(
                        _bridge_executor, lambda: bridge.manager.call(_clear_on_align_fail)
                    )
                    _broadcast_rooms(force=True)
                except Exception as exc:
                    _align_log.warning("对齐失败后清除对齐组失败: %s", exc)
                return {'success': False, 'error': result.error, 'precision': 'buffer_only'}

            _align_log.info(
                "预览音频对齐完成: reference=%s, offsets=%s, scores=%s",
                result.reference_room_id,
                {k: f"{v:.4f}" for k, v in result.offsets.items()},
                {k: f"{v:.3f}" for k, v in result.correlation_scores.items()},
            )

            # 仅对置信度 ≥ 0.3 的房间写入 offset/group；可信不足 2 路则不建组
            _ALIGN_TRUST_THRESHOLD = 0.3
            offsets = result.offsets
            scores = result.correlation_scores
            trusted = {
                rid: float(offset)
                for rid, offset in offsets.items()
                if float(scores.get(rid, 0.0) or 0.0) >= _ALIGN_TRUST_THRESHOLD
            }
            if len(trusted) < 2:
                _align_log.warning(
                    "可信对齐房间不足 %d/2，清除 align_group_id: trusted=%s",
                    len(trusted),
                    list(trusted.keys()),
                )

                def _clear_stale_align_groups():
                    for rid in offsets:
                        room = manager.get_room(rid)
                        if room is None:
                            continue
                        room.content_offset = 0.0
                        room.align_group_id = ''
                    return True

                try:
                    await asyncio.get_running_loop().run_in_executor(
                        _bridge_executor, lambda: bridge.manager.call(_clear_stale_align_groups)
                    )
                    _broadcast_rooms(force=True)
                except Exception as exc:
                    _align_log.warning("清除对齐组失败: %s", exc)
                return {
                    'success': False,
                    'error': '可信对齐不足，无法建立对齐组',
                    'offsets': result.offsets,
                    'reference_room_id': result.reference_room_id,
                    'method': result.method,
                    'scores': result.correlation_scores,
                    'precision': 'buffer_only',
                }

            group_id = f"align_{int(time.time())}"
            reference_room_id = result.reference_room_id

            def _apply_alignment_and_create_timeline():
                timeline_svc = get_timeline_service()
                # 重新对齐前先失效旧 timeline
                seen_tids: set[str] = set()
                for rid in trusted:
                    old = timeline_svc.get_active_timeline_for_room(rid)
                    if old is not None and old.timeline_id not in seen_tids:
                        seen_tids.add(old.timeline_id)
                        timeline_svc.invalidate_timeline(
                            old.timeline_id, f"realign:{group_id}",
                        )

                room_meta: dict[str, dict] = {}
                for rid, offset in offsets.items():
                    room = manager.get_room(rid)
                    if room is None:
                        continue
                    score = float(scores.get(rid, 0.0) or 0.0)
                    if score < _ALIGN_TRUST_THRESHOLD:
                        room.content_offset = 0.0
                        room.align_group_id = ''
                        continue
                    room.content_offset = float(offset)
                    room.align_group_id = group_id
                    media_start = (
                        getattr(room, 'recording_media_start_mono', None)
                        or getattr(room, 'recording_start_mono', None)
                        or 0.0
                    )
                    room_meta[rid] = {
                        'preview_epoch_id': getattr(room, 'preview_epoch_id', '') or '',
                        'recording_id': getattr(room, 'recording_id', '') or '',
                        'media_start_mono': float(media_start or 0.0),
                    }

                snapshots = build_room_snapshots_from_align(
                    reference_room_id,
                    offsets=trusted,
                    scores=scores,
                    room_meta=room_meta,
                    confidence_threshold=_ALIGN_TRUST_THRESHOLD,
                )
                if len(snapshots) < 2:
                    _align_log.warning(
                        "对齐快照不足 2 路，跳过 create_timeline: %s",
                        list(snapshots.keys()),
                    )
                    return None
                return timeline_svc.create_timeline(
                    reference_room_id,
                    snapshots,
                    required_room_ids=list(trusted.keys()),
                )

            timeline_payload = None
            try:
                ctx = await asyncio.get_running_loop().run_in_executor(
                    _bridge_executor, lambda: bridge.manager.call(_apply_alignment_and_create_timeline)
                )
                if ctx is not None:
                    timeline_payload = timeline_to_dict(ctx)
                    bridge.queue_broadcast({
                        'type': 'timeline_ready',
                        'data': {'timeline': timeline_payload},
                    })
                else:
                    _align_log.warning(
                        "create_timeline 未创建（offset 已保留）: reference=%s trusted=%s",
                        reference_room_id, list(trusted.keys()),
                    )

                    def _clear_on_timeline_fail():
                        for rid in trusted:
                            room = manager.get_room(rid)
                            if room is None:
                                continue
                            room.align_group_id = ''
                            room.content_offset = 0.0
                        return True

                    try:
                        await asyncio.get_running_loop().run_in_executor(
                            _bridge_executor, lambda: bridge.manager.call(_clear_on_timeline_fail)
                        )
                        _broadcast_rooms(force=True)
                    except Exception as clear_exc:
                        _align_log.warning("公共时间轴创建失败后清除对齐组失败: %s", clear_exc)
                    return {
                        'success': False,
                        'error': '公共时间轴创建失败',
                        'offsets': result.offsets,
                        'reference_room_id': result.reference_room_id,
                        'method': result.method,
                        'scores': result.correlation_scores,
                        'precision': 'buffer_only',
                    }
            except Exception as exc:
                _align_log.warning("写入对齐组/创建 TimelineContext 失败: %s", exc)
                return {
                    'success': False,
                    'error': '公共时间轴创建失败',
                    'detail': str(exc),
                    'offsets': result.offsets,
                    'reference_room_id': result.reference_room_id,
                    'method': result.method,
                    'scores': result.correlation_scores,
                    'precision': 'buffer_only',
                }

            response = {
                'success': True,
                'offsets': result.offsets,
                'reference_room_id': result.reference_room_id,
                'method': result.method,
                'scores': result.correlation_scores,
                'align_group_id': group_id,
            }
            if timeline_payload is not None:
                response['timeline'] = timeline_payload
            return response
        except Exception as exc:
            _align_log.error("预览音频对齐失败: %s", exc, exc_info=True)
            return {'success': False, 'error': str(exc)}

    _DEPS_CHECK_CACHE_TTL = 30.0
    _deps_cache: dict[str, Any] = {'data': None, 'at': 0.0}

    @server.on('check_dependencies')
    async def handle_check_dependencies(data):
        """检测系统依赖状态：FFmpeg / FFprobe / NVENC / Python"""
        from lsc.core.services.mse_streamer import _check_nvenc
        from lsc.utils.process_launcher import prepare_launch as _prepare_launch

        # 依赖在 30s 内不会变化：SplashScreen 与 Settings 打开各发一次，
        # 命中缓存避免重复跑 ffmpeg/ffprobe -version 子进程（各 5s 超时窗口）。
        if _deps_cache['data'] is not None and time.time() - _deps_cache['at'] < _DEPS_CHECK_CACHE_TTL:
            return _deps_cache['data']

        cfg = load_config()
        _log.info("检测依赖: ffmpeg=%s, ffprobe=%s, nvenc=%s",
                  cfg.ffmpeg_path or shutil.which("ffmpeg"),
                  cfg.ffprobe_path or shutil.which("ffprobe"),
                  _check_nvenc() if (cfg.ffmpeg_path or shutil.which("ffmpeg")) else False)
        results = {}

        # FFmpeg
        ffmpeg_path = cfg.ffmpeg_path or shutil.which("ffmpeg") or ""
        ffmpeg_ok = bool(ffmpeg_path) and os.path.isfile(ffmpeg_path)
        ffmpeg_version = ""
        if ffmpeg_ok:
            try:
                env, cflags, cwd = _prepare_launch(ffmpeg_path)
                r = run_hidden(
                    [ffmpeg_path, "-version"],
                    capture_output=True, text=True, timeout=5,
                    env=env, cwd=cwd,
                    **({"creationflags": cflags} if cflags else {}),
                )
                if r.returncode == 0:
                    ffmpeg_version = r.stdout.split('\n')[0].strip()
            except Exception as exc:
                _log.debug("检测 FFmpeg 版本失败: %s", exc)
        results['ffmpeg'] = {'available': ffmpeg_ok, 'path': ffmpeg_path, 'version': ffmpeg_version}

        # FFprobe
        ffprobe_path = cfg.ffprobe_path or shutil.which("ffprobe") or ""
        ffprobe_ok = bool(ffprobe_path) and os.path.isfile(ffprobe_path)
        ffprobe_version = ""
        if ffprobe_ok:
            try:
                env, cflags, cwd = _prepare_launch(ffprobe_path)
                r = run_hidden(
                    [ffprobe_path, "-version"],
                    capture_output=True, text=True, timeout=5,
                    env=env, cwd=cwd,
                    **({"creationflags": cflags} if cflags else {}),
                )
                if r.returncode == 0:
                    ffprobe_version = r.stdout.split('\n')[0].strip()
            except Exception as exc:
                _log.debug("检测 FFprobe 版本失败: %s", exc)
        results['ffprobe'] = {'available': ffprobe_ok, 'path': ffprobe_path, 'version': ffprobe_version}

        # NVENC
        nvenc_ok = _check_nvenc() if ffmpeg_ok else False
        results['nvenc'] = {'available': nvenc_ok, 'path': '', 'version': 'h264_nvenc' if nvenc_ok else ''}

        # Python
        py_version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        results['python'] = {'available': True, 'path': sys.executable, 'version': py_version}

        result = {'success': True, 'dependencies': results}
        _deps_cache['data'] = result
        _deps_cache['at'] = time.time()
        return result

    @server.on('render_clip_preview')
    async def handle_render_clip_preview(data):
        """从录制文件抽入点/中点/出点三帧，供导出前画面预览。"""
        import base64

        room_id = data.get('room_id')
        try:
            start = float(data.get('start', 0.0))
            end = float(data.get('end', 0.0))
        except (TypeError, ValueError):
            return {'success': False, 'error': '时间参数无效'}
        if not room_id or not (end > start):
            return {'success': False, 'error': '参数无效'}
        room = manager.get_room(room_id)
        if room is None:
            return {'success': False, 'error': '房间不存在'}
        path = getattr(room, 'record_output_path', '')
        if not path or not os.path.isfile(path):
            return {'success': False, 'error': '该房间没有录制文件'}

        def _render() -> list[str | None]:
            import cv2

            from lsc.analyzer.valorant_ocr_rounds import extract_frames_cancellable
            cfg = load_config()
            ffmpeg_path = cfg.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
            frames: list[str | None] = []
            for pt in (start, (start + end) / 2.0, end):
                t0 = max(0.0, pt - 0.4)
                got = extract_frames_cancellable(
                    video_path=path,
                    start_sec=t0,
                    end_sec=t0 + 0.8,
                    fps=1,
                    ffmpeg_path=ffmpeg_path,
                    overlap_sec=0.0,
                )
                if not got:
                    frames.append(None)
                    continue
                ok, buf = cv2.imencode(
                    '.jpg', got[0][1], [int(cv2.IMWRITE_JPEG_QUALITY), 70]
                )
                frames.append(base64.b64encode(buf.tobytes()).decode('ascii') if ok else None)
            return frames

        frames = await asyncio.get_running_loop().run_in_executor(
            _recording_executor, _render
        )
        return {'success': True, 'frames': frames, 'start': start, 'end': end}

    @server.on('cancel_export')
    async def handle_cancel_export(data):
        """取消导出任务 — 支持取消排队中和进行中的任务。"""
        job_id = data.get('job_id', '')
        if not job_id:
            return {'success': False, 'error': 'job_id is required'}

        # 情况 1：任务正在执行（已经注册了 clip_id）
        with _export_jobs_lock:
            clip_id = export_jobs.get(job_id)
        if clip_id:
            _log.info("取消导出(执行中): job_id=%s, clip_id=%s", job_id, clip_id)
            def _cancel():
                return manager.cancel_export(clip_id)
            try:
                cancelled = await asyncio.get_running_loop().run_in_executor(
                    _bridge_executor, lambda: bridge.manager.call(_cancel)
                )
            except Exception as exc:
                _log.error("取消导出异常: job_id=%s, error=%s", job_id, exc)
                return {'success': False, 'error': humanize_error(str(exc))}
            if cancelled:
                with _export_jobs_lock:
                    export_jobs.pop(job_id, None)
                _set_export_job_state(job_id, 'cancelled', error='导出已取消')
                _log.info("导出已取消: job_id=%s", job_id)
                return {'success': True}
            return {'success': False, 'error': 'job not found'}

        # 情况 2：任务还在排队中（尚未注册 clip_id）— 标记为取消
        _export_cancelled_jobs.add(job_id)
        _set_export_job_state(job_id, 'cancelled', error='导出已取消')
        _log.info("取消导出(排队中): job_id=%s", job_id)
        return {'success': True, 'note': 'queued job marked as cancelled'}

    @server.on('get_export_job_status')
    async def handle_get_export_job_status(data):
        """返回导出任务快照，供前端补偿丢失的进度/完成广播。"""
        raw_ids = (data or {}).get('job_ids') or []
        if not isinstance(raw_ids, list):
            return {'success': False, 'error': 'job_ids must be a list', 'jobs': []}
        job_ids = [str(job_id) for job_id in raw_ids[:100] if str(job_id)]
        return {'success': True, 'jobs': _get_export_job_states(job_ids)}

    @server.on('enable_preview')
    async def handle_enable_preview(data):
        """开启/关闭房间预览（支持 qt / electron / mse 模式）。"""
        room_id = data.get('room_id')
        enabled = bool(data.get('enabled', True))
        mode = data.get('mode', 'qt')  # 'qt' | 'electron' | 'mse'
        if not room_id:
            return {'error': 'room_id is required'}
        _log.info("预览切换: room_id=%s, enabled=%s, mode=%s", room_id, enabled, mode)

        # MSE 模式：在 handler 层管理 MseStreamer（需要 WebSocket 推送）
        if mode == 'mse':
            return await _handle_mse_preview(server, manager, room_id, enabled, data)

        def _preview():
            if enabled:
                return manager.start_preview(room_id, mode=mode)  # type: ignore[call-arg]
            manager.stop_preview(room_id)
            return True

        success = await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.manager.call(_preview))
        _broadcast_rooms()
        return {'success': bool(success)}

    # MSE preview handler
    async def _handle_mse_preview(srv, mgr, room_id: str, enabled: bool, data: dict | None = None, *, force_restart: bool = False) -> dict[str, Any]:
        """Handle MSE (Media Source Extensions) preview mode.

        Creates an MseStreamer that transcodes the live stream to fragmented MP4
        and pushes segments via WebSocket for browser-native <video> playback.
        """
        if enabled:
            # Check if already streaming / starting
            existing = _preview_stream_registry().get(room_id)
            if existing is not None and existing.is_running:
                    # Streamer 仍在运行：设置 preview_enabled=True 并重发 init 段
                    _log.info("预览已在运行: room_id=%s, 重发 init 段", room_id)
                    def _set_preview_on():
                        room = mgr.get_room(room_id)
                        if room is not None:
                            room.preview_enabled = True
                        return True
                    try:
                        await asyncio.get_running_loop().run_in_executor(
                            _bridge_executor, lambda: bridge.manager.call(_set_preview_on)
                        )
                    except Exception as exc:
                        _log.error("设置 preview_enabled 失败: room_id=%s, error=%s", room_id, exc)
                    existing.replay_init()
                    _broadcast_rooms()
                    return _mse_preview_success_response(
                        room_id, data, note='already streaming, init replayed',
                    )
            # §7.3.3 硬上限：预览最多 4 路（MSE 为前端唯一预览路径，原上限检查仅存于
            # 已弃用的 mpv 路径 orchestrator.start_preview）。已在运行的重发 init 提前
            # return 不占新进程；此处仅拦截「新启动」。
            max_previews = _get_configured_max_previews()
            if _preview_stream_registry().active_count() >= max_previews:
                _log.warning(
                    "预览数已达上限 (%d)，拒绝启动 MSE 预览: room_id=%s",
                    max_previews, room_id,
                )
                return {
                    'success': False,
                    'room_id': room_id,
                    'error': f'预览数已达上限 ({max_previews})，请先关闭其它预览',
                }
            shared_enabled = _shared_ingest_v2_enabled(mgr, room_id)
            if shared_enabled:
                shared_ingest = _shared_ingests.get(room_id)
                if (
                    shared_ingest is not None
                    and getattr(shared_ingest, 'recording_active', False)
                    and not getattr(shared_ingest, 'is_stopped', True)
                ):
                    loop = asyncio.get_running_loop()
                    shared_handle = None
                    if shared_ingest is None:  # 类型收窄守卫（替代 assert，-O 模式不失效）
                        return {'success': False, 'error': '共享进样实例不可用'}
                    try:
                        if force_restart:
                            # 停止旧预览 FFmpeg 以应用新画质参数
                            def _stop_preview_sink():
                                try:
                                    shared_ingest.stop_preview_sink()  # type: ignore[union-attr]
                                except Exception as exc:
                                    _log.debug("stop_preview_sink 失败: %s", exc)
                            await asyncio.get_running_loop().run_in_executor(_bridge_executor, _stop_preview_sink)

                        _bind_shared_ingest_lease(mgr, room_id, shared_ingest)
                        _configure_shared_preview_quality(shared_ingest, data)
                        shared_handle = _attach_shared_preview_handle(
                            room_id, shared_ingest, loop,
                        )

                        if force_restart:
                            # 启动新预览 FFmpeg（subscriber 已存在，新参数已配置）
                            def _start_preview_sink():
                                preview_params = _compute_preview_quality_params(data)
                                valid_keys = {'width', 'height', 'use_nvenc', 'video_bitrate', 'crf_value', 'fps'}
                                filtered = {k: v for k, v in preview_params.items() if k in valid_keys}
                                shared_ingest.configure_preview(**filtered)  # type: ignore[union-attr]
                                return shared_ingest.start_preview(**filtered)  # type: ignore[union-attr]
                            try:
                                result = await asyncio.get_running_loop().run_in_executor(_bridge_executor, _start_preview_sink)
                                if not (getattr(result, 'accepted', False) or getattr(result, 'ok', False)):
                                    raise RuntimeError(
                                        getattr(result, 'error', '') or 'shared preview restart failed'
                                    )
                            except Exception as exc:
                                raise RuntimeError(redact_text(exc)) from exc

                        shared_handle.replay_init()

                        def _set_shared_preview_on():
                            room = mgr.get_room(room_id)
                            if room is not None:
                                new_epoch = uuid4().hex
                                room.preview_enabled = True
                                room.preview_epoch_id = new_epoch
                                get_timeline_service().on_preview_epoch_change(room_id, new_epoch)
                            return True

                        await asyncio.get_running_loop().run_in_executor(
                            _bridge_executor, lambda: bridge.manager.call(_set_shared_preview_on)
                        )
                        _note_mse_reconnect_event(room_id, "accepted")
                        _broadcast_rooms()
                        return _mse_preview_success_response(
                            room_id, data, note='shared ingest preview attached',
                        )
                    except Exception as exc:
                        _log.warning(
                            "shared ingest preview attach failed: room_id=%s, error=%s",
                            room_id,
                            exc,
                        )
                        if shared_handle is not None:
                            try:
                                shared_handle.stop()
                            except Exception as stop_exc:
                                _log.debug("shared preview cleanup failed: %s", stop_exc)
                if shared_ingest is None or not getattr(shared_ingest, 'recording_active', False):
                    loop = asyncio.get_running_loop()
                    shared_handle = None
                    try:
                        # 提前定义，供首次启动和 code=0 重试共用
                        settings = load_settings()
                        preview_quality = (data or {}).get('preview_quality') or settings.get('preview_quality', '高清')

                        def _read_shared_snapshot():
                            room = mgr.get_room(room_id)
                            if room is None:
                                return None
                            return {
                                'is_connected': room.is_connected,
                                'stream_url': room.stream_info.stream_url if room.stream_info else '',
                                'headers': (room.stream_info.headers if room.stream_info else None) or {},
                                'quality_urls': (room.stream_info.quality_urls if room.stream_info else {}),
                            }

                        if shared_ingest is None or getattr(shared_ingest, 'is_stopped', True):
                            refresh_ok = await asyncio.get_running_loop().run_in_executor(
                                _recording_executor, lambda: mgr.refresh_stream_url(room_id, force=force_restart)
                            )
                            if not refresh_ok:
                                raise RuntimeError("stream url refresh failed")

                            snapshot = await asyncio.get_running_loop().run_in_executor(
                                _bridge_executor, lambda: bridge.manager.call(_read_shared_snapshot)
                            )
                            if snapshot is None or not snapshot['is_connected'] or not snapshot['stream_url']:
                                raise RuntimeError("room is not connected or has no stream url")

                            stream_url = snapshot['stream_url']
                            quality_urls = snapshot.get('quality_urls') or {}
                            if quality_urls:
                                selected_url, _selected_key = select_quality(
                                    {'qualityUrls': quality_urls, 'streamUrl': stream_url, 'selectedQuality': ''},
                                    preview_quality,
                                )
                                if selected_url:
                                    stream_url = selected_url
                            shared_ingest = _shared_ingests.get_or_create(
                                room_id,
                                url=stream_url,
                                headers=snapshot.get('headers') or {},
                            )
                            _bind_shared_ingest_lease(mgr, room_id, shared_ingest)

                        _bind_shared_ingest_lease(mgr, room_id, shared_ingest)
                        upstream_dead = (
                            getattr(shared_ingest, 'process_id', None) is None
                            or getattr(shared_ingest, 'is_stopped', True)
                            or (
                                callable(getattr(shared_ingest, 'upstream_is_live', None))
                                and not shared_ingest.upstream_is_live()
                            )
                        )
                        if upstream_dead:
                            preview_params = _compute_preview_quality_params(data)
                            # 过滤掉不接受的参数
                            valid_keys = {'width', 'height', 'use_nvenc', 'video_bitrate', 'crf_value', 'fps'}
                            filtered = {k: v for k, v in preview_params.items() if k in valid_keys}
                            shared_ingest.configure_preview(**filtered)
                            result = shared_ingest.start_preview(**filtered)
                            if not (getattr(result, 'accepted', False) or getattr(result, 'ok', False)):
                                first_error = getattr(result, 'error', '') or "shared preview start failed"
                                # code=0 表示 CDN 返回空响应（虎牙反爬签名过期），强制刷新流地址后重试一次
                                if 'code=0' in first_error:
                                    _log.info(
                                        "shared preview code=0, force-refreshing stream URL: room_id=%s",
                                        room_id,
                                    )
                                    _stop_idle_shared_ingest(room_id, reason="code=0 retry")
                                    retry_refresh = await asyncio.get_running_loop().run_in_executor(
                                        _recording_executor, lambda: mgr.refresh_stream_url(room_id, force=True)
                                    )
                                    if retry_refresh:
                                        retry_snapshot = await asyncio.get_running_loop().run_in_executor(
                                            _bridge_executor, lambda: bridge.manager.call(_read_shared_snapshot)
                                        )
                                        if retry_snapshot and retry_snapshot.get('stream_url'):
                                            retry_url = retry_snapshot['stream_url']
                                            retry_quality_urls = retry_snapshot.get('quality_urls') or {}
                                            if retry_quality_urls:
                                                sel_url, _ = select_quality(
                                                    {'qualityUrls': retry_quality_urls, 'streamUrl': retry_url, 'selectedQuality': ''},
                                                    preview_quality,
                                                )
                                                if sel_url:
                                                    retry_url = sel_url
                                            shared_ingest = _shared_ingests.get_or_create(
                                                room_id,
                                                url=retry_url,
                                                headers=retry_snapshot.get('headers') or {},
                                            )
                                            _bind_shared_ingest_lease(mgr, room_id, shared_ingest)
                                            shared_ingest.configure_preview(**filtered)
                                            result = shared_ingest.start_preview(**filtered)
                                if not (getattr(result, 'accepted', False) or getattr(result, 'ok', False)):
                                    raise RuntimeError(getattr(result, 'error', '') or first_error)

                        _configure_shared_preview_quality(shared_ingest, data)
                        shared_handle = _attach_shared_preview_handle(room_id, shared_ingest, loop)
                        shared_handle.replay_init()

                        def _set_shared_preview_on():
                            room = mgr.get_room(room_id)
                            if room is not None:
                                new_epoch = uuid4().hex
                                room.preview_enabled = True
                                room.preview_epoch_id = new_epoch
                                get_timeline_service().on_preview_epoch_change(room_id, new_epoch)
                            return True

                        await asyncio.get_running_loop().run_in_executor(
                            _bridge_executor, lambda: bridge.manager.call(_set_shared_preview_on)
                        )
                        _note_mse_reconnect_event(room_id, "accepted")
                        _broadcast_rooms()
                        return _mse_preview_success_response(
                            room_id, data, note='shared ingest preview-only started',
                        )
                    except Exception as exc:
                        _log.warning(
                            "shared ingest preview-only failed: room_id=%s, error=%s",
                            room_id,
                            exc,
                        )
                        if shared_handle is not None:
                            try:
                                shared_handle.stop()
                            except Exception as stop_exc:
                                _log.debug("shared preview-only handle cleanup failed: %s", stop_exc)
                        if shared_ingest is not None and not getattr(shared_ingest, 'recording_active', False):
                            _stop_idle_shared_ingest(room_id, reason="shared preview-only failed")
                        return {'success': False, 'room_id': room_id, 'error': f'共享预览启动失败：{redact_text(exc)}'}
                if shared_enabled:
                    return {'success': False, 'room_id': room_id, 'error': '共享预览启动失败，请检查直播流状态'}
            with _mse_starting_lock:
                    if room_id in _mse_starting:
                        return {'success': False, 'room_id': room_id, 'error': 'MSE 正在启动中，请稍候'}
                    _mse_starting.add(room_id)

            try:
                # 先在后台线程刷新流 URL，避免阻塞编排线程（B站等平台耗时 10+ 秒）
                # force=False：连接后 120s 内复用房间流缓存，显著加快预览启动
                await srv.broadcast('preview_phase', {'room_id': room_id, 'phase': 'refreshing_url'})
                # 单连接签名平台预览挂同一条上游，禁止为“独立签名”强刷页面。
                # 能力字段 preview_refresh_when_recording 为 False 时保持 force=False。
                force_refresh = False
                def _peek_room_state():
                    r = mgr.get_room(room_id)
                    if r is None or r.stream_info is None:
                        return False
                    from lsc.platforms.recovery_policy import should_force_refresh_when_recording

                    return bool(
                        r.is_recording
                        and should_force_refresh_when_recording(r.stream_info)
                    )
                try:
                    force_refresh = await asyncio.get_running_loop().run_in_executor(
                        _bridge_executor, lambda: bridge.manager.call(_peek_room_state)
                    )
                except Exception as exc:
                    _log.debug("预览 peek 房间状态失败: %s", exc)
                refresh_ok = await asyncio.get_running_loop().run_in_executor(
                    _recording_executor, lambda: mgr.refresh_stream_url(room_id, force=force_refresh)
                )
                if not refresh_ok:
                    # 仅在确实没有可用流时才断开；保留缓存，避免误报「房间未连接」
                    def _mark_disconnected_if_no_stream():
                        room = mgr.get_room(room_id)
                        if room is None:
                            return
                        has_url = bool(
                            (room.stream_info and room.stream_info.stream_url)
                            or room.stream_url_cached
                            or (room.controller and getattr(room.controller, "stream_url", ""))
                        )
                        if has_url:
                            _log.warning(
                                "preview refresh failed but keep connection for %s (cached stream present)",
                                room_id,
                            )
                            return
                        room.is_connected = False
                        room.stream_info = None
                    await asyncio.get_running_loop().run_in_executor(
                        _bridge_executor, lambda: bridge.manager.call(_mark_disconnected_if_no_stream)
                    )

                # 在编排线程读取刷新后的房间状态
                def _read_snapshot():
                    room = mgr.get_room(room_id)
                    if room is None:
                        return None
                    stream_url = ''
                    if room.stream_info and room.stream_info.stream_url:
                        stream_url = room.stream_info.stream_url
                    elif room.stream_url_cached:
                        stream_url = room.stream_url_cached
                    return {
                        'is_connected': room.is_connected or bool(stream_url),
                        'stream_url': stream_url,
                        'platform': room.platform,
                        'headers': (room.stream_info.headers if room.stream_info else None) or {},
                        'quality_urls': (room.stream_info.quality_urls if room.stream_info else {}),
                    }

                snapshot = await asyncio.get_running_loop().run_in_executor(
                    _bridge_executor, lambda: bridge.manager.call(_read_snapshot)
                )
                if snapshot is None:
                    await srv.broadcast('preview_phase', {'room_id': room_id, 'phase': 'error'})
                    return {'success': False, 'room_id': room_id, 'error': '房间不存在'}
                if not snapshot['is_connected'] or not snapshot['stream_url']:
                    await srv.broadcast('preview_phase', {'room_id': room_id, 'phase': 'error'})
                    return {'success': False, 'room_id': room_id, 'error': '房间未连接或无流信息（直播可能已结束）'}

                await srv.broadcast('preview_phase', {'room_id': room_id, 'phase': 'probing'})
                stream_url = snapshot['stream_url']

                # 读取预览画质预设（优先消息传入的 preview_quality，回退到全局设置）
                settings = load_settings()
                preview_quality = data.get('preview_quality') or settings.get('preview_quality', '高清')  # type: ignore[union-attr]

                platform = snapshot.get('platform', '')
                from lsc.platforms.capabilities import get_platform_capabilities

                # 根据用户选择的预览画质，从 quality_urls 中挑选对应画质的流地址
                quality_urls = snapshot.get('quality_urls') or {}
                # B站无登录态时高画质（qn=250/300）易被 CDN 403 拒绝，与适配器
                # "无 Cookie 避高 qn" 策略冲突；直接复用录制/连接已验证的流地址。
                if get_platform_capabilities(platform).anonymous_quality_fallback:
                    try:
                        from lsc.platforms.credentials import has_usable_credentials

                        if not has_usable_credentials(platform):
                            quality_urls = {}
                            _log.info("当前平台无可用凭据，预览复用已连接流地址（跳过画质重选）")
                    except Exception as exc:
                        _log.debug("检查平台凭据失败，沿用原选流逻辑: %s", exc)
                if quality_urls:
                    selected_url, selected_key = select_quality(
                        {'qualityUrls': quality_urls, 'streamUrl': stream_url, 'selectedQuality': ''},
                        preview_quality,
                    )
                    if selected_url:
                        stream_url = selected_url
                        _log.info("预览画质选择: preset=%s, quality_key=%s, url=%s", preview_quality, selected_key, redact_url(stream_url)[:80])
                # B站/虎牙等平台首次刷新 URL 耗时较长，延长 FFmpeg 启动探测超时
                probe_timeout = get_platform_capabilities(platform).probe_timeout_sec

                preview_params = _compute_preview_quality_params(data)
                width = preview_params['width']
                height = preview_params['height']
                target_fps = preview_params.get('fps', 0)

                # 提取流 headers（B站/虎牙/斗鱼 CDN 强制检查 Referer）
                preview_headers = snapshot.get('headers') or {}
                video_bitrate = preview_params['video_bitrate']
                crf_value = preview_params['crf_value']

                loop = asyncio.get_running_loop()

                async def _on_mse_error(room_id: str, err: str, loop):
                    """MSE 流错误处理：尝试自动重连，超限后清理预览状态。

                    使用 while 循环替代递归，避免异步递归控制流难以推理的问题。
                    on_error 回调仍可调用本函数，但首次进入即开始循环，不形成递归链。
                    """
                    _log.info("MSE error for room %s: %s", room_id, err)

                    # 启动期忽略：与 shared 版一致，_handle_mse_preview 启动
                    # （含硬解→软解回退）期间的 on_error 由启动流程统一处理，
                    # 避免重连循环与软解重试并行创建两个 FFmpeg 进程
                    if room_id in _mse_starting:
                        _log.debug(
                            "MSE error during startup, ignored (room=%s): %s",
                            room_id, err,
                        )
                        return

                    # 0. 防重入：退避等待期间新 on_error 可能再次触发本函数，
                    #    并发循环会各自启动 FFmpeg 进程；由在途循环接管，直接返回
                    prev_state = _mse_reconnect_state.get(room_id)
                    if prev_state and prev_state.get('running'):
                        _log.info(
                            "MSE reconnect loop already running for room %s, "
                            "ignoring duplicate error: %s", room_id, err,
                        )
                        return
                    _mse_reconnect_state[room_id] = _begin_mse_reconnect(prev_state)

                    # 1. 从 _mse_streamers 移除并停止已失效的 streamer（仅首次执行）
                    old_streamer = _preview_stream_registry().pop(room_id)
                    if old_streamer is not None:
                        try:
                            old_streamer.stop()
                        except Exception as exc:
                            _log.debug("停止旧 MSE streamer 失败: %s", exc)
                        _stop_idle_shared_ingest(room_id, reason="mse error cleanup")

                    current_error = err
                    mse_failure_reason = 'network'

                    async def _finalize_mse_error(error_text: str, reason: str, timeline_reason: str) -> None:
                        _mse_reconnect_state.pop(room_id, None)
                        _mse_live_phase.discard(room_id)
                        await srv.broadcast('mse_error', {
                            'room_id': room_id,
                            'error': error_text,
                            'reason': reason,
                        })

                        if reason == 'offline':
                            await _start_recording_file_mse(
                                srv,
                                mgr,
                                bridge,
                                room_id,
                                loop,
                                offline_message=error_text,
                                stop_recording_if_active=True,
                            )
                            return

                        def _clear_preview():
                            room = mgr.get_room(room_id)
                            if room is not None:
                                room.preview_enabled = False
                                room.preview_mode = 'live_mse'
                            _invalidate_room_timeline(room_id, reason=timeline_reason)

                        try:
                            await loop.run_in_executor(
                                _bridge_executor, lambda: bridge.manager.call(_clear_preview)
                            )
                        except Exception as exc:
                            _log.error("MSE error cleanup failed: %s", exc)
                        bridge.queue_broadcast({
                            'type': 'rooms_updated',
                            'data': {'rooms': _rooms_list(mgr)},
                        })

                    def _peek_reconnect_platform():
                        room = mgr.get_room(room_id)
                        if room is None:
                            return ""
                        return str(
                            getattr(room, "platform", "")
                            or getattr(getattr(room, "stream_info", None), "platform", "")
                            or ""
                        )

                    try:
                        reconnect_platform = await loop.run_in_executor(
                            _bridge_executor, lambda: bridge.manager.call(_peek_reconnect_platform)
                        )
                    except Exception:
                        reconnect_platform = ""
                    if not _preview_auto_reconnect_allowed(reconnect_platform):
                        _mse_reconnect_state.pop(room_id, None)
                        await _finalize_mse_error(
                            current_error or "预览失败",
                            mse_failure_reason,
                            f"mse_no_auto_reconnect:{room_id}",
                        )
                        return

                    while True:
                        # 2. 检查是否仍需预览（用户可能已手动关闭）
                        def _check_preview():
                            room = mgr.get_room(room_id)
                            if room is None:
                                return False
                            return room.preview_enabled

                        try:
                            still_previewing = await loop.run_in_executor(
                                _bridge_executor, lambda: bridge.manager.call(_check_preview)
                            )
                        except Exception:
                            still_previewing = False

                        if not still_previewing:
                            _mse_reconnect_state.pop(room_id, None)
                            return

                        # 3. 检查重连次数
                        state = _mse_reconnect_state.get(room_id, {'attempts': 0})
                        if state['attempts'] >= _MSE_MAX_RECONNECT:
                            _log.warning(
                                "MSE reconnect exhausted for room %s (%d attempts)",
                                room_id, state['attempts'],
                            )
                            exhausted_msg = (
                                _mse_offline_error_message(current_error)
                                if mse_failure_reason == 'offline'
                                else '预览重连失败，已达到最大重试次数，请手动重新开启预览'
                            )
                            await _finalize_mse_error(
                                exhausted_msg,
                                mse_failure_reason,
                                f"mse_reconnect_exhausted:{room_id}",
                            )
                            return

                        # 4. 计算指数退避延迟
                        delay = min(
                            _MSE_RECONNECT_BASE_DELAY * (2 ** state['attempts']),
                            _MSE_RECONNECT_MAX_DELAY,
                        )
                        state['attempts'] += 1
                        _mse_reconnect_state[room_id] = state

                        _log.info(
                            "MSE reconnect attempt %d/%d for room %s (delay=%.1fs, error=%s)",
                            state['attempts'], _MSE_MAX_RECONNECT, room_id, delay, current_error,
                        )

                        # 5. 广播重连中
                        await srv.broadcast('mse_reconnecting', {
                            'room_id': room_id,
                            'attempt': state['attempts'],
                            'max_attempts': _MSE_MAX_RECONNECT,
                            'delay': delay,
                        })

                        # 6. 等待退避延迟
                        await asyncio.sleep(delay)

                        # 7. 再次检查是否仍在预览
                        try:
                            still_previewing = await loop.run_in_executor(
                                _bridge_executor, lambda: bridge.manager.call(_check_preview)
                            )
                        except Exception:
                            still_previewing = False
                        if not still_previewing:
                            _mse_reconnect_state.pop(room_id, None)
                            return

                        # 8. 刷新流 URL。403/签名类错误直接强制刷新（跳过缓存复用）：
                        #    缓存 URL 时间戳可能未过期但 CDN 已拒绝（虎牙线路/IP 风控），
                        #    force=False 复用缓存返回 True 会让 force=True 永不执行，
                        #    导致重试继续用 403 的 URL 直到耗尽；平台策略负责隔离候选。
                        if _should_refresh_failed_stream(current_error):
                            try:
                                def _mark_failed_candidate_403():
                                    room = mgr.get_room(room_id)
                                    if room is not None:
                                        mark_failed_candidate(room.stream_info, current_error)
                                await loop.run_in_executor(
                                    _bridge_executor,
                                    lambda: bridge.manager.call(_mark_failed_candidate_403),
                                )
                            except Exception as exc:
                                _log.debug("MSE reconnect candidate policy failed: %s", exc)
                            try:
                                refresh_ok = await loop.run_in_executor(
                                    _recording_executor, lambda: mgr.refresh_stream_url(room_id, force=True)
                                )
                            except Exception as exc:
                                _log.error("MSE reconnect URL refresh failed: %s", exc)
                                refresh_ok = False
                        else:
                            try:
                                refresh_ok = await loop.run_in_executor(
                                    _recording_executor, lambda: mgr.refresh_stream_url(room_id, force=False)
                                )
                                if not refresh_ok:
                                    refresh_ok = await loop.run_in_executor(
                                        _recording_executor, lambda: mgr.refresh_stream_url(room_id, force=True)
                                    )
                            except Exception as exc:
                                _log.error("MSE reconnect URL refresh failed: %s", exc)
                                refresh_ok = False

                        if not refresh_ok:
                            try:
                                offline, offline_msg = await loop.run_in_executor(
                                    _recording_executor,
                                    lambda: _probe_stream_offline(mgr, room_id),
                                )
                            except Exception as exc:
                                _log.debug("MSE reconnect offline probe failed: %s", exc)
                                offline, offline_msg = False, ''
                            if offline:
                                mse_failure_reason = 'offline'
                                await _finalize_mse_error(
                                    offline_msg or _mse_offline_error_message(),
                                    'offline',
                                    f"mse_offline:{room_id}",
                                )
                                return
                            current_error = '流地址刷新失败'
                            continue  # 进入下一次循环重试

                        # 9. 读取刷新后的房间状态
                        def _read_snapshot():
                            room = mgr.get_room(room_id)
                            if room is None:
                                return None
                            return {
                                'is_connected': room.is_connected,
                                'stream_url': room.stream_info.stream_url if room.stream_info else '',
                                'platform': room.platform,
                                'headers': (room.stream_info.headers if room.stream_info else None) or {},
                                'quality_urls': (room.stream_info.quality_urls if room.stream_info else {}),
                            }

                        try:
                            snapshot = await loop.run_in_executor(
                                _bridge_executor, lambda: bridge.manager.call(_read_snapshot)
                            )
                        except Exception:
                            snapshot = None

                        if snapshot is None or not snapshot['is_connected'] or not snapshot['stream_url']:
                            try:
                                offline, offline_msg = await loop.run_in_executor(
                                    _recording_executor,
                                    lambda: _probe_stream_offline(mgr, room_id),
                                )
                            except Exception as exc:
                                _log.debug("MSE reconnect offline probe failed: %s", exc)
                                offline, offline_msg = False, ''
                            if offline:
                                mse_failure_reason = 'offline'
                                await _finalize_mse_error(
                                    offline_msg or _mse_offline_error_message(),
                                    'offline',
                                    f"mse_offline:{room_id}",
                                )
                                return
                            current_error = '房间未连接或无流信息'
                            continue  # 进入下一次循环重试

                        # 10. 获取预览画质（与初始启动统一走压力降级）
                        preview_params = _compute_preview_quality_params({})
                        r_width = int(preview_params.get('width') or 0)
                        r_height = int(preview_params.get('height') or 0)
                        r_fps = int(preview_params.get('fps') or 0)
                        r_bitrate = preview_params.get('video_bitrate')
                        r_crf = preview_params.get('crf_value')

                        r_headers = snapshot.get('headers') or {}
                        r_quality_urls = snapshot.get('quality_urls') or {}
                        r_stream_url = snapshot['stream_url']
                        settings = load_settings()
                        preview_quality = settings.get('preview_quality', '高清')
                        # B站无登录态时高画质易 403，复用录制/连接已验证的流（与初始启动一致）
                        from lsc.platforms.capabilities import get_platform_capabilities

                        if get_platform_capabilities(
                            snapshot.get('platform', '')
                        ).anonymous_quality_fallback:
                            try:
                                from lsc.platforms.credentials import has_usable_credentials

                                if not has_usable_credentials(snapshot.get('platform', '')):
                                    r_quality_urls = {}
                            except Exception as exc:
                                _log.debug("检查平台凭据失败，沿用原选流逻辑: %s", exc)
                        if r_quality_urls:
                            selected_url, _ = select_quality(
                                {'qualityUrls': r_quality_urls, 'streamUrl': r_stream_url, 'selectedQuality': ''},
                                preview_quality,
                            )
                            if selected_url:
                                r_stream_url = selected_url
                        from lsc.platforms.capabilities import get_platform_capabilities

                        r_probe = get_platform_capabilities(
                            snapshot.get('platform', '')
                        ).probe_timeout_sec

                        # 11. 创建并启动新的 MseStreamer
                        def _restart():
                            from lsc.core.services.mse_streamer import MseStreamer
                            try:
                                streamer = MseStreamer(
                                    url=r_stream_url,
                                    width=r_width,
                                    height=r_height,
                                    fps=r_fps,
                                    headers=r_headers or None,
                                    video_bitrate=r_bitrate,  # type: ignore[arg-type]
                                    crf_value=r_crf,  # type: ignore[arg-type]
                                    on_init_segment=lambda seg, _room_id=room_id: _push_mse_segment(  # type: ignore[misc]
                                        srv, loop, 'mse_init', _room_id, seg
                                    ),
                                    on_media_segment=lambda seg, _room_id=room_id: _push_mse_segment(  # type: ignore[misc]
                                        srv, loop, 'mse_segment', _room_id, seg
                                    ),
                                    on_error=lambda e, _room_id=room_id: asyncio.run_coroutine_threadsafe(  # type: ignore[misc,arg-type]
                                        _on_mse_error(_room_id, e, loop), loop
                                    ),
                                )
                                ok = streamer.start(startup_probe_timeout=r_probe)
                                if ok:
                                    _preview_stream_registry().set_legacy(room_id, streamer)
                                    return True, ''
                                stderr_tail = ''
                                try:
                                    stderr_tail = (streamer._last_stderr or '').strip()[:300]
                                except AttributeError:
                                    pass
                                try:
                                    streamer.stop()
                                except Exception as exc:
                                    _log.debug("停止启动失败的 streamer 失败: %s", exc)
                                return False, stderr_tail
                            except Exception as exc:
                                _log.error("MSE reconnect start failed: %s", exc)
                                return False, str(exc)

                        try:
                            success, error_detail = await loop.run_in_executor(
                                _recording_executor, _restart
                            )
                        except Exception as exc:
                            success, error_detail = False, str(exc)

                        if success:
                            _mse_live_phase.discard(room_id)
                            _log.info("MSE reconnect succeeded for room %s", room_id)
                            ready_state = _note_mse_reconnect_event(room_id, "media_ready")

                            def _rotate_epoch_on_reconnect():
                                room = mgr.get_room(room_id)
                                if room is None:
                                    return False
                                new_epoch = uuid4().hex
                                room.preview_epoch_id = new_epoch
                                get_timeline_service().on_preview_epoch_change(room_id, new_epoch)
                                return True

                            try:
                                await loop.run_in_executor(
                                    _bridge_executor, lambda: bridge.manager.call(_rotate_epoch_on_reconnect)
                                )
                            except Exception as exc:
                                _log.debug("MSE reconnect epoch rotate failed: %s", exc)

                            quality_fields = _preview_quality_response_fields(preview_params)
                            asyncio.create_task(
                                _watch_mse_reconnect_durable(
                                    room_id,
                                    ready_at=float(ready_state.get("media_ready_at") or time.monotonic()),
                                    broadcast=lambda: srv.broadcast(
                                        'mse_reconnected',
                                        {'room_id': room_id, **quality_fields},
                                    ),
                                )
                            )
                            _broadcast_rooms()
                            return
                        else:
                            _log.warning(
                                "MSE reconnect failed for room %s: %s",
                                room_id, error_detail,
                            )
                            current_error = f'重连失败：{error_detail}'
                            continue  # 进入下一次循环重试

                def _start():
                    """启动 MseStreamer。返回 (ok, error_detail)。

                    error_detail 在 ok=False 时携带具体失败原因（FFmpeg stderr
                    尾部或异常消息），供前端精确显示，避免笼统的"请检查 FFmpeg"
                    误导用户。
                    """
                    try:
                        from lsc.core.services.mse_streamer import MseStreamer

                        streamer = MseStreamer(
                            url=stream_url,
                            width=width,
                            height=height,
                            headers=preview_headers or None,
                            video_bitrate=video_bitrate,
                            crf_value=crf_value,
                            fps=target_fps,
                            on_init_segment=lambda seg: _push_mse_segment(
                                srv, loop, 'mse_init', room_id, seg
                            ),
                            on_media_segment=lambda seg: _push_mse_segment(
                                srv, loop, 'mse_segment', room_id, seg
                            ),
                            on_error=lambda err: asyncio.run_coroutine_threadsafe(  # type: ignore[arg-type]
                                _on_mse_error(room_id, err, loop), loop
                            ),
                        )
                        ok = streamer.start(startup_probe_timeout=probe_timeout)
                        if ok:
                            _preview_stream_registry().set_legacy(room_id, streamer)
                            return True, ''
                        # 启动失败：从 streamer 提取 stderr 详情
                        stderr_tail = ''
                        try:
                            stderr_tail = (streamer._last_stderr or '').strip()[:300]
                        except AttributeError:
                            pass
                        # 清理已启动的 FFmpeg 进程和管道，防止资源泄漏
                        try:
                            streamer.stop()
                        except Exception as exc:
                            _log.debug("停止启动失败的 streamer 失败 (start): %s", exc)
                        return False, stderr_tail
                    except FileNotFoundError:
                        # FFmpeg 可执行文件未找到
                        return False, 'FFmpeg 未找到，请在设置中配置 FFmpeg 路径或将其加入 PATH'
                    except Exception as exc:
                        _log.error("MSE streamer start failed: %s", exc)
                        return False, str(exc)

                success, error_detail = await asyncio.get_running_loop().run_in_executor(_recording_executor, _start)

                if not success:
                    # MSE 启动失败：不设置 preview_enabled，避免前端渲染 VideoPreview 导致反复重试。
                    # 根据 error_detail 区分失败原因：
                    # - FFmpeg 未找到 → 提示安装/配置
                    # - 有 stderr → 直播流连接失败（地址过期/主播下播/CDN 拒绝等）
                    # - 无 stderr → 未知原因
                    if not error_detail:
                        error_msg = 'MSE 流启动失败，请检查直播流是否在线'
                    elif 'FFmpeg 未找到' in error_detail:
                        error_msg = error_detail
                    else:
                        error_msg = f'直播流连接失败：{error_detail}'
                    # 403/鉴权失败：交给平台策略隔离坏候选并强制刷新。
                    if _should_refresh_failed_stream(error_detail):
                        try:
                            def _mark_failed_candidate_on_start():
                                room = mgr.get_room(room_id)
                                if room is not None:
                                    mark_failed_candidate(room.stream_info, error_detail)
                            await loop.run_in_executor(
                                _bridge_executor,
                                lambda: bridge.manager.call(_mark_failed_candidate_on_start),
                            )
                        except Exception as exc:
                            _log.debug("MSE start candidate policy failed: %s", exc)
                        try:
                            await loop.run_in_executor(
                                _recording_executor, lambda: mgr.refresh_stream_url(room_id, force=True)
                            )
                        except Exception as exc:
                            _log.error("MSE start 403 URL refresh failed: %s", exc)
                    await srv.broadcast('preview_phase', {'room_id': room_id, 'phase': 'error'})
                    return {'success': False, 'room_id': room_id, 'error': error_msg}

                # 启动成功：通过 orchestrator.call 在编排线程更新 preview_enabled
                # 若此处抛异常，streamer 已在 _mse_streamers 中但前端不知需要 stop，
                # 需主动清理避免进程泄漏
                def _set_preview_enabled():
                    room = mgr.get_room(room_id)
                    if room is not None:
                        new_epoch = uuid4().hex
                        room.preview_enabled = True
                        room.preview_mode = 'live_mse'
                        room.preview_error = ''
                        room.preview_epoch_id = new_epoch
                        get_timeline_service().on_preview_epoch_change(room_id, new_epoch)
                    return True

                try:
                    await asyncio.get_running_loop().run_in_executor(
                        _bridge_executor, lambda: bridge.manager.call(_set_preview_enabled)
                    )
                except Exception as exc:
                    # orchestrator.call 失败：清理已注册的 streamer，避免进程泄漏
                    _log.error("MSE preview_enabled 设置失败，清理 streamer: %s", exc)
                    leak_streamer = _preview_stream_registry().pop(room_id)
                    if leak_streamer is not None:
                        try:
                            leak_streamer.stop()
                        except Exception as stop_exc:
                            _log.debug("停止泄漏 streamer 失败 (cleanup): %s", stop_exc)
                        _stop_idle_shared_ingest(room_id, reason="preview state sync failed")
                    return {'success': False, 'room_id': room_id, 'error': f'预览状态同步失败：{exc}'}

                _note_mse_reconnect_event(room_id, "accepted")
                # streaming phase 由首个 init 段到达时广播（_push_mse_segment），
                # 此处仅重置 live 标志；启动期间 phase 保持 probing，避免
                # 前端 watchdog 在流未连通时误触发重连
                _mse_live_phase.discard(room_id)
                _broadcast_rooms()
                return _mse_preview_success_response(room_id, data, note='mse streaming started')
            finally:
                with _mse_starting_lock:
                    _mse_starting.discard(room_id)

        else:
            # Stop MSE streaming
            streamer = _preview_stream_registry().pop(room_id)
            if streamer is not None:
                def _stop():
                    streamer.stop()
                await asyncio.get_running_loop().run_in_executor(_bridge_executor, _stop)
                _stop_idle_shared_ingest(room_id, reason="preview stopped")

            def _disable():
                room = mgr.get_room(room_id)
                if room:
                    room.preview_enabled = False
                    room.preview_mode = 'live_mse'
                return True

            await asyncio.get_running_loop().run_in_executor(_bridge_executor, lambda: bridge.manager.call(_disable))
            _mse_reconnect_state.pop(room_id, None)
            _mse_live_phase.discard(room_id)
            _clear_mse_push_paused(room_id)
            _broadcast_rooms()
            _log.info("MSE 预览已停止: room_id=%s", room_id)
            await srv.broadcast('preview_phase', {'room_id': room_id, 'phase': 'idle'})
            return {'success': True, 'note': 'mse streaming stopped'}

    @server.on('request_mse_init')
    async def handle_request_mse_init(data):
        """前端挂载 VideoPreview 后主动请求补发 init 段。

        消除 mse_init 早于 rooms_updated 到达前端导致的竞态：前端收到
        rooms_updated 后才挂载 VideoPreview 并注册 player，此时可能已
        错过后端首次广播的 mse_init。本 handler 从缓存的 init 段重发。
        """
        room_id = data.get('room_id')
        if not room_id:
            return {'success': False, 'error': 'room_id is required'}
        _log.debug("请求 MSE init 重发: room_id=%s", room_id)
        streamer = _preview_stream_registry().get(room_id)
        if streamer is None:
            _log.debug("request_mse_init: room_id=%s 流未启动", room_id)
            return {'success': False, 'error': 'MSE 流未启动'}
        ok = streamer.replay_init()
        _log.debug("request_mse_init: room_id=%s, ok=%s", room_id, ok)
        return {'success': ok, 'room_id': room_id, 'note': 'init replayed' if ok else 'init not ready yet'}

    @server.on('mse_backpressure')
    async def handle_mse_backpressure(data):
        """前端 MSE pending 过高时通知后端暂停推送 media 段。

        data: { room_id, state: 'pause'|'resume', pending?: number }
        """
        room_id = (data or {}).get('room_id') or ''
        state = (data or {}).get('state') or ''
        if not room_id or state not in ('pause', 'resume'):
            return {'success': False, 'error': 'room_id and state=pause|resume required'}
        with _mse_push_paused_lock:
            if state == 'pause':
                _mse_push_paused.add(room_id)
            else:
                _mse_push_paused.discard(room_id)
        _log.debug(
            "mse_backpressure: room=%s state=%s pending=%s paused=%s",
            room_id, state, (data or {}).get('pending'), room_id in _mse_push_paused,
        )
        return {'success': True, 'room_id': room_id, 'state': state}

    def _broadcast_analysis_progress(room_id: str, stage: str, progress: float, detail: str) -> None:
        """广播 AI 分析进度到前端。

        使用 bridge.queue_broadcast 线程安全地投递消息，
        与 _queue_rooms_update / _broadcast_system_stats 采用相同的广播模式。
        广播失败只记日志，不中断分析流程。
        """
        try:
            bridge.queue_broadcast({
                'type': 'analysis_progress',
                'data': {
                    'room_id': room_id,
                    'stage': stage,
                    'progress': progress,
                    'detail': detail,
                },
            })
        except Exception as exc:
            _log.warning("广播分析进度失败: %s", exc)

    @server.on('start_analysis')
    async def handle_start_analysis(data):
        """启动场景分析/AI高光分析。

        参数:
            mode: 'scene'（场景检测，默认）| 'ai'（仅AI分析）| 'combined'（AI+场景融合）
            whisper_model: 'auto'/'tiny'/'base'/'small'/'medium'，仅 AI/combined 模式
            weights: 融合权重 {'audio': float, 'visual': float, 'scene': float}，仅 AI/combined 模式
        """
        room_id = data.get('room_id')
        threshold = _safe_float(data.get('threshold', 0.3), 0.3)
        mode = data.get('mode', 'scene')  # 'scene' | 'ai' | 'combined'
        game = data.get('game', 'valorant')  # 'valorant' | 'generic'

        if not room_id:
            return {'error': 'room_id is required'}
        with _analysis_jobs_lock:
            _existing_job = _analysis_jobs.get(room_id)
            if _existing_job and not _existing_job.get('completed_at') and not _existing_job.get('cancelled'):
                return {'success': False, 'error': '该房间已有分析任务进行中'}
            _continuous_conflict = any(
                room_id == (st.get('main_room_id') or '')
                or room_id in (st.get('target_room_ids') or [])
                for st in _continuous_tasks.values()
            )
        if _continuous_conflict:
            return {'success': False, 'error': '该房间正在持续分析中，请先停止持续分析'}
        _log.info("启动分析: room_id=%s, mode=%s, threshold=%.2f", room_id, mode, threshold)

        # 冲突检查通过后立即登记，消除「检查 → executor 内登记」的跨线程窗口：
        # 否则两个并发 start_analysis 都能通过检查，后登记覆盖先登记导致双任务并行。
        with _analysis_jobs_lock:
            _analysis_jobs[room_id] = {"progress": 0.0, "highlights": [], "mode": mode, "cancelled": False}

        def _do_analysis():
            room = manager.get_room(room_id)
            if room is None:
                _clear_analysis_job(room_id)
                return {'success': False, 'error': '房间不存在'}
            if not room.record_output_path or not os.path.isfile(room.record_output_path):
                _clear_analysis_job(room_id)
                return {'success': False, 'error': '录制文件不存在'}

            video_path = room.record_output_path
            t0 = time.monotonic()

            # 进度回调与取消检查（scene 和 AI 模式共用，P0-4）
            def _progress_cb(stage, progress, detail):
                with _analysis_jobs_lock:
                    if _analysis_jobs.get(room_id, {}).get('cancelled'):
                        return
                    _analysis_jobs[room_id]['progress'] = progress / 100.0
                    _analysis_jobs[room_id]['stage'] = stage
                _broadcast_analysis_progress(room_id, stage, progress, detail)

            def _cancel_check():
                with _analysis_jobs_lock:
                    return _analysis_jobs.get(room_id, {}).get('cancelled', False)

            highlights = _analyze_scene_or_rounds(
                video_path, game=game, threshold=threshold,
                progress_callback=_progress_cb, cancel_check=_cancel_check,
            )
            if highlights is None:
                return {'success': False, 'error': '分析已取消', 'cancelled': True}

            # P0-5: 补齐 scene 模式字段，与 AI 模式格式统一（前端 Modal 渲染需要）
            for _h in highlights:
                _h.setdefault("reason", "场景切换频繁")
                _h.setdefault("speech_score", 0.0)
                _h.setdefault("visual_score", 0.0)
                _h.setdefault("transcript", "")
            with _analysis_jobs_lock:
                _analysis_jobs[room_id] = {
                    "progress": 1.0,
                    "highlights": highlights,
                    "mode": mode,
                    "completed_at": time.time(),
                }
            # P0-3: 落盘到录制文件同目录 {basename}.analysis.json（重启不丢失）
            analysis_time = time.monotonic() - t0
            save_analysis_results(
                video_path, room_id, mode, highlights,
                analysis_time_sec=analysis_time,
            )
            _log.info("分析完成: room_id=%s, mode=%s, highlights=%d", room_id, mode, len(highlights))
            return {'success': True, 'mode': mode, 'highlights': highlights}

        executor = _ai_executor if mode in ('ai', 'combined') else _bridge_executor
        _timeout = 120
        try:
            result = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(executor, _do_analysis),
                timeout=_timeout,
            )
        except asyncio.TimeoutError:
            with _analysis_jobs_lock:
                _analysis_jobs.setdefault(room_id, {})['cancelled'] = True
            _log.error("分析超时（%ss），room_id=%s, mode=%s", _timeout, room_id, mode)
            return {
                'success': False,
                'error': f'分析超时（{_timeout}s），可能模型下载卡住或视频过长。请检查网络后重试。',
            }
        except Exception as exc:
            # 分析内部异常：清理登记，避免前端「分析中」永久卡死
            _clear_analysis_job(room_id)
            _log.error("分析执行异常: room_id=%s, mode=%s: %s", room_id, mode, exc, exc_info=True)
            return {'success': False, 'error': f'分析执行失败: {exc}'}
        return result

    @server.on('start_analysis_export')
    async def handle_start_analysis_export(data):
        """高光分析并自动导出（单房间 / 多房间同步）。

        参数:
            main_room_id: str         — 做高光分析的主直播间
            target_room_ids: [str]    — 要导出的所有房间（含 main；单房间时=[main]）
            mode: 'scene'|'ai'|'combined'（默认 scene）
            whisper_model / weights / threshold — 仅 AI/combined
            preset_id: str            — 导出预设
            job_prefix: str           — 前端关联进度用

        流程: 校验对齐组 → 分析主房间 → 高光按 content_offset 映射到每个目标房间
              → 批量导出。复用 export_progress/clip_completed/clip_failed 事件。
        """
        main_room_id = data.get('main_room_id')
        target_room_ids = data.get('target_room_ids') or ([main_room_id] if main_room_id else [])
        mode = data.get('mode', 'scene')
        weights = data.get('weights', {})
        threshold = _safe_float(data.get('threshold', 0.3), 0.3)
        game = data.get('game', 'valorant')  # 'valorant' | 'generic'
        preset_id = data.get('preset_id', '')
        job_prefix = data.get('job_prefix', f'hlexport-{int(time.time() * 1000)}')

        if not main_room_id:
            return {'error': 'main_room_id is required'}
        if not target_room_ids:
            target_room_ids = [main_room_id]

        with _analysis_jobs_lock:
            _existing_job = _analysis_jobs.get(main_room_id)
            if _existing_job and not _existing_job.get('completed_at') and not _existing_job.get('cancelled'):
                return {'success': False, 'error': '该房间已有分析任务进行中'}
            _target_id_set = set(target_room_ids)
            _continuous_conflict = any(
                (st.get('main_room_id') or '') in _target_id_set
                or bool(_target_id_set.intersection(st.get('target_room_ids') or []))
                for st in _continuous_tasks.values()
            )
        if _continuous_conflict:
            return {'success': False, 'error': '目标房间正在持续分析中，请先停止持续分析'}

        _log.info("分析并导出: main=%s, targets=%s, mode=%s", main_room_id, target_room_ids, mode)
        loop = asyncio.get_running_loop()

        def _do_analysis_and_export():
            ok, error, main_room, target_rooms = _validate_synced_analysis_targets(
                manager, main_room_id, target_room_ids, wait_for_file=True,
            )
            if not ok:
                return {'success': False, 'error': error}

            # 3. 高光分析主房间（复用 scene/AI 分析逻辑）
            video_path = main_room.record_output_path  # type: ignore[union-attr]
            t0 = time.monotonic()
            with _analysis_jobs_lock:
                _analysis_jobs[main_room_id] = {"progress": 0.0, "highlights": [], "mode": mode, "cancelled": False}

            def _progress_cb(stage, progress, detail):
                with _analysis_jobs_lock:
                    if _analysis_jobs.get(main_room_id, {}).get('cancelled'):
                        return
                    _analysis_jobs[main_room_id]['progress'] = progress / 100.0
                    _analysis_jobs[main_room_id]['stage'] = stage
                _broadcast_analysis_progress(main_room_id, stage, progress, detail)

            def _cancel_check():
                with _analysis_jobs_lock:
                    return _analysis_jobs.get(main_room_id, {}).get('cancelled', False)

            highlights = _analyze_scene_or_rounds(
                video_path, game=game, threshold=threshold,
                progress_callback=_progress_cb, cancel_check=_cancel_check,
            )
            if highlights is None:
                return {'success': False, 'error': '分析已取消', 'cancelled': True}

            # 补齐字段
            for _h in highlights:
                _h.setdefault("reason", "场景切换频繁")
                _h.setdefault("speech_score", 0.0)
                _h.setdefault("visual_score", 0.0)
                _h.setdefault("transcript", "")

            # 落盘分析结果（P0-3）
            analysis_time = time.monotonic() - t0
            save_analysis_results(
                video_path, main_room_id, mode, highlights,
                analysis_time_sec=analysis_time,
                weights=weights if weights else None,
            )
            with _analysis_jobs_lock:
                _analysis_jobs[main_room_id] = {
                    "progress": 1.0, "highlights": highlights, "mode": mode, "completed_at": time.time(),
                }

            if not highlights:
                return {'success': False, 'error': '未检测到高光片段', 'highlights': []}

            # 4. 高光按 content_offset 映射到各房间，仅入列（list_pending），不自动 queue_export
            #    与持续分析一致：用户在前端手动确认后再批量导出
            async def _submit_list_only():
                return await _auto_export_highlights(
                    main_room, target_rooms, highlights,
                    job_prefix=job_prefix, preset_id=preset_id,
                    defer_export=True, confirm_status='pending', list_only=True,
                )

            submitted_rounds = asyncio.run_coroutine_threadsafe(
                _submit_list_only(), loop
            ).result(timeout=60)
            submitted_list = list(submitted_rounds)
            _log.info("分析导出已入列: main=%s, 高光=%d, 房间=%d, 入列=%d",
                      main_room_id, len(highlights), len(target_rooms), len(submitted_list))
            if not submitted_list:
                return {
                    'success': False,
                    'error': '未能入列任何切片',
                    'highlights': highlights,
                }
            return {
                'success': True,
                'highlights': highlights,
                'submitted_count': len(submitted_list),
                'job_ids': [],
            }

        executor = _ai_executor if mode in ('ai', 'combined') else _bridge_executor
        _timeout = 120
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(executor, _do_analysis_and_export),
                timeout=_timeout,
            )
        except asyncio.TimeoutError:
            with _analysis_jobs_lock:
                _analysis_jobs.setdefault(main_room_id, {})['cancelled'] = True
            _log.error("分析导出超时（%ss），main_room=%s, mode=%s", _timeout, main_room_id, mode)
            return {
                'success': False,
                'error': f'分析超时（{_timeout}s），可能模型下载卡住或视频过长。请检查网络后重试。',
            }
        return result

    @server.on('cancel_analysis')
    async def handle_cancel_analysis(data):
        """取消正在进行的 AI 分析。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        with _analysis_jobs_lock:
            if room_id in _analysis_jobs:
                _analysis_jobs[room_id]['cancelled'] = True
                _log.info("取消分析: room_id=%s", room_id)
                return {'success': True, 'room_id': room_id}
        return {'success': False, 'error': '没有正在进行的分析任务'}

    @server.on('get_analysis_results')
    async def handle_get_analysis_results(data):
        """获取场景分析结果（自动清理 5 分钟前的过期任务）。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        _log.debug("获取分析结果: room_id=%s", room_id)
        # 清理过期的已完成分析任务，防止 _analysis_jobs 字典无限增长
        now = time.time()
        with _analysis_jobs_lock:
            stale_keys = [
                rid for rid, job in _analysis_jobs.items()
                if job.get('completed_at') and now - job['completed_at'] > _ANALYSIS_JOB_TTL
            ]
            for rid in stale_keys:
                _analysis_jobs.pop(rid, None)
            job = _analysis_jobs.get(room_id)
            if job is not None:
                job = dict(job)
        if job is None:
            # P0-3: 内存未命中（重启/TTL 过期），回退读录制文件同目录的分析结果 JSON
            room = manager.get_room(room_id)
            video_path = getattr(room, 'record_output_path', '') if room else ''
            if video_path and os.path.isfile(video_path):
                stored = load_analysis_results(video_path)
                if stored and not is_analysis_stale(video_path, stored):
                    return {
                        'progress': 1.0,
                        'highlights': stored.get('highlights', []),
                        'mode': stored.get('mode', 'scene'),
                        'stage': '',
                        'done': True,
                        'persisted': True,
                    }
            return {'progress': 0, 'highlights': [], 'done': False}
        return {
            'progress': job.get('progress', 0),
            'highlights': job.get('highlights', []),
            'mode': job.get('mode', 'scene'),
            'stage': job.get('stage', ''),
            'done': job.get('progress', 0) >= 1.0,
        }

    def _merge_highlights(existing: list[dict[str, Any]], new_hl: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """合并历史高光与新增高光, 基于 start/end 去重 (IoU >= 0.6 视为重复, 保留分数高的)。

        用于 scene 模式增量分析的累积, 避免全量替换丢失历史高光。
        """
        from lsc.analyzer.pipeline import _deduplicate_highlights
        return _deduplicate_highlights(existing + new_hl, iou_threshold=0.6)

    _CLIP_KEY_CACHE_MAX = 20000  # clip key 缓存上限：超限裁掉最旧一半，防长期运行无限增长

    def _bounded_clip_key_add(cache: dict, key: str, value: Any = None) -> None:
        """向保序 dict 缓存写入 key；超上限时裁掉最旧一半（近似 LRU）。"""
        if len(cache) >= _CLIP_KEY_CACHE_MAX:
            for _old_key in list(cache)[: _CLIP_KEY_CACHE_MAX // 2]:
                cache.pop(_old_key, None)
        cache[key] = value

    _exported_clip_ids: dict[str, None] = {}  # 已真正导出 / 入导出队列（保序、有界）
    _listed_clip_ids: dict[str, None] = {}  # 已向切片列表广播过的 room:round_key（保序、有界）
    _listed_clip_bounds: dict[str, tuple[float, float, str]] = {}  # listed_key -> (start, end, status)
    _deleted_clip_keys: dict[str, None] = {}  # 用户删除的 listed_key（tombstone，防止 OCR upsert 复活）
    _refined_round_keys: set[str] = set()  # 精修中或已确认的 round_key（OCR 不得改边界）
    # 保护 _refined_round_keys 的锁：asyncio handler 与分析 executor 线程
    # 均会并发读写（#102），无锁可导致重复导出或 OCR 误改冻结边界
    _refined_round_keys_lock = threading.Lock()

    def _remember_continuous_listed_clip(payload: dict[str, Any]) -> None:
        """Keep an authoritative clip snapshot for WS reconnect/reload recovery."""
        room_id = str(payload.get("room_id") or "")
        if not room_id:
            return
        snapshot_key = f"{room_id}:{payload.get('round_key') or payload.get('clip_id') or ''}"
        with _analysis_jobs_lock:
            if snapshot_key in _deleted_clip_keys:
                return
            snapshots: dict[str, dict[str, Any]] | None = None
            for task_state in _continuous_tasks.values():
                target_ids = task_state.get("target_room_ids") or []
                if room_id not in target_ids:
                    continue
                snapshots = task_state.setdefault("listed_clips", {})
                break
            if snapshots is None:
                # 同步分析（start_analysis_export）无 _continuous_tasks 条目，记到
                # _analysis_jobs 任务状态，供断连后 get_analysis_status 恢复。
                for job_state in _analysis_jobs.values():
                    target_ids = job_state.get("target_room_ids") or []
                    if room_id not in target_ids:
                        continue
                    snapshots = job_state.setdefault("listed_clips", {})
                    break
            if snapshots is None:
                return
            snapshots[snapshot_key] = dict(payload)
            if len(snapshots) > 512:
                for stale_key in list(snapshots)[:256]:
                    snapshots.pop(stale_key, None)

    async def _auto_export_highlights(main_room, target_rooms, highlights, job_prefix, preset_id='',
                                      defer_export: bool = True,
                                      confirm_status: str = 'pending',
                                      list_only: bool = False):
        """确认高光先入切片列表；默认延后导出，压力缓解后再真正 queue_export。

        defer_export=True（持续分析默认）:
          - 仅广播 clip_queued（export_deferred），不启动 FFmpeg
          - 任务写入 _deferred_export_jobs，由 _flush_deferred_exports 消费
        defer_export=False:
          - 立即 queue_export（手动/一次性同步分析路径）
        list_only=True:
          - 仅广播 clip_queued 入列，不写 _deferred_export_jobs（pending/ocr 不自动导出）
        confirm_status:
          - clip_queued 载荷中的确认状态（pending/refining/user_confirmed/ocr_confirmed）
        """
        if not highlights or not target_rooms:
            return []

        main_rec = _recording_media_start(main_room)
        main_offset = float(getattr(main_room, 'content_offset', 0.0) or 0.0)

        submitted_jobs = set()

        min_dur = _min_highlight_duration_for_queue(list_only=list_only)
        for idx, hl in enumerate(highlights):
            source_start = float(hl.get('start', 0) or 0)
            source_end = float(hl.get('end', 0) or 0)
            if source_start >= source_end:
                continue
            if source_end - source_start < min_dur:
                _log.info(
                    "跳过过短高光 %.1f-%.1f (min=%.1fs, list_only=%s)",
                    source_start, source_end, min_dur, list_only,
                )
                continue

            round_idx = int(hl.get('round_index', idx + 1))
            round_key = _valorant_round_key(hl)

            for target_room in target_rooms:
                rid = getattr(target_room, 'room_id', '')
                if not rid:
                    continue

                target_rec = _recording_media_start(target_room)
                target_offset = float(getattr(target_room, 'content_offset', 0.0) or 0.0)
                delta = (main_rec - target_rec) + (main_offset - target_offset)

                export_start = max(0.0, source_start + delta)
                export_end = max(0.0, source_end + delta)
                if export_start >= export_end:
                    continue
                if export_end - export_start < min_dur:
                    continue

                room_name = getattr(target_room, 'streamer_name', '') or rid
                listed_key = f"{rid}:{round_key}"
                start_r = round(export_start, 1)
                end_r = round(export_end, 1)

                if list_only:
                    # Take a snapshot under the lock to avoid concurrent
                    # mutation during the membership check (#102)
                    with _refined_round_keys_lock:
                        refined_snapshot = set(_refined_round_keys)
                    action = _should_broadcast_clip_list_update(
                        listed_key,
                        round_key,
                        start_r,
                        end_r,
                        confirm_status,
                        listed_ids=_listed_clip_ids,
                        exported_ids=_exported_clip_ids,
                        refined_keys=refined_snapshot,
                        listed_bounds=_listed_clip_bounds,
                        deleted_keys=_deleted_clip_keys,
                    )
                    if action == "skip":
                        continue
                    if action == "first":
                        _ai_clip_counters[rid] = _ai_clip_counters.get(rid, 0) + 1
                    label = format_ai_round_clip_label(
                        room_name, round_idx, _ai_clip_counters.get(rid, 1),
                    )
                    job_id = f"auto-{job_prefix}-{round_key}-{rid}"
                    clip_id = _clip_id(rid, export_start, export_end)
                    _bounded_clip_key_add(_listed_clip_ids, listed_key)
                    _bounded_clip_key_add(_listed_clip_bounds, listed_key, (start_r, end_r, confirm_status))
                    submitted_jobs.add((round_key, rid))
                    clip_payload = {
                            'clip_id': clip_id,
                            'job_id': job_id,
                            'room_id': rid,
                            'room_name': room_name,
                            'label': label,
                            'start': start_r,
                            'end': end_r,
                            'export_deferred': True,
                            'confirm_status': confirm_status,
                            'round_key': round_key,
                            'upsert': action == "upsert",
                            **_hybrid_clip_metadata(hl),
                    }
                    _remember_continuous_listed_clip(clip_payload)
                    bridge.queue_broadcast({
                        'type': 'clip_queued',
                        'data': clip_payload,
                    })
                    _log.info(
                        "仅入列(%s): room=%s, round_key=%s, status=%s, %.1f-%.1f",
                        action, rid, round_key, confirm_status, export_start, export_end,
                    )
                    continue

                if listed_key in _exported_clip_ids:
                    continue
                _ai_clip_counters[rid] = _ai_clip_counters.get(rid, 0) + 1
                label = format_ai_round_clip_label(room_name, round_idx, _ai_clip_counters[rid])
                job_id = f"auto-{job_prefix}-{round_key}-{rid}"
                clip_id = _clip_id(rid, export_start, export_end)

                if defer_export:
                    _deferred_export_jobs.append({
                        'room_id': rid,
                        'start': export_start,
                        'end': export_end,
                        'label': label,
                        'preset_id': preset_id,
                        'job_id': job_id,
                        'round_key': round_key,
                        'clip_id': clip_id,
                        'room_name': room_name,
                    })
                    _bounded_clip_key_add(_exported_clip_ids, listed_key)
                    _bounded_clip_key_add(_listed_clip_ids, listed_key)
                    _bounded_clip_key_add(_listed_clip_bounds, listed_key, (start_r, end_r, confirm_status))
                    submitted_jobs.add((round_key, rid))
                    bridge.queue_broadcast({
                        'type': 'clip_queued',
                        'data': {
                            'clip_id': clip_id,
                            'job_id': job_id,
                            'room_id': rid,
                            'room_name': room_name,
                            'label': label,
                            'start': start_r,
                            'end': end_r,
                            'export_deferred': True,
                            'confirm_status': confirm_status,
                            'round_key': round_key,
                            **_hybrid_clip_metadata(hl),
                        },
                    })
                    _log.info("延后导出仅入列: room=%s, job_id=%s, %.1f-%.1f", rid, job_id, export_start, export_end)
                    continue

                result = await queue_export(
                    rid, export_start, export_end,
                    label=label, preset_id=preset_id,
                    source='ai_highlight', job_id=job_id,
                )
                if result.get('success'):
                    _bounded_clip_key_add(_exported_clip_ids, listed_key)
                    _bounded_clip_key_add(_listed_clip_ids, listed_key)
                    _bounded_clip_key_add(_listed_clip_bounds, listed_key, (start_r, end_r, confirm_status))
                    submitted_jobs.add((round_key, rid))
                    bridge.queue_broadcast({
                        'type': 'clip_queued',
                        'data': {
                            'clip_id': clip_id,
                            'job_id': job_id,
                            'room_id': rid,
                            'room_name': room_name,
                            'label': label,
                            'start': start_r,
                            'end': end_r,
                            'export_deferred': False,
                            'confirm_status': confirm_status,
                            'round_key': round_key,
                            **_hybrid_clip_metadata(hl),
                        },
                    })
                    _log.info("自动导出入队: room=%s, job_id=%s, %.1f-%.1f", rid, job_id, export_start, export_end)
                else:
                    _log.warning("自动导出入队失败: room=%s, error=%s", rid, result.get('error'))

            if idx < len(highlights) - 1:
                try:
                    await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    break

        return submitted_jobs

    async def _flush_deferred_exports(force: bool = False) -> int:
        """压力缓解或收尾时，把延后队列真正送进导出 worker。"""
        if not _deferred_export_jobs:
            return 0
        pressure = _analysis_pressure()
        if not force and (
            pressure.get('pause_analysis')
            or pressure.get('level') == 'critical'
        ):
            return 0
        jobs = list(_deferred_export_jobs)
        _deferred_export_jobs.clear()
        flushed = 0
        for job in jobs:
            result = await queue_export(
                job['room_id'], job['start'], job['end'],
                label=job['label'], preset_id=job.get('preset_id', ''),
                source='ai_highlight', job_id=job['job_id'],
            )
            if result.get('success'):
                flushed += 1
                bridge.queue_broadcast({
                    'type': 'clip_export_started',
                    'data': {
                        'clip_id': job.get('clip_id'),
                        'job_id': job['job_id'],
                        'room_id': job['room_id'],
                    },
                })
                _log.info("延后导出入队: room=%s, job_id=%s", job['room_id'], job['job_id'])
            else:
                _deferred_export_jobs.append(job)
                _log.warning("延后导出入队失败: %s", result.get('error'))
        return flushed

    def _build_export_profile(settings, preset_id=None):
        """全系统唯一的 ExportProfile 构建入口。

        从 settings + preset_id 构造导出配置。preset_id 优先于 settings 全局值，
        供 queue_export（手动/自动导出统一入口）和 handle_export_clip 复用，
        消除 profile 构造重复。

        preset_id 为空或找不到时回退到全局 settings。
        """
        encoder = settings.get('encoder', 'h264_nvenc')
        crf_val = int(settings.get('crf', 23))
        resolution = settings.get('resolution', '')
        framerate = settings.get('framerate', '原画')
        audio_br = settings.get('audio_bitrate', '128k')
        vertical_crop = False

        if preset_id:
            preset = _get_export_preset(preset_id)
            if preset:
                encoder = preset.get('codec', encoder)
                crf_val = preset.get('crf', crf_val)
                resolution = preset.get('resolution', resolution)
                framerate = preset.get('framerate', framerate)
                audio_br = preset.get('audio_bitrate', audio_br)
                vertical_crop = preset.get('vertical_crop', vertical_crop)

        codec_map = {
            'H.264 NVENC': 'h264_nvenc', 'H.264 CPU': 'libx264',
            'H.265 NVENC': 'hevc_nvenc', 'H.265 CPU': 'libx265',
            'Copy': 'copy', 'h264_nvenc': 'h264_nvenc',
            'libx264': 'libx264', 'hevc_nvenc': 'hevc_nvenc',
            'libx265': 'libx265', 'copy': 'copy',
        }
        rate_mode_map = {'CRF 质量': 'crf', '码率限制': 'bitrate', '不限制': 'unrestricted'}
        bitrate = str(settings.get('bitrate', 8000))
        video_bitrate = f"{bitrate}k" if not bitrate.endswith(('k', 'M')) else bitrate
        if resolution and ":" in resolution:
            resolution = resolution.replace(":", "x")
        enc_preset = settings.get('preset', 'medium')

        codec = codec_map.get(encoder, 'libx264')
        # 导出预设可能保存了 NVENC 编码器。在没有 NVIDIA 编码器的机器（例如
        # VMware 虚拟机）上，必须在启动 FFmpeg 前回退到 CPU 编码。
        if codec in {'h264_nvenc', 'hevc_nvenc'} and not _check_nvenc():
            fallback_codec = 'libx265' if codec == 'hevc_nvenc' else 'libx264'
            _log.warning("导出 NVENC 不可用，已自动回退到 %s", fallback_codec)
            codec = fallback_codec

        return ExportProfile(
            codec=codec,
            crf=crf_val, preset=enc_preset,
            rate_mode=rate_mode_map.get(settings.get('param_mode', 'CRF 质量'), 'crf'),
            video_bitrate=video_bitrate, audio_bitrate=audio_br,
            resolution=resolution, fps=_parse_fps(framerate),
            vertical_crop=vertical_crop,
        )

    async def _continuous_valorant_worker(
        room_id, mode, game, threshold,
        _continuous_tasks, _analysis_semaphore, _bridge_executor,
        scan_result_container: dict,
    ) -> None:
        """后台 Worker：连续执行 detect_valorant_rounds_ocr。

        持有 _analysis_semaphore 确保同时只有 1 个 FFmpeg。
        主循环通过 scan_result_container['video_path'] / ['current_dur'] / ['refine_with_ocr']
        传入最新参数，通过 ['done_event'] 获知何时消费完毕、何时需重启分析。
        """
        loop = asyncio.get_running_loop()
        _log.info(f"持续分析 Worker 启动: room_id={room_id}, mode={mode}")
        try:
            while True:
                with _analysis_jobs_lock:
                    if _continuous_tasks.get(room_id, {}).get('cancelled'):
                        break
                # 等待主循环 kick 或上一次结果被消费
                await asyncio.sleep(0.5)
                with _analysis_jobs_lock:
                    task_state = _continuous_tasks.get(room_id)
                if not task_state or task_state.get('cancelled'):
                    break
                if not task_state.get('scan_requested'):
                    continue

                video_path = task_state.get('video_path')
                current_dur = task_state.get('current_dur', 0.0)
                refine_with_ocr = task_state.get('refine_with_ocr', False)
                scan_range = task_state.get('scan_range', (0.0, current_dur))
                scan_timeout = int(task_state.get('scan_timeout', 120))
                _finalizing = bool(
                    task_state.get('finalizing') or task_state.get('finalize_pending')
                )
                task_state['degraded_mode'] = None

                if not video_path or current_dur <= 3.0:
                    task_state['scan_requested'] = False
                    continue

                _vp = video_path  # capture for closure
                def _scan_cancel_check():
                    with _analysis_jobs_lock:
                        st = _continuous_tasks.get(room_id, {})
                        return bool(st.get('cancelled') or st.get('scan_abort'))

                def _refine_cancel_check():
                    # 粗扫即将抢占时置 refine_abort，密扫须尽快退出释放 semaphore
                    with _analysis_jobs_lock:
                        st = _continuous_tasks.get(room_id, {})
                        return bool(
                            st.get('cancelled')
                            or st.get('scan_abort')
                            or st.get('refine_abort')
                        )

                def _do_scan(
                    _vp=_vp,
                    _dur=current_dur,
                    _ocr=refine_with_ocr,
                    _range=scan_range,
                    _mode=mode,
                ):
                    from lsc.analyzer.base import ScanWindow
                    from lsc.analyzer.registry import get as get_analyzer
                    _cfg = load_config()
                    _ffmpeg = _cfg.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
                    _cancel = _scan_cancel_check
                    plugin = get_analyzer(game)
                    if game == 'valorant' and _mode == 'valorant_round':
                        _window = ScanWindow(
                            start_sec=float(_range[0]),
                            end_sec=float(_range[1]),
                            timeout_sec=float(scan_timeout),
                            use_ocr=bool(_ocr),
                        )
                        _scan_state = {
                            'mode': _mode,
                            'game': game,
                            'ffmpeg_path': _ffmpeg,
                            'session_id': str(task_state.get('session_id', '') or ''),
                            'runtime_state': task_state.setdefault('ocr_runtime_state', {}),
                            'current_dur': _dur,
                            'finalize': _finalizing,
                            'valorant_profile': str(task_state.get('valorant_profile') or 'valorant'),
                            'ocr_sample_interval': float(task_state.get('ocr_sample_interval', 1.0)),
                        }
                        return plugin.scan_window(
                            _vp, _window, _scan_state, cancel_check=_cancel,
                        )
                    return _detect_rounds_by_audio_rhythm(
                        _vp, duration=_dur, ffmpeg_path=_ffmpeg,
                        time_range=_range,
                        cancel_check=_cancel,
                    )

                task_state['scan_requested'] = False
                task_state['scan_running'] = True
                task_state['scan_abort'] = False
                # 粗扫优先：打断仍占用 semaphore 的后台密扫，避免滞后螺旋
                task_state['refine_abort'] = True
                task_state['_scan_start_mono'] = time.monotonic()
                try:
                    # 超时后仍须持锁等到线程经 cancel_check 退出，避免双 OCR 并发压垮 DirectML。
                    async with _analysis_semaphore:
                        task_state['refine_abort'] = False
                        fut = loop.run_in_executor(_ai_executor, _do_scan)
                        try:
                            result = await asyncio.wait_for(
                                asyncio.shield(fut),
                                timeout=scan_timeout,
                            )
                        except TimeoutError:
                            task_state['scan_abort'] = True
                            _log.warning(
                                "持续分析扫描超时，中止 OCR/抽帧: room_id=%s, timeout=%ss",
                                room_id,
                                scan_timeout,
                            )
                            # 等待同一线程真实退出后才能离开 semaphore；硬上限防止永久挂死。
                            try:
                                await asyncio.wait_for(
                                    asyncio.shield(fut),
                                    timeout=_SCAN_ABORT_HARD_SEC,
                                )
                            except TimeoutError:
                                _log.error(
                                    "扫描线程硬超时仍未退出，放弃等待以免占死任务槽: room_id=%s",
                                    room_id,
                                )
                            raise
                        completed_dur = (
                            float(scan_range[1])
                            if game == 'valorant' and mode == 'valorant_round'
                            else current_dur
                        )
                        # 粗结果先入列：唤醒主循环，消除密扫尖峰对入列延迟
                        scan_result_container['result'] = result or []
                        scan_result_container['error'] = None
                        scan_result_container['video_path'] = video_path
                        scan_result_container['current_dur'] = completed_dur
                        scan_result_container['completed_at'] = time.time()
                        scan_result_container['degraded_mode'] = None
                        scan_result_container['boundary_refine_pass'] = False
                        done_ev = task_state.get('scan_done_event')
                        if done_ev is not None:
                            try:
                                done_ev.set()
                            except Exception:
                                _log.debug("scan_done_event.set 失败", exc_info=True)
                        _log.info(
                            "持续分析 Worker 完成: room_id=%s, %d 回合",
                            room_id,
                            len(result or []),
                        )

                        # 密扫改后台：粗结果已入列；密扫可被下一窗粗扫 refine_abort 抢占
                        _need_refine = [
                            r for r in (result or [])
                            if isinstance(r, dict) and r.get('boundary_refined') is False
                        ]
                        # 已明显滞后时跳过密扫，优先追赶粗扫（密扫单回合可达 30–55s）
                        _lag_now = max(0.0, float(current_dur) - float(completed_dur))
                        if (
                            _need_refine
                            and game == 'valorant'
                            and mode == 'valorant_round'
                            and not _finalizing
                            and not task_state.get('cancelled')
                            and not task_state.get('scan_abort')
                            and _lag_now <= 25.0
                        ):
                            _refine_vp = video_path
                            _refine_rounds = [dict(r) for r in (result or [])]
                            _refine_dur = completed_dur

                            async def _boundary_refine_bg(
                                _vp=_refine_vp,
                                _rounds=_refine_rounds,
                                _dur=_refine_dur,
                            ):
                                def _do_boundary_refine():
                                    from lsc.analyzer.valorant_ocr_rounds import (
                                        refine_valorant_round_boundaries,
                                    )
                                    _cfg = load_config()
                                    _ffmpeg = (
                                        _cfg.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
                                    )
                                    return refine_valorant_round_boundaries(
                                        _rounds,
                                        _vp,
                                        _ffmpeg,
                                        cancel_check=_refine_cancel_check,
                                    )

                                try:
                                    async with _analysis_semaphore:
                                        with _analysis_jobs_lock:
                                            st = _continuous_tasks.get(room_id)
                                            if (
                                                not st
                                                or st.get('cancelled')
                                                or st.get('scan_abort')
                                                or st.get('refine_abort')
                                            ):
                                                return
                                            st['refine_running'] = True
                                        try:
                                            refined = await loop.run_in_executor(
                                                _ai_executor, _do_boundary_refine,
                                            )
                                        finally:
                                            with _analysis_jobs_lock:
                                                st2 = _continuous_tasks.get(room_id)
                                                if st2 is not None:
                                                    st2['refine_running'] = False
                                except Exception as refine_exc:
                                    _log.warning(
                                        "边界密扫失败（保留粗边界）: room_id=%s, err=%r",
                                        room_id,
                                        refine_exc,
                                    )
                                    return
                                with _analysis_jobs_lock:
                                    st = _continuous_tasks.get(room_id)
                                    if (
                                        not refined
                                        or not st
                                        or st.get('cancelled')
                                        or st.get('scan_abort')
                                        or st.get('refine_abort')
                                    ):
                                        return
                                scan_result_container['result'] = refined
                                scan_result_container['error'] = None
                                scan_result_container['video_path'] = _vp
                                scan_result_container['current_dur'] = _dur
                                scan_result_container['completed_at'] = time.time()
                                scan_result_container['boundary_refine_pass'] = True
                                _ev = None
                                with _analysis_jobs_lock:
                                    st = _continuous_tasks.get(room_id)
                                    if st is not None:
                                        _ev = st.get('scan_done_event')
                                if _ev is not None:
                                    try:
                                        _ev.set()
                                    except Exception:
                                        _log.debug("scan_done_event.set 失败", exc_info=True)
                                _log.info(
                                    "持续分析边界精修完成: room_id=%s, %d 回合",
                                    room_id,
                                    len(refined),
                                )

                            asyncio.create_task(
                                _boundary_refine_bg(),
                                name=f"boundary-refine-{room_id[:8]}",
                            )
                        elif _need_refine and _lag_now > 25.0:
                            _log.info(
                                "持续分析跳过密扫（优先追赶）: room_id=%s, lag=%.0fs",
                                room_id,
                                _lag_now,
                            )
                except Exception as exc:
                    # TimeoutError 的 str() 常为空，必须用 repr + exc_info
                    _log.warning(
                        "持续分析 Worker 异常: room_id=%s, err=%r, timeout=%ss",
                        room_id,
                        exc,
                        scan_timeout,
                        exc_info=True,
                    )
                    scan_result_container['result'] = []
                    scan_result_container['error'] = repr(exc)
                    scan_result_container['video_path'] = video_path
                    scan_result_container['current_dur'] = (
                        float(scan_range[1])
                        if game == 'valorant' and mode == 'valorant_round'
                        else current_dur
                    )
                    # 写入 completed_at，让主循环能消费失败并触发收尾重试
                    scan_result_container['completed_at'] = time.time()
                    scan_result_container['boundary_refine_pass'] = False
                finally:
                    task_state['scan_running'] = False
                    task_state['scan_abort'] = False
                    done_ev = task_state.get('scan_done_event')
                    if done_ev is not None:
                        try:
                            done_ev.set()
                        except Exception:
                            _log.debug("scan_done_event.set 失败", exc_info=True)
        except asyncio.CancelledError:
            # 停止持续分析时的正常取消路径；记录以便排查「分析突然停止」
            _log.debug("持续分析 Worker 被取消: room_id=%s", room_id)
        except Exception as exc:
            _log.error(f"持续分析 Worker 异常退出: room_id={room_id}, {exc}", exc_info=True)
            # 与单次扫描失败一致：写入失败结果并唤醒主循环。否则 scan_requested
            # 无人消费，主循环会一直「扫描中」空转；主循环随后检测到 worker 退出并重建。
            try:
                _st = _continuous_tasks.get(room_id, {})
                scan_result_container['result'] = []
                scan_result_container['error'] = repr(exc)
                scan_result_container['video_path'] = str(_st.get('video_path') or '')
                scan_result_container['current_dur'] = float(_st.get('current_dur') or 0.0)
                scan_result_container['completed_at'] = time.time()
                _done_ev = _st.get('scan_done_event')
                if _done_ev is not None:
                    _done_ev.set()
            except Exception:
                _log.debug("持续分析 Worker 失败上报异常", exc_info=True)

    async def _continuous_analysis_loop(
        main_room_id: str,
        target_room_ids: list[str],
        interval: int,
        threshold: float,
        mode: str = 'scene', game: str = 'valorant',
        valorant_profile: str | None = None,
    ) -> None:
        """持续分析后台循环（生产者-消费者模式）。

        Worker (_continuous_valorant_worker) 在后台独立执行 OCR/音频分析。
        主循环每 interval 秒：
          1. 更新任务状态 (video_path/dur/refine_with_ocr/scan_range)
          2. Kick worker (scan_requested=True)
          3. 消费上一轮结果（如果已被 worker 写入 scan_result_container）
          4. 导出 + 广播

        关键改进：主循环不再被 detect_valorant_rounds 阻塞。
        """
        room_id = main_room_id
        last_analyzed = 0.0
        last_consumed_at = 0.0
        all_highlights: list[dict[str, Any]] = []
        # 收尾分析状态：录制停止后从游标继续处理尾部，不默认全文件重扫
        _finalize_pending = False        # 是否有待完成的收尾扫描
        _finalize_started = False        # 收尾扫描是否已启动
        _finalize_failures = 0           # 收尾失败次数（超时/异常），用于重试与加长超时
        _finalize_max_attempts = 3
        _recording_was_active = False    # 录制是否曾经处于活跃状态
        _recording_stop_ticks = 0        # 录制停止后经过的 tick 数（延迟确认防抖）
        _last_recording_wallclock = 0.0  # 录制中最后一次墙钟时长（停录后钳制 probe）
        video_path = ''
        current_dur = 0.0
        # 文件替换检测：重连会创建新录制文件，需重置分析游标避免反向区间
        _last_video_path = ''            # 上一轮的文件路径
        _file_switch_cooldown = 0        # 文件切换后的冷却 tick 数（抑制误判录制停止）
        _skip_sleep_ticks = 0            # 连续跳过 sleep 的 tick 数（防御忙循环）
        loop = asyncio.get_running_loop()
        _valorant_incremental_rounds = mode == "valorant_round" and game == "valorant"

        # Worker 共享状态
        scan_result: dict[str, Any] = {'result': [], 'video_path': '', 'current_dur': 0.0, 'completed_at': 0.0}
        scan_done_event = asyncio.Event()

        def _get_recording_file_info():
            room = manager.get_room(room_id)
            if room is None or not room.record_output_path:
                return None, 0.0
            path = room.record_output_path
            if os.path.isfile(path):
                dur = _get_video_duration(path)
                if dur > 0:
                    return path, dur
            base, ext = os.path.splitext(path)
            for candidate in (path + '.tmp', base + '.tmp', path + '.tmp' + ext):
                if os.path.isfile(candidate):
                    dur = _get_video_duration(candidate)
                    if dur > 0:
                        return candidate, dur
            return None, 0.0

        # 初始化任务状态
        with _analysis_jobs_lock:
            if room_id not in _continuous_tasks:
                _continuous_tasks[room_id] = {}
            _continuous_tasks[room_id].update({
                'cancelled': False,
                'scan_requested': False,
                'scan_running': False,
                'scan_done_event': scan_done_event,
                'video_path': '',
                'current_dur': 0.0,
                'refine_with_ocr': False,
                'scan_range': (0.0, 0.0),
                'scan_timeout': 120,
                'full_rescan': True,
                'last_analyzed': 0.0,
                'highlights': [],
                'result_ready': False,
                'shadow_mode': _valorant_vision_shadow_enabled() and _valorant_incremental_rounds,
                'shadow_rounds_detected': 0,
                'shadow_listable_rounds': 0,
                'shadow_vision_confirmed': 0,
                # 纯 OCR 检测器跨窗口状态（FSM/锚点/计时器外推）
                'ocr_runtime_state': {},
                'valorant_profile': 'valorant',
                # S1: worker 崩溃重建计数（达到上限则终止任务，防止无限重建循环）
                'worker_restarts': 0,
            })

        # 启动后台 Worker（崩溃后由主循环检测并重建）
        def _spawn_worker():
            _w = asyncio.create_task(
                _continuous_valorant_worker(
                    room_id, mode, game, threshold,
                    _continuous_tasks, _analysis_semaphore, _bridge_executor,
                    scan_result,
                ),
                name=f"continuous-worker-{room_id[:8]}",
            )
            with _analysis_jobs_lock:
                if room_id in _continuous_tasks:
                    _continuous_tasks[room_id]['worker_task'] = _w
            return _w

        _worker_task = _spawn_worker()

        _log.info("持续分析启动: room_id=%s, mode=%s, game=%s, interval=%ds, 增量回合窗口=%s",
                  room_id, mode, game, interval, _valorant_incremental_rounds)

        _scan_counter = 0  # tick 计数器，用于状态机与局部 OCR 预算
        try:
            while True:
                with _analysis_jobs_lock:
                    if _continuous_tasks.get(room_id, {}).get('cancelled'):
                        break
                # 质量优先模式下压力归一化为 normal，分析不再被掐停/拉长间隔
                pressure = _analysis_pressure()
                interval_base = 5 if _valorant_incremental_rounds else max(interval, 20)
                # 获取超时状态用于智能恢复
                with _analysis_jobs_lock:
                    state = _continuous_tasks.get(room_id)
                _consecutive_timeouts = int(state.get('consecutive_scan_timeouts', 0) or 0) if state else 0
                _ocr_degraded = int(state.get('ocr_degraded_remaining', 0) or 0) if state else 0
                effective_interval, skip_for_pressure = _continuous_effective_interval(
                    interval_base, last_analyzed, _valorant_incremental_rounds, pressure,
                    consecutive_timeouts=_consecutive_timeouts,
                    ocr_degraded_remaining=_ocr_degraded,
                )
                if state:
                    state['resource_pressure'] = pressure
                    state['effective_interval'] = effective_interval

                # Fix: 首次 tick 加速；扫描中短轮询；Worker 完成立即唤醒
                _sleep_time = float(effective_interval)
                if last_analyzed <= 0.0:
                    _sleep_time = min(_sleep_time, 10.0)
                with _analysis_jobs_lock:
                    state = _continuous_tasks.get(room_id)
                if state and (state.get('scan_running') or state.get('scan_requested')):
                    _sleep_time = min(_sleep_time, 2.0)

                _pending_result = float(scan_result.get('completed_at', 0.0) or 0.0) > last_consumed_at
                if not _pending_result:
                    try:
                        scan_done_event.clear()
                        # 复查：stop / worker 完成可能在 clear 前已 set event
                        # （同一事件循环内的连续同步区段，set 会被 clear 吞掉），
                        # 避免停止 / 结果消费被无谓延迟一整个 sleep 周期。
                        with _analysis_jobs_lock:
                            _pre_sleep_state = _continuous_tasks.get(room_id)
                        _skip_wait = bool(
                            _pre_sleep_state is None
                            or _pre_sleep_state.get('cancelled')
                            or float(scan_result.get('completed_at', 0.0) or 0.0) > last_consumed_at
                        )
                        if not _skip_wait:
                            await asyncio.wait_for(scan_done_event.wait(), timeout=_sleep_time)
                            _skip_sleep_ticks = 0
                        else:
                            _skip_sleep_ticks += 1
                            if _skip_sleep_ticks >= _MAX_SKIP_SLEEP_TICKS:
                                # 防御：任何路径组合导致持续跳过 sleep 时强制节流，避免忙循环广播风暴
                                await asyncio.sleep(0.5)
                                _skip_sleep_ticks = 0
                    except asyncio.TimeoutError:
                        _skip_sleep_ticks = 0
                        pass
                    except asyncio.CancelledError:
                        break
                else:
                    # worker 结果未消费时跳过本 tick 的 sleep 等待；限制连续跳过次数
                    # 防止「结果一直不消费」的路径组合（如压力让路）造成忙循环。
                    _skip_sleep_ticks += 1
                    if _skip_sleep_ticks >= _MAX_SKIP_SLEEP_TICKS:
                        await asyncio.sleep(0.5)
                        _skip_sleep_ticks = 0

                with _analysis_jobs_lock:
                    state = _continuous_tasks.get(room_id)
                if not state or state.get('cancelled'):
                    if state:
                        state['status'] = 'stopping'
                        state['analysis_stage'] = '停止中'
                        state['scan_abort'] = True
                        if state.get('scan_running'):
                            try:
                                await asyncio.wait_for(scan_done_event.wait(), timeout=_SCAN_ABORT_GRACE_SEC)
                            except asyncio.TimeoutError:
                                _log.warning("停止等待扫描超时: room_id=%s", room_id)
                    break
                # worker 存活检测：worker 只在 cancelled 时正常退出，其它退出视为崩溃
                # （内部异常已上报 scan_result 后退出，外层未捕获异常也在此兜底）。
                # 崩溃后重建 worker，避免 scan_requested 无人消费导致「扫描中」空转。
                _wt = state.get('worker_task')
                if _wt is not None and _wt.done() and not state.get('cancelled'):
                    _wt_err = _wt.exception()
                    _restarts = int(state.get('worker_restarts', 0) or 0)
                    if _restarts >= _WORKER_MAX_RESTARTS:
                        _log.error(
                            "持续分析 Worker 反复崩溃（%d 次），终止任务: room_id=%s",
                            _restarts, room_id,
                        )
                        with _analysis_jobs_lock:
                            if room_id in _continuous_tasks:
                                _continuous_tasks[room_id]['cancelled'] = True
                                _continuous_tasks[room_id]['analysis_stage'] = '扫描器异常终止'
                                _continuous_tasks[room_id]['last_scan_error'] = (
                                    repr(_wt_err) if _wt_err is not None else 'worker 反复崩溃'
                                )
                        break
                    _log.error(
                        "持续分析 Worker 崩溃，重建 (%d/%d): room_id=%s, err=%r",
                        _restarts + 1, _WORKER_MAX_RESTARTS, room_id,
                        repr(_wt_err) if _wt_err is not None else 'unknown',
                    )
                    with _analysis_jobs_lock:
                        if room_id in _continuous_tasks:
                            _continuous_tasks[room_id]['worker_restarts'] = _restarts + 1
                            _continuous_tasks[room_id]['analysis_stage'] = '扫描器重启中'
                    _worker_task = _spawn_worker()
                video_path, current_dur = await loop.run_in_executor(
                    _probe_executor, _get_recording_file_info,
                )

                # 文件替换检测：重连（如虎牙 URL 过期主动重连）会创建新录制文件。
                # 检测到文件切换时，重置分析游标和录制停止计数器，避免：
                #   1. 误判录制停止触发错误的收尾扫描
                #   2. 收尾扫描产生反向区间（last_analyzed > current_dur）导致失败
                _path_changed = video_path and video_path != _last_video_path
                if _path_changed and _last_video_path:
                    _log.info(
                        "持续分析检测到录制文件切换: room_id=%s, old=%s, new=%s, 重置分析游标",
                        room_id, os.path.basename(_last_video_path), os.path.basename(video_path),
                    )
                    last_analyzed = 0.0
                    _recording_stop_ticks = 0
                    _finalize_pending = False
                    _finalize_started = False
                    _finalize_failures = 0
                    _last_recording_wallclock = 0.0
                    _file_switch_cooldown = 3  # 冷却 3 个 tick，避免立即再次触发
                    with _analysis_jobs_lock:
                        if room_id in _continuous_tasks:
                            _continuous_tasks[room_id]['full_rescan'] = True
                            _continuous_tasks[room_id]['last_analyzed'] = 0.0
                            # 文件切换必须重置 OCR 跨窗口状态（FSM/锚点/计时器外推）：
                            # 否则旧文件 last_processed_ts 只增不减，会把新文件前 N 秒帧
                            # 全部按"已处理过"过滤，导致重新开录的前几分钟回合静默漏检。
                            _continuous_tasks[room_id]['ocr_runtime_state'] = {}
                if video_path:
                    _last_video_path = video_path

                room_obj = manager.get_room(room_id)
                is_still_recording = bool(room_obj and getattr(room_obj, 'is_recording', False))
                # 共享进样模式：上游退出后 recording_active 会变为 False，
                # 此时必须同步视为"录制已停止"，否则持续分析会永久卡在"等待新片段"。
                if is_still_recording:
                    try:
                        from lsc.core.services.ingest_registry import get_shared_ingest_registry
                        _ingest = get_shared_ingest_registry().get(room_id)
                        if _ingest is not None and not getattr(_ingest, "recording_active", False):
                            is_still_recording = False
                            _log.info(
                                "持续分析检测到共享进样录制已停止: room_id=%s, recording_error=%s",
                                room_id,
                                getattr(_ingest, "recording_error", "") or getattr(_ingest, "upstream_error", ""),
                            )
                    except Exception as exc:
                        _log.debug("检查共享进样录制状态失败: room_id=%s, %s", room_id, exc)
                recording_start = float(getattr(room_obj, 'recording_start_mono', 0.0) or 0.0)
                wallclock_dur = (
                    time.monotonic() - recording_start
                    if is_still_recording and recording_start
                    else 0.0
                )
                # 录制中时长校准（双向）：写入中的 MP4 无稳定 moov，ffprobe 时长常取不到，
                # 而 _get_video_duration 的码率估算有两种失效方向：
                #   虚高（自举漂移）→ scan_range 末端 seek 超出文件实际数据 → 抽帧 0 帧失败循环；
                #   虚低（估算被物理校验拒绝后回退旧缓存）→ 窗口 end 被限制、分析追不上
                #   录制 → 滞后持续增长（real 日志：probe 299s vs 墙钟 523s）。
                # 录制中一律以墙钟为准并留写缓冲容差（frag_keyframe 下 15s），两种偏差都不超界。
                if wallclock_dur > 0:
                    _last_recording_wallclock = wallclock_dur
                    recorded_duration = wallclock_dur
                    buffered_wall = max(0.0, wallclock_dur - _RECORDING_WRITE_BUFFER_SEC)
                    if abs(current_dur - buffered_wall) > 15.0:
                        _log.debug(
                            "持续分析时长校准: room_id=%s, probed=%.1fs, wallclock=%.1fs, buffered=%.1fs",
                            room_id, current_dur, wallclock_dur, buffered_wall,
                        )
                    current_dur = buffered_wall
                else:
                    # 停录后：probe/码率缓存可能虚高（现场 1787s → 2226s），钳到最后录制墙钟
                    _clamped = _clamp_post_stop_duration(
                        current_dur, _last_recording_wallclock,
                    )
                    if _clamped < current_dur:
                        _log.warning(
                            "持续分析停录后时长虚高已钳制: room_id=%s, probed=%.1fs, last_wall=%.1fs",
                            room_id, current_dur, _last_recording_wallclock,
                        )
                        current_dur = _clamped
                    recorded_duration = (
                        max(current_dur, _last_recording_wallclock)
                        if _last_recording_wallclock > 0
                        else current_dur
                    )
                state['video_path'] = video_path or ''
                state['current_dur'] = current_dur
                state['recorded_duration'] = recorded_duration
                if is_still_recording:
                    _recording_was_active = True
                    _recording_stop_ticks = 0
                    if _file_switch_cooldown > 0:
                        _file_switch_cooldown -= 1
                elif _recording_was_active:
                    # 文件切换冷却期内不递增停止计数器，避免误判重连为录制停止
                    if _file_switch_cooldown > 0:
                        _recording_stop_ticks = 0
                    else:
                        # 必须在压力让路之前递增：critical 时若先 continue，收尾永远触发不了。
                        _recording_stop_ticks += 1
                    if _recording_stop_ticks >= 2 and not _finalize_started:
                        _finalize_pending = True
                        _log.info("持续分析收尾: 录制已停止，触发最终完整扫描 room_id=%s", room_id)
                        try:
                            await _flush_deferred_exports(force=True)
                        except Exception as exc:
                            _log.debug("停录后冲刷延后导出失败: %s", exc)

                if _finalize_started or _finalize_pending:
                    state['analysis_stage'] = '收尾中'
                elif state.get('scan_running'):
                    state['analysis_stage'] = (
                        '降级追赶'
                        if state.get('degraded_mode') == 'audio_only'
                        else '扫描中'
                    )
                elif is_still_recording and not video_path:
                    state['analysis_stage'] = '等待可分析片段'
                elif is_still_recording:
                    state['analysis_stage'] = '等待新片段'
                else:
                    # 录制已停、收尾尚未触发：提示用户正在等待收尾，而非「等待新录制」
                    state['analysis_stage'] = '等待收尾'

                worker_completed_at = scan_result.get('completed_at', 0.0)
                worker_dur = scan_result.get('current_dur', 0.0)
                worker_result = scan_result.get('result', [])
                worker_error = scan_result.get('error')
                _boundary_refine_pass = bool(scan_result.get('boundary_refine_pass'))
                _time_since_last_consume = time.time() - last_consumed_at
                _min_consume_interval = 10.0 if _valorant_incremental_rounds else 0.0
                can_consume = (
                    worker_completed_at > last_consumed_at
                    and (
                        worker_dur > last_analyzed + 5.0
                        or _finalize_started
                        or bool(worker_error)
                        or not _valorant_incremental_rounds
                        or _time_since_last_consume > _min_consume_interval
                        or _boundary_refine_pass  # 密扫二次结果立即 upsert，不受 10s 消费间隔限制
                    )
                )
                if can_consume and _boundary_refine_pass:
                    scan_result['boundary_refine_pass'] = False
                if can_consume and worker_error:
                    last_consumed_at = worker_completed_at
                    scan_result['error'] = None
                    terminal_model_error = _is_model_contract_error(worker_error)
                    with _analysis_jobs_lock:
                        if room_id in _continuous_tasks:
                            _continuous_tasks[room_id]['last_scan_error'] = worker_error
                            # 结果优先：不做失败退避，下一次 kick 立即重试同一窗口
                            # （skip-kick 因 last_scan_error 已放行同窗重试）。
                            if terminal_model_error:
                                _continuous_tasks[room_id]['analysis_stage'] = '视觉模型不可用'
                                _continuous_tasks[room_id]['completed'] = True
                                _continuous_tasks[room_id]['cancelled'] = True
                                _continuous_tasks[room_id]['terminal_error'] = worker_error
                            # 非收尾扫描超时：结果优先，不做纯音频/关 OCR 降级。
                            # 连续超时达到上限后跳过该窗口（强制推进游标），
                            # 避免同窗无限重试死循环；跳过时保留警告便于排查。
                            if (
                                _is_timeout_scan_error(worker_error)
                                and not (_finalize_started or _finalize_pending)
                            ):
                                _apply_scan_timeout_backoff(_continuous_tasks[room_id])
                                _timeout_failures = int(
                                    _continuous_tasks[room_id].get(
                                        "consecutive_scan_timeouts", 0
                                    ) or 0
                                )
                                if _timeout_failures >= _SCAN_MAX_TIMEOUT_RETRIES:
                                    _skip_to = float(
                                        _continuous_tasks[room_id].get(
                                            "scan_out_sec",
                                            worker_dur or 0.0,
                                        ) or 0.0
                                    )
                                    _log.warning(
                                        "持续分析连续超时 %d 次，跳过该窗口（游标推进到 %.1fs）: room_id=%s",
                                        _timeout_failures, _skip_to, room_id,
                                    )
                                    _continuous_tasks[room_id][
                                        "consecutive_scan_timeouts"
                                    ] = 0
                                    _continuous_tasks[room_id]["last_analyzed"] = max(
                                        float(_continuous_tasks[room_id].get(
                                            "last_analyzed", 0.0) or 0.0),
                                        _skip_to,
                                    )
                    if terminal_model_error:
                        bridge.queue_broadcast({
                            'type': 'continuous_analysis_complete',
                            'data': {
                                'room_id': room_id,
                                'total_highlights': len(all_highlights),
                                'listed_clip_count': len(_continuous_listed_clip_snapshot(state)),
                                'listed_clips': _continuous_listed_clip_snapshot(state),
                                'error': worker_error,
                            },
                        })
                    elif _finalize_started or _finalize_pending:
                        _finalize_failures += 1
                        if _finalize_failures < _finalize_max_attempts:
                            _finalize_started = False
                            _finalize_pending = True
                            with _analysis_jobs_lock:
                                if room_id in _continuous_tasks:
                                    _continuous_tasks[room_id]['finalizing'] = False
                                    _continuous_tasks[room_id]['analysis_stage'] = '收尾中'
                            _log.warning(
                                "持续分析收尾失败，将重试 (%d/%d): room_id=%s, err=%s",
                                _finalize_failures,
                                _finalize_max_attempts,
                                room_id,
                                worker_error,
                            )
                        else:
                            _finalize_pending = False
                            _finalize_started = False
                            _log.error(
                                "持续分析收尾放弃（已重试 %d 次）: room_id=%s, err=%s, 累计 %d 段待确认",
                                _finalize_failures,
                                room_id,
                                worker_error,
                                len(all_highlights),
                            )
                            with _analysis_jobs_lock:
                                if room_id in _continuous_tasks:
                                    _continuous_tasks[room_id]['finalizing'] = False
                                    _continuous_tasks[room_id]['analysis_stage'] = '收尾失败'
                                    _continuous_tasks[room_id]['completed'] = True
                                    _continuous_tasks[room_id]['cancelled'] = True
                                    _continuous_tasks[room_id]['finalize_error'] = worker_error
                            bridge.queue_broadcast({
                                'type': 'continuous_analysis_complete',
                                'data': {
                                    'room_id': room_id,
                                    'total_highlights': len(all_highlights),
                                    'listed_clip_count': len(_continuous_listed_clip_snapshot(state)),
                                    'listed_clips': _continuous_listed_clip_snapshot(state),
                                    'error': worker_error,
                                },
                            })
                    else:
                        _log.warning(
                            "持续分析扫描失败（非收尾）: room_id=%s, err=%s",
                            room_id,
                            worker_error,
                        )
                elif can_consume:
                    last_consumed_at = worker_completed_at
                    with _analysis_jobs_lock:
                        if room_id in _continuous_tasks:
                            _continuous_tasks[room_id]['last_scan_error'] = None
                            _note_successful_scan_after_degrade(_continuous_tasks[room_id])
                    new_hl = _cleanup_segments(list(worker_result))
                    for h in new_hl:
                        h.setdefault("reason", "回合战斗阶段")
                        h.setdefault("speech_score", 0.0)
                        h.setdefault("visual_score", 0.0)
                        h.setdefault("transcript", "")

                    # 状态栏显示本轮最近一个已扫出回合的切片入/出点。仅在确有
                    # 结果时更新，保留上一次有效边界供用户核对产出节奏。
                    if new_hl:
                        latest_detected = new_hl[-1]
                        with _analysis_jobs_lock:
                            task = _continuous_tasks.get(room_id)
                            if task is not None:
                                task['last_detected_in_sec'] = float(latest_detected['start'])
                                task['last_detected_out_sec'] = float(latest_detected['end'])

                    publish_update = False
                    if _valorant_incremental_rounds:
                        # skip_refine_exported: 已成功导出的 clip 不因精修边界变化自动重导
                        # _merge_round_windows 按 round_key 去重，已存在且导出成功的回合不会被覆盖
                        window_rounds = (
                            list(new_hl)
                            if _finalize_started
                            else _drop_open_tail_rounds(new_hl, worker_dur)
                        )
                        full_rounds = _merge_round_windows(all_highlights, window_rounds)
                        publish_update = _round_lists_changed(all_highlights, full_rounds) or bool(window_rounds)
                        new_hl = _new_rounds(all_highlights, full_rounds)
                        all_highlights = full_rounds
                    elif mode == 'scene':
                        all_highlights = _merge_highlights(all_highlights, new_hl)
                        publish_update = bool(new_hl)
                    else:
                        all_highlights = new_hl
                        publish_update = bool(new_hl)

                    retry_pending_exports = _valorant_incremental_rounds and any(
                        _is_auto_exportable_valorant_round(h)
                        and any(
                            f"{rid}:{_valorant_round_key(h)}" not in _exported_clip_ids
                            for rid in target_room_ids
                        )
                        for h in all_highlights
                    )
                    # publish_update 含边界微调（new_hl 可能为空），仍需 list upsert
                    if publish_update or retry_pending_exports:
                        if publish_update and new_hl:
                            await _export_and_broadcast(
                                room_id, main_room_id, target_room_ids, manager,
                                bridge, all_highlights, new_hl,
                                scan_result.get('video_path', ''), mode,
                            )
                        ok, _, main_room_for_map, target_rooms_for_map = _validate_synced_analysis_targets(
                            manager, main_room_id, target_room_ids, wait_for_file=True,
                        )
                        if not ok:
                            # 录制重启会主动清除旧对齐组。副房映射必须停用，但主房
                            # 检测结果仍应正常入列，避免一次重连吞掉整轮分析结果。
                            fallback_main_room = manager.get_room(main_room_id)
                            fallback_path = (
                                getattr(fallback_main_room, "record_output_path", "")
                                if fallback_main_room is not None
                                else ""
                            )
                            if fallback_main_room is not None and fallback_path and os.path.isfile(fallback_path):
                                main_room_for_map = fallback_main_room
                                target_rooms_for_map = [fallback_main_room]
                        if _valorant_incremental_rounds:
                            # 入列：只加入切片列表与时间线（clip_queued），不启动 FFmpeg 导出；
                            # vision_confirmed（真出点）与 pending（伪造出点等确认）分流标记
                            listable_hl = [
                                h for h in all_highlights
                                if _is_listable_ocr_round(h)
                                and any(
                                    f"{rid}:{_valorant_round_key(h)}" not in _exported_clip_ids
                                    for rid in target_room_ids
                                )
                            ]
                            if state and state.get('shadow_mode'):
                                state['shadow_rounds_detected'] = len(all_highlights)
                                state['shadow_listable_rounds'] = len(listable_hl)
                                state['shadow_vision_confirmed'] = len(listable_hl)
                                _log.info(
                                    "Shadow 模式: room_id=%s, 检测 %d 回合, 可入列 %d（跳过 clip_queued）",
                                    room_id,
                                    len(all_highlights),
                                    len(listable_hl),
                                )
                        else:
                            listable_hl = list(new_hl)
                        if (
                            listable_hl
                            and ok
                            and main_room_for_map is not None
                            and target_rooms_for_map
                            and not (state and state.get('shadow_mode'))
                        ):
                            _auto_preset = load_settings().get('appSettings', {}).get('default_export_preset', '')
                            confirmed_hl = [
                                h for h in listable_hl
                                if _is_auto_exportable_valorant_round(h)
                            ]
                            pending_hl = [
                                h for h in listable_hl
                                if not _is_auto_exportable_valorant_round(h)
                            ]
                            # 只入列不导出：list_only=True 仅广播 clip_queued 入切片列表与时间线
                            if confirmed_hl:
                                await _auto_export_highlights(
                                    main_room_for_map, target_rooms_for_map, confirmed_hl,
                                    job_prefix=f"{int(time.time() * 1000)}",
                                    preset_id=_auto_preset,
                                    defer_export=True,
                                    confirm_status='vision_confirmed',
                                    list_only=True,
                                )
                            if pending_hl:
                                await _auto_export_highlights(
                                    main_room_for_map, target_rooms_for_map, pending_hl,
                                    job_prefix=f"{int(time.time() * 1000)}-pending",
                                    preset_id=_auto_preset,
                                    defer_export=True,
                                    confirm_status='pending',
                                    list_only=True,
                                )
                            _log.info(
                                "持续分析入列（仅列表）: room_id=%s, 确认 %d 段, pending %d 段 × %d 房间",
                                room_id, len(confirmed_hl), len(pending_hl), len(target_rooms_for_map),
                            )

                        # 导出成功后，清除"最近检测边界"，避免状态栏持续显示已导出的回合。
                        # 状态栏应只展示"已分析但尚未导出"的入出点；导出完成后应等待下一轮扫描
                        # 产出新的未导出回合，再更新显示。
                        if listable_hl:
                            with _analysis_jobs_lock:
                                _task = _continuous_tasks.get(room_id)
                                if _task is not None:
                                    _task.pop('last_detected_in_sec', None)
                                    _task.pop('last_detected_out_sec', None)

                    # 压力缓解或收尾时冲刷延后导出队列
                    await _flush_deferred_exports(force=_finalize_started)

                    last_analyzed = worker_dur
                    with _analysis_jobs_lock:
                        if room_id in _continuous_tasks:
                            _continuous_tasks[room_id]['last_analyzed'] = last_analyzed
                            _continuous_tasks[room_id]['recorded_duration'] = max(recorded_duration, worker_dur)
                            # 吞吐量跟踪（自适应追赶上限）：媒体秒 / 墙钟秒，保留最近 5 次
                            if mode == 'valorant_round':
                                _scan_in = float(_continuous_tasks[room_id].get('scan_in_sec') or 0.0)
                                _scan_out = float(_continuous_tasks[room_id].get('scan_out_sec') or 0.0)
                                _scan_wall = time.monotonic() - float(
                                    _continuous_tasks[room_id].get('_scan_start_mono') or time.monotonic()
                                )
                                if _scan_out > _scan_in and _scan_wall > 0.0:
                                    _thru = (_scan_out - _scan_in) / _scan_wall
                                    _hist = list(_continuous_tasks[room_id].get('scan_throughput_history') or [])
                                    _hist.append(round(_thru, 3))
                                    _continuous_tasks[room_id]['scan_throughput_history'] = _hist[-5:]
                            confirmed_total = sum(
                                1 for item in all_highlights
                                if _is_auto_exportable_valorant_round(item)
                            )
                            _continuous_tasks[room_id]['confirmed_rounds'] = confirmed_total
                            _continuous_tasks[room_id]['pending_rounds'] = max(0, len(all_highlights) - confirmed_total)
                            _continuous_tasks[room_id]['analysis_stage'] = (
                                '收尾中'
                                if _finalize_started
                                else (
                                    '降级追赶'
                                    if _continuous_tasks[room_id].get('degraded_mode') == 'audio_only'
                                    else '分析中'
                                )
                            )
                            _continuous_tasks[room_id]['highlights'] = all_highlights
                            _continuous_tasks[room_id]['result_ready'] = False
                            _continuous_tasks[room_id]['full_rescan'] = False

                    _log.info("持续分析增量: room_id=%s, mode=%s, 新增 %d 段, 累计 %d 段 (已分析到 %.1fs)",
                              room_id, mode, len(new_hl), len(all_highlights), worker_dur)

                    if _finalize_started and _finalize_pending and worker_dur <= last_analyzed + 5.0:
                        _finalize_pending = False
                        _finalize_failures = 0
                        _log.info("持续分析收尾完成: room_id=%s, 累计 %d 段", room_id, len(all_highlights))
                        bridge.queue_broadcast({
                            'type': 'continuous_analysis_complete',
                            'data': {
                                'room_id': room_id,
                                'total_highlights': len(all_highlights),
                                'listed_clip_count': len(_continuous_listed_clip_snapshot(state)),
                                'listed_clips': _continuous_listed_clip_snapshot(state),
                            },
                        })
                        with _analysis_jobs_lock:
                            if room_id in _continuous_tasks:
                                _continuous_tasks[room_id]['completed'] = True
                                _continuous_tasks[room_id]['finalizing'] = False
                                _continuous_tasks[room_id]['cancelled'] = True

                with _analysis_jobs_lock:
                    state = _continuous_tasks.get(room_id)
                if not state or state.get('cancelled'):
                    if state:
                        state['status'] = 'stopping'
                        state['analysis_stage'] = '停止中'
                        state['scan_abort'] = True
                        if state.get('scan_running'):
                            try:
                                await asyncio.wait_for(scan_done_event.wait(), timeout=_SCAN_ABORT_GRACE_SEC)
                            except asyncio.TimeoutError:
                                _log.warning("停止等待扫描超时: room_id=%s", room_id)
                    break
                if state.get('stop_requested') and not (_finalize_pending or _finalize_started):
                    # 已停录请求收尾停止：等待收尾自然完成；收尾无法进行时强制退出
                    if _recording_was_active and video_path:
                        _finalize_pending = True
                        _log.info("持续分析立即触发收尾（stop_requested）: room_id=%s", room_id)
                    else:
                        state['cancelled'] = True
                        state['scan_abort'] = True
                        state['status'] = 'stopping'
                        state['analysis_stage'] = '停止中'
                        continue

                # 压力让路：在 worker 结果消费之后执行，避免 skipped-sleep + 不消费
                # 组合成忙循环（completed_at 永远 > last_consumed_at → 每 tick 跳过 sleep）。
                # 让路只决定「是否 kick 新扫描」，不阻止消费已完成结果。
                # 极端压力 pause 时：若已落后 >90s，仍降级追赶，避免永久饿死确认/收尾。
                _pressure_behind = float(recorded_duration or 0.0) > float(last_analyzed) + 90.0
                if (
                    skip_for_pressure
                    and not (_finalize_pending or _finalize_started)
                    and not _pressure_behind
                ):
                    bridge.queue_broadcast({
                        'type': 'continuous_analysis_status',
                        'data': {
                            'running': True,
                            'room_id': room_id,
                            'target_room_ids': target_room_ids,
                            'mode': mode,
                            'analyzed_duration': last_analyzed,
                            'recorded_duration': state.get('recorded_duration', current_dur),
                            'confirmed_rounds': state.get('confirmed_rounds', 0),
                            'pending_rounds': state.get('pending_rounds', 0),
                            'analysis_stage': state.get('analysis_stage', '分析中'),
                            'total_highlights': len(all_highlights),
                            'phase': 'finalizing' if _finalize_started else 'running',
                            'updated_at': time.time(),
                            'scan_mode': 'incremental' if _valorant_incremental_rounds else 'full',
                            'scan_range': [max(0.0, current_dur - 1.0), current_dur] if current_dur else [0.0, 0.0],
                            'scan_timeout': state.get('scan_timeout', 120),
                            'full_rescan': bool(state.get('full_rescan', False)),
                            'refine_with_ocr': bool(state.get('refine_with_ocr', False)),
                            'progress': min(100.0, max(0.0, (last_analyzed / max(current_dur, 1.0)) * 100.0)) if current_dur else 0.0,
                            'scan_phase': state.get('scan_phase'),
                            'scan_reason': state.get('scan_reason'),
                            'effective_interval': effective_interval,
                            'scan_elapsed_sec': round(time.monotonic() - state.get('_scan_start_mono', time.monotonic()), 1) if state.get('scan_running') else 0,
                            'scan_running': state.get('scan_running', False),
                        },
                    })
                    _log.info("持续分析让路: room_id=%s, pressure=%s", room_id, pressure.get("level"))
                    continue
                if skip_for_pressure and _pressure_behind and not (_finalize_pending or _finalize_started):
                    _log.info(
                        "持续分析压力降级追赶: room_id=%s, pressure=%s, behind=%.0fs",
                        room_id,
                        pressure.get("level"),
                        float(recorded_duration or 0.0) - float(last_analyzed),
                    )

                if video_path:
                    should_kick = False
                    if _finalize_pending and not _finalize_started:
                        should_kick = True
                        _finalize_started = True
                        with _analysis_jobs_lock:
                            if room_id in _continuous_tasks:
                                _continuous_tasks[room_id]['finalizing'] = True
                    elif _valorant_incremental_rounds:
                        # 文件时长可能滞后于墙钟录制时长；用二者较大值决定是否 kick
                        kick_dur = max(current_dur, float(state.get('recorded_duration', 0.0) or 0.0))
                        should_kick = kick_dur > last_analyzed + _VALORANT_KICK_AHEAD_SEC
                    elif current_dur > last_analyzed + 12.0:
                        should_kick = True

                    # 写缓冲期内 current_dur 仍为 0/极短：禁止 kick（避免 range=0-0 空扫烧 OCR）
                    if (
                        should_kick
                        and not (_finalize_pending or _finalize_started)
                        and current_dur <= 3.0
                    ):
                        should_kick = False

                    # 失败退避：worker 出错后 30s 内不重试（收尾除外），
                    # 避免「失败 → 立即重 kick → 再失败」的 3s 风暴。
                    _last_err_at = float(state.get('last_scan_error_at') or 0.0)
                    if (
                        should_kick
                        and _last_err_at
                        and not (_finalize_pending or _finalize_started)
                        and time.time() - _last_err_at < _SCAN_ERROR_BACKOFF_SEC
                    ):
                        should_kick = False
                        _log.debug(
                            "持续分析失败退避: room_id=%s, 距上次错误 %.0fs < %ds",
                            room_id,
                            time.time() - _last_err_at,
                            _SCAN_ERROR_BACKOFF_SEC,
                        )

                    if should_kick and not state.get('scan_running'):
                        state['last_progress_broadcast_at'] = time.time()
                        _scan_counter += 1

                        # 扫描预算：固定增量窗口（回看 30s + 追赶），纯 OCR 恒开
                        from lsc.analyzer.registry import get as get_analyzer
                        _analyzer = get_analyzer(game)
                        if _analyzer.capabilities().realtime_continuous:
                            _plan_state = {
                                'mode': mode,
                                'last_analyzed': last_analyzed,
                                'tick_count': _scan_counter,
                                # 吞吐历史 + kick 间隔：自适应追赶上限输入
                                'throughput_history': list(state.get('scan_throughput_history') or []),
                                'kick_interval': effective_interval,
                            }
                            _window = _analyzer.plan_scan_window(
                                _plan_state, current_dur, pressure or {},
                            )
                            scan_range = (_window.start_sec, _window.end_sec)
                            use_ocr_this_tick = bool(_window.use_ocr)
                            _scan_timeout = int(_window.timeout_sec)
                            full_rescan = bool(_plan_state.get('full_rescan', last_analyzed <= 0.0))
                        else:
                            scan_range, use_ocr_this_tick, _scan_timeout, full_rescan = _continuous_valorant_scan_budget(
                                mode, last_analyzed, current_dur, pressure,
                            )
                        # 停录收尾：从游标继续处理尾部（保留重叠），禁止默认全文件重扫
                        if _finalize_started or _finalize_pending:
                            scan_range = (
                                max(0.0, float(last_analyzed) - _OCR_FINALIZE_OVERLAP_SEC),
                                float(current_dur),
                            )
                            use_ocr_this_tick = True
                            full_rescan = False
                            _scan_timeout = max(
                                _scan_timeout,
                                _finalize_scan_timeout(current_dur, attempt=_finalize_failures + 1),
                            )
                            # 重置 FSM 游标，允许重叠区域的帧被重新处理；
                            # 否则 last_processed_ts 会过滤掉回看窗口内的所有帧，
                            # 导致收尾扫描实际只处理极少新帧，几乎不产出切片。
                            _rs = state.get('ocr_runtime_state')
                            if _rs is not None:
                                _rs['last_processed_ts'] = max(
                                    0.0,
                                    float(last_analyzed) - _OCR_FINALIZE_OVERLAP_SEC,
                                )
                        else:
                            use_ocr_this_tick, scan_range = _apply_scan_budget_degrade(
                                state,
                                scan_range=scan_range,
                                last_analyzed=last_analyzed,
                                use_ocr=use_ocr_this_tick,
                            )
                            # 纯 OCR 粗扫的锚点读取（OCR）每帧执行；降级期外必须给足
                            # 覆盖双区域 OCR 的开销，避免超时→空窗。
                            _ocr_anchor_active = not (
                                int(state.get('ocr_degraded_remaining') or 0) > 0
                            )
                            _scan_timeout = _window_scan_timeout(
                                max(1.0, float(scan_range[1]) - float(scan_range[0])),
                                use_ocr=_ocr_anchor_active,
                            )
                        if _should_skip_continuous_scan_kick(
                            state,
                            scan_range,
                            full_rescan=full_rescan,
                            use_ocr=use_ocr_this_tick,
                            finalize=bool(_finalize_started or _finalize_pending),
                        ):
                            continue
                        state['video_path'] = video_path
                        state['current_dur'] = current_dur
                        state['refine_with_ocr'] = use_ocr_this_tick
                        try:
                            _budget_ocr_iv = float(
                                pressure.get('ocr_sample_interval', 1.0) or 1.0
                            )
                        except (TypeError, ValueError):
                            _budget_ocr_iv = 1.0
                        if _finalize_started or _finalize_pending:
                            _budget_ocr_iv = min(_budget_ocr_iv, 1.0)
                        state['ocr_sample_interval'] = _budget_ocr_iv
                        # 质量优先：不再在 critical 奇数 tick 关掉 OCR
                        state['refine_with_ocr'] = use_ocr_this_tick
                        state['scan_range'] = scan_range
                        state['scan_in_sec'] = float(scan_range[0])
                        state['scan_out_sec'] = float(scan_range[1])
                        state['full_rescan'] = full_rescan
                        state['scan_timeout'] = _scan_timeout
                        state['scan_requested'] = True
                        state['scan_phase'] = 'full' if full_rescan else 'incremental'
                        state['scan_reason'] = 'finalize' if _finalize_started else 'audio_increment'

                        bridge.queue_broadcast({
                            'type': 'continuous_analysis_status',
                            'data': {
                                'running': True,
                                'room_id': room_id,
                                'target_room_ids': target_room_ids,
                                'mode': mode,
                                'analyzed_duration': last_analyzed,
                                'recorded_duration': state.get('recorded_duration', current_dur),
                                'confirmed_rounds': state.get('confirmed_rounds', 0),
                                'pending_rounds': state.get('pending_rounds', 0),
                                'analysis_stage': state.get('analysis_stage', '分析中'),
                                'total_highlights': len(all_highlights),
                                'phase': 'stopping' if state.get('stop_requested') else ('finalizing' if _finalize_started else 'running'),
                                'updated_at': time.time(),
                                'scan_mode': 'full' if full_rescan else 'incremental',
                                'scan_range': [scan_range[0], scan_range[1]],
                                'scan_timeout': _scan_timeout,
                                'full_rescan': full_rescan,
                                'refine_with_ocr': use_ocr_this_tick,
                                'progress': min(100.0, max(0.0, (last_analyzed / max(scan_range[1], 1.0)) * 100.0)),
                                'scan_phase': 'full' if full_rescan else 'incremental',
                                'scan_reason': 'finalize' if _finalize_started else 'audio_increment',
                                'effective_interval': effective_interval,
                                'scan_elapsed_sec': round(time.monotonic() - state.get('_scan_start_mono', time.monotonic()), 1) if state.get('scan_running') else 0,
                                'scan_running': state.get('scan_running', False),
                                'valorant_profile': state.get('valorant_profile'),
                            },
                        })

                        if not state.get('scan_running'):
                            _log.info(f"持续分析 kick worker: room_id={room_id}, dur={current_dur:.0f}s, range={scan_range[0]:.0f}-{scan_range[1]:.0f}, OCR={use_ocr_this_tick}, full={full_rescan}, finalize={_finalize_started}")

                # 每 tick 广播状态（含等待中），避免 UI 卡在 analyzed_duration /「等待新片段」
                # 高频 tick 广播不带全量 listed_clips（前端不消费，切片列表由 clip_queued 驱动）
                bridge.queue_broadcast({
                    'type': 'continuous_analysis_status',
                    'data': _build_continuous_status_payload(
                        state,
                        room_id=room_id,
                        recorded_duration=float(state.get('recorded_duration', current_dur) or 0.0),
                        analysis_stage=state.get('analysis_stage', '分析中'),
                        phase='stopping' if state.get('stop_requested') else ('finalizing' if _finalize_started else 'running'),
                        all_highlights=all_highlights,
                        last_analyzed=last_analyzed,
                        current_dur=current_dur,
                        include_listed=False,
                        effective_interval=effective_interval,
                    ),
                })

                # 录制停止收尾已在本 tick 前半段触发（避免 pressure continue 饿死）。
                # 这里仅处理「收尾中又重新开录」的取消。
                room_obj = manager.get_room(room_id)
                is_still_recording = bool(room_obj and getattr(room_obj, 'is_recording', False))
                if is_still_recording and (_finalize_pending or _finalize_started):
                    _finalize_pending = False
                    _finalize_started = False
                    _finalize_failures = 0
                    _recording_stop_ticks = 0
                    with _analysis_jobs_lock:
                        if room_id in _continuous_tasks:
                            _continuous_tasks[room_id]['finalizing'] = False
                    _log.info("持续分析收尾取消: 录制已恢复 room_id=%s", room_id)


        except asyncio.CancelledError:
            pass
        except Exception as exc:
            _log.error("持续分析异常: room_id=%s, %s", room_id, exc, exc_info=True)
            # 立即标记任务为异常停止，避免清理期间 status handler 仍报告 running
            with _analysis_jobs_lock:
                if room_id in _continuous_tasks:
                    _continuous_tasks[room_id]['status'] = 'stopping'
                    _continuous_tasks[room_id]['cancelled'] = True
                    _continuous_tasks[room_id]['scan_abort'] = True
                    _continuous_tasks[room_id]['analysis_stage'] = '异常退出'
        finally:
            with _analysis_jobs_lock:
                stop_state = _continuous_tasks.get(room_id, {})
            if stop_state.get('status') == 'stopping' or stop_state.get('cancelled'):
                with _analysis_jobs_lock:
                    if room_id in _continuous_tasks:
                        _continuous_tasks[room_id]['scan_abort'] = True
            # 让 worker 通过 cancel_check 自行退出；不得 cancel 协程后提前释放 semaphore。
            if _worker_task and not _worker_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(_worker_task),
                        timeout=_SCAN_ABORT_GRACE_SEC,
                    )
                except asyncio.TimeoutError:
                    _log.warning(
                        "worker 未在 %.1fs 内退出，继续等待至硬上限 %.1fs: room_id=%s",
                        _SCAN_ABORT_GRACE_SEC,
                        _SCAN_ABORT_HARD_SEC,
                        room_id,
                    )
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(_worker_task),
                            timeout=_SCAN_ABORT_HARD_SEC,
                        )
                    except asyncio.TimeoutError:
                        _log.error(
                            "worker 硬超时仍未退出，强制释放任务槽: room_id=%s",
                            room_id,
                        )
                except asyncio.CancelledError:
                    pass
            # 终态广播：按任务结束原因分流，避免模型故障/收尾失败被当成成功/idle
            _terminal_err = stop_state.get('terminal_error')
            _finalize_err = stop_state.get('finalize_error')
            _base_terminal = {
                'running': False,
                'room_id': room_id,
                'updated_at': time.time(),
            }
            if _terminal_err:
                bridge.queue_broadcast({
                    'type': 'continuous_analysis_status',
                    'data': {
                        **_base_terminal,
                        'phase': 'error',
                        'status': 'error',
                        'error': _terminal_err,
                        'analysis_stage': '视觉模型不可用',
                        'total_highlights': len(all_highlights),
                    },
                })
            elif _finalize_err:
                bridge.queue_broadcast({
                    'type': 'continuous_analysis_status',
                    'data': {
                        **_base_terminal,
                        'phase': 'error',
                        'status': 'error',
                        'error': _finalize_err,
                        'analysis_stage': '收尾失败',
                        'total_highlights': len(all_highlights),
                    },
                })
            elif stop_state.get('completed'):
                bridge.queue_broadcast({
                    'type': 'continuous_analysis_status',
                    'data': {
                        **_base_terminal,
                        'phase': 'completed',
                        'status': 'completed',
                        'analysis_stage': '已完成',
                        'total_highlights': len(all_highlights),
                    },
                })
            else:
                bridge.queue_broadcast({
                    'type': 'continuous_analysis_status',
                    'data': {
                        **_base_terminal,
                        'phase': 'idle',
                        'status': 'idle',
                    },
                })
            with _analysis_jobs_lock:
                _continuous_tasks.pop(room_id, None)
            _log.info("持续分析已停止: room_id=%s, 累计 %d 段高光", room_id, len(all_highlights))

    async def _export_and_broadcast(
        room_id, main_room_id, target_room_ids, manager, bridge,
        all_highlights, new_hl, video_path, mode,
    ) -> None:
        """导出 + 广播（公共逻辑，从原 loop 提取）"""
        mapped_highlights_by_room: dict[str, list[dict[str, Any]]] = {}
        mapping_fallback = False
        mapping_error = ""
        ok, error, main_room_for_map, target_rooms_for_map = _validate_synced_analysis_targets(
            manager, main_room_id, target_room_ids, wait_for_file=True,
        )
        if ok and main_room_for_map is not None:
            mapped_highlights_by_room = _map_highlights_by_room(
                all_highlights, main_room_for_map, target_rooms_for_map,
            )
            # 映射成功，清除错误（P3: 多房间同步详细错误）
            with _analysis_jobs_lock:
                if room_id in _continuous_tasks:
                    _continuous_tasks[room_id].pop('mapping_error', None)
        else:
            fallback_main_room = manager.get_room(main_room_id)
            fallback_path = (
                getattr(fallback_main_room, "record_output_path", "")
                if fallback_main_room is not None
                else ""
            )
            if fallback_main_room is not None and fallback_path and os.path.isfile(fallback_path):
                main_room_for_map = fallback_main_room
                target_rooms_for_map = [fallback_main_room]
        if not ok and main_room_for_map is not None:
            _log.warning("持续分析同步映射回退到主房间: %s", error)
            mapping_fallback = True
            mapping_error = error or "同步映射校验失败"
            mapped_highlights_by_room = _map_highlights_by_room(
                all_highlights, main_room_for_map, [main_room_for_map],
            )
            # 保存详细错误到 task state（P3: 多房间同步详细错误）
            with _analysis_jobs_lock:
                if room_id in _continuous_tasks:
                    _continuous_tasks[room_id]['mapping_error'] = mapping_error
        for idx, hl in enumerate(new_hl):
            bridge.queue_broadcast({
                'type': 'highlight_stream',
                'data': {
                    'room_id': room_id, 'main_room_id': main_room_id,
                    'highlight': hl, 'index': idx,
                    'total_in_round': len(new_hl), 'round_total': len(all_highlights),
                },
            })
        try:
            bridge.queue_broadcast({
                'type': 'continuous_highlights',
                'data': {
                    'room_id': room_id, 'main_room_id': main_room_id,
                    'target_room_ids': target_room_ids,
                    'highlights': all_highlights, 'new_count': len(new_hl),
                    'total': len(all_highlights),
                    'mapped_highlights_by_room': mapped_highlights_by_room,
                    'mapping_fallback': mapping_fallback,
                    'error': mapping_error if mapping_fallback else None,
                },
            })
        except Exception as exc:
            _log.warning("广播持续分析高光失败: %s", exc)

        # 非 Valorant 回合（scene/generic）：与 Valorant 一致，仅 list_only 入列待确认，
        # 禁止 defer→flush 自动 FFmpeg 导出（用户须确认后再导出）。
        if mode != 'valorant_round':
            try:
                if mapped_highlights_by_room and main_room_for_map is not None and target_rooms_for_map:
                    _auto_preset_generic = load_settings().get('appSettings', {}).get('default_export_preset', '')
                    for target_rid, hls in mapped_highlights_by_room.items():
                        if not hls:
                            continue
                        target_room = next(
                            (r for r in target_rooms_for_map if getattr(r, 'room_id', '') == target_rid),
                            None,
                        )
                        if target_room:
                            await _auto_export_highlights(
                                main_room_for_map,
                                [target_room],
                                hls,
                                job_prefix=f"auto-{int(time.time() * 1000)}",
                                preset_id=_auto_preset_generic,
                                defer_export=True,
                                confirm_status='pending',
                                list_only=True,
                            )
            except Exception as exc:
                _log.warning("持续分析入列失败: %s", exc)

        if video_path:
            with _analysis_jobs_lock:
                stop_state = _continuous_tasks.get(room_id, {})
            _t0 = float(
                stop_state.get("_session_t0")
                or stop_state.get("_scan_start_mono")
                or time.monotonic()
            )
            save_analysis_results(
                video_path, room_id, mode, all_highlights,
                analysis_time_sec=max(0.0, time.monotonic() - _t0),
            )

    @server.on('start_continuous_analysis')
    async def handle_start_continuous_analysis(data):
        """启动持续分析（边录边分析）。

        参数:
            main_room_id / room_id: 主房间 ID（只分析该房录制文件）
            target_room_ids: 映射入列的目标房间（须含主房；多房须同对齐组）
            mode: 'valorant_round' | 'scene'
            game: 'valorant' | 'generic'
            interval: 增量分析间隔（秒，默认 60，最小 10）
            threshold: 场景检测阈值（默认 0.3）
            valorant_profile: 遗留字段；pov/broadcast/hvv 等均映射到统一 valorant 档
        """
        data = data or {}
        main_room_id = data.get('main_room_id') or data.get('room_id')
        target_room_ids = data.get('target_room_ids') or [main_room_id]
        mode = data.get('mode', 'scene')
        interval = int(data.get('interval', 60))
        threshold = _safe_float(data.get('threshold', 0.3), 0.3)
        game = data.get('game', 'valorant')  # 'valorant' | 'generic'
        # 统一保守档；旧客户端传 pov/broadcast 仍兼容
        _start_valorant_profile = (data.get('valorant_profile') or 'valorant')
        if not main_room_id:
            return {'error': 'room_id is required'}

        # 持续分析是边录边分析，不允许用历史文件或未录制房间占用任务槽。
        # 先做轻量状态快照；后续仍由同步目标校验函数校验文件与对齐。
        requested_target_ids = [str(room_id) for room_id in target_room_ids if room_id]
        if main_room_id not in requested_target_ids:
            requested_target_ids.insert(0, main_room_id)
        def _recording_state(room_id: str) -> dict[str, Any]:
            get_status = getattr(manager, 'get_recording_status', None)
            if callable(get_status):
                return get_status(room_id)  # type: ignore[no-any-return]
            room = manager.get_room(room_id)
            return {
                'exists': room is not None,
                'is_recording': bool(room is not None and getattr(room, 'is_recording', False)),
            }

        recording_states = await asyncio.get_running_loop().run_in_executor(
            _bridge_executor,
            lambda: {
                room_id: _recording_state(room_id)
                for room_id in requested_target_ids
            },
        )
        inactive_room_ids = [
            room_id for room_id, status in recording_states.items()
            if not status.get('exists') or not status.get('is_recording')
        ]
        if inactive_room_ids:
            if main_room_id in inactive_room_ids:
                return {'success': False, 'error': '主直播间尚未开始录制，无法启动持续分析'}
            return {
                'success': False,
                'error': '目标房间尚未开始录制，无法启动持续分析',
                'inactive_room_ids': inactive_room_ids,
            }
        target_room_ids = requested_target_ids
        with _analysis_jobs_lock:
            if _continuous_tasks:
                active_room_id = next(iter(_continuous_tasks))
                active_state = _continuous_tasks.get(active_room_id) or {}
                if active_state.get('status') == 'stopping' or active_state.get('cancelled'):
                    return {
                        'success': False,
                        'error': '持续分析正在停止，请稍后再试',
                        'active_room_id': active_room_id,
                        'phase': 'stopping',
                    }
                return {
                    'success': False,
                    'error': '已有持续分析任务正在运行',
                    'active_room_id': active_room_id,
                }
            # 占位防双启动：validate 含磁盘等待（每房最多 8s），期间其他连接的
            # 启动请求会在上方看到任务槽非空而被拒绝。
            _continuous_tasks[main_room_id] = {
                'status': 'starting',
                'cancelled': False,
                'main_room_id': main_room_id,
                'target_room_ids': list(target_room_ids or []),
            }
        if mode == 'valorant_round' and game == 'valorant':
            interval = 5
        elif interval < 10:
            interval = 10  # 最小 10s，避免过于频繁

        def _discard_starting_placeholder() -> None:
            with _analysis_jobs_lock:
                if (_continuous_tasks.get(main_room_id) or {}).get('status') == 'starting':
                    _continuous_tasks.pop(main_room_id, None)

        # wait_for_file 内含 time.sleep，必须移出 asyncio event loop
        try:
            ok, error, main_room, target_rooms = await asyncio.get_running_loop().run_in_executor(
                _bridge_executor,
                lambda: _validate_synced_analysis_targets(
                    manager,
                    main_room_id,
                    target_room_ids,
                    wait_for_file=True,
                ),
            )
        except Exception:
            _discard_starting_placeholder()
            raise
        if not ok:
            _discard_starting_placeholder()
            return {'success': False, 'error': error}
        with _analysis_jobs_lock:
            _placeholder = _continuous_tasks.get(main_room_id) or {}
            _start_aborted = bool(
                _placeholder.get('cancelled') or _placeholder.get('status') == 'stopping'
            )
        if _start_aborted:
            # validate 期间用户已请求停止：尊重取消，不得继续创建分析循环
            with _analysis_jobs_lock:
                _continuous_tasks.pop(main_room_id, None)
            _log.info("持续分析启动被中止（validate 期间收到停止）: main_room_id=%s", main_room_id)
            return {'success': False, 'error': '持续分析已在启动前被取消', 'cancelled': True}
        resolved_target_room_ids = [
            getattr(room, "room_id", "") for room in target_rooms if getattr(room, "room_id", "")
        ]

        task = asyncio.create_task(_continuous_analysis_loop(
            main_room_id, resolved_target_room_ids, interval, threshold, mode, game,
            valorant_profile=_start_valorant_profile,
        ))
        with _analysis_jobs_lock:
            _continuous_tasks[main_room_id] = {
                'task': task,
                'last_analyzed': 0.0,
                'highlights': [],
                'cancelled': False,
                'completed': False,
                'finalizing': False,
                'mode': mode,
                'main_room_id': main_room_id,
                'target_room_ids': resolved_target_room_ids,
                'recorded_duration': 0.0,
                'confirmed_rounds': 0,
                'pending_rounds': 0,
                'analysis_stage': '等待新录制',
                'session_id': uuid4().hex,
                '_session_t0': time.monotonic(),
            }
        _log.info(
            "持续分析已启动: main_room_id=%s, targets=%s, mode=%s, interval=%ds, threshold=%.2f",
            main_room_id,
            resolved_target_room_ids,
            mode,
            interval,
            threshold,
        )
        bridge.queue_broadcast({
            'type': 'continuous_analysis_status',
            'data': {
                'running': True,
                'room_id': main_room_id,
                'target_room_ids': resolved_target_room_ids,
                'mode': mode,
                'analyzed_duration': 0.0,
                'total_highlights': 0,
                'recorded_duration': 0.0,
                'confirmed_rounds': 0,
                'pending_rounds': 0,
                'analysis_stage': '等待新录制',
                'phase': 'running',
                'updated_at': time.time(),
                'scan_mode': 'full',
                'scan_range': [0.0, 0.0],
                'scan_timeout': 120,
                'full_rescan': True,
                'refine_with_ocr': False,
            },
        })
        return {
            'success': True,
            'message': f'持续分析已启动（{mode} 模式，间隔 {interval}s）',
            'main_room_id': main_room_id,
            'target_room_ids': resolved_target_room_ids,
            'mode': mode,
        }

    @server.on('stop_continuous_analysis')
    async def handle_stop_continuous_analysis(data):
        """停止持续分析。"""
        data = data or {}
        requested_room_id = data.get('main_room_id') or data.get('room_id')
        room_id = requested_room_id
        with _analysis_jobs_lock:  # 保持现有锁保护
            if not room_id and len(_continuous_tasks) == 1:
                room_id = next(iter(_continuous_tasks))
            if room_id and room_id not in _continuous_tasks:
                for active_room_id, active_state in list(_continuous_tasks.items()):
                    active_targets = active_state.get('target_room_ids') or []
                    if room_id == active_state.get('main_room_id') or room_id in active_targets:
                        room_id = active_room_id
                        break
            state = _continuous_tasks.get(room_id)
            if state is not None:
                room = manager.get_room(room_id)
                is_recording = bool(room is not None and getattr(room, 'is_recording', False))
                finalizing = bool(state.get('finalizing'))
                ever_recorded = float(state.get('recorded_duration') or 0.0) > 0.0
                if finalizing or (not is_recording and ever_recorded):
                    # 已停录/正在收尾：尊重收尾，尾部回合不丢；收尾完成后任务自然退出
                    state['stop_requested'] = True
                    state['status'] = 'stopping'
                    state['analysis_stage'] = '停止中（等待收尾）'
                else:
                    state['cancelled'] = True
                    state['scan_abort'] = True
                    state['status'] = 'stopping'
                    state['analysis_stage'] = '停止中'
                done_event = state.get('scan_done_event')
            else:
                done_event = None
        if not room_id:
            return {'error': 'room_id is required'}
        if not state:
            return {'success': False, 'error': '该房间没有持续分析任务'}
        if done_event is not None and not state.get('scan_running'):
            done_event.set()
        # running=False：前端按钮可离开「分析中」；phase=stopping：禁止立刻再启动，
        # 等 finally 广播 idle 并 pop 任务槽后再允许启动。
        bridge.queue_broadcast({
            'type': 'continuous_analysis_status',
            'data': {
                'running': False,
                'phase': 'stopping',
                'status': 'stopping',
                'room_id': room_id,
                'analysis_stage': '停止中',
                'updated_at': time.time(),
            },
        })
        _log.info("持续分析停止请求：room_id=%s", room_id)
        return {
            'success': True,
            'status': 'stopping',
            'phase': 'stopping',
            'room_id': room_id,
            'requested_room_id': requested_room_id,
        }

    @server.on('get_continuous_analysis_status')
    async def handle_get_continuous_analysis_status(data):
        """查询当前是否有正在运行的持续分析任务。"""
        with _analysis_jobs_lock:
            _ct_items = list(_continuous_tasks.items())
        if _ct_items:
            active_room_id, task = _ct_items[0]
            room = manager.get_room(active_room_id)
            recorded_duration = float(task.get('recorded_duration', task.get('last_analyzed', 0.0)) or 0.0)
            if room is not None and getattr(room, 'is_recording', False):
                started = float(getattr(room, 'recording_start_mono', 0.0) or 0.0)
                if started:
                    recorded_duration = max(recorded_duration, time.monotonic() - started)
            analysis_stage = task.get('analysis_stage', '分析中')
            if room is not None and getattr(room, 'is_recording', False) and analysis_stage == '等待新录制':
                analysis_stage = '等待可分析片段'
            if task.get('status') == 'stopping' or (
                task.get('cancelled') and not task.get('completed') and not task.get('finalizing')
            ):
                phase = 'stopping'
            elif task.get('completed'):
                phase = 'completed'
            elif task.get('finalizing'):
                phase = 'finalizing'
            else:
                phase = 'running'
            return _build_continuous_status_payload(
                task,
                room_id=active_room_id,
                recorded_duration=recorded_duration,
                analysis_stage=analysis_stage,
                phase=phase,
            )
        return {'running': False, 'phase': 'idle', 'updated_at': time.time()}


    # ── 切片精修 handlers ──────────────────────────────────────────
    _clip_refine_state: dict[str, dict[str, Any]] = {}  # { round_key: { status, start, end, room_ids } }

    @server.on('begin_refine_clip')
    async def handle_begin_refine_clip(data):
        """用户点击 pending 切片进入精修：冻结 round_key，广播 refining 状态。"""
        room_id = data.get('room_id', '')
        round_key = data.get('round_key', '') or data.get('clip_id', '')
        if not round_key:
            _log.warning("begin_refine_clip: 缺少 round_key")
            return {'success': False, 'error': 'missing round_key'}
        # 冻结：OCR 不得再改该 round_key 的边界
        with _refined_round_keys_lock:
            _refined_round_keys.add(round_key)
        _clip_refine_state[round_key] = {
            'status': 'refining',
            'room_id': room_id,
            'start': float(data.get('start', 0)),
            'end': float(data.get('end', 0)),
        }
        bridge.queue_broadcast({
            'type': 'clip_confirm_status',
            'data': {
                'room_id': room_id,
                'round_key': round_key,
                'confirm_status': 'refining',
                'start': round(float(data.get('start', 0)), 1),
                'end': round(float(data.get('end', 0)), 1),
            },
        })
        _log.info("精修开始: room=%s, round_key=%s", room_id, round_key)
        return {'success': True, 'round_key': round_key, 'status': 'refining'}

    @server.on('confirm_highlight_clip')
    async def handle_confirm_highlight_clip(data):
        """用户确认精修结果：主房 + 目标房均为 user_confirmed，不自动导出。

        ⚠️ 本 handler 被 handlers.analysis_handlers 的同名注册覆盖
        （register_analysis_handlers 后注册，server.on 覆写），永不触发；
        实际生效版本在 analysis_handlers.py（含 align_group_id 校验）。保留仅防回滚。
        """
        room_id = data.get('room_id', '')
        round_key = data.get('round_key', '') or data.get('clip_id', '')
        start = float(data.get('start', 0))
        end = float(data.get('end', 0))
        target_room_ids = data.get('target_room_ids', [])
        if not round_key:
            _log.warning("confirm_highlight_clip: 缺少 round_key")
            return {'success': False, 'error': 'missing round_key'}
        # 主房确认
        with _refined_round_keys_lock:
            _refined_round_keys.add(round_key)
        _clip_refine_state[round_key] = {
            'status': 'user_confirmed',
            'room_id': room_id,
            'start': start,
            'end': end,
            'target_room_ids': target_room_ids,
        }
        # 广播主房确认
        bridge.queue_broadcast({
            'type': 'clip_confirm_status',
            'data': {
                'room_id': room_id,
                'round_key': round_key,
                'confirm_status': 'user_confirmed',
                'start': round(start, 1),
                'end': round(end, 1),
            },
        })
        # 映射到目标房间（按 content_offset / recording_start 做时间对齐）
        main_room = manager.get_room(room_id) if room_id else None
        for target_rid in target_room_ids:
            if not target_rid or target_rid == room_id:
                continue
            t_start, t_end = start, end
            target_room = manager.get_room(target_rid)
            if main_room is not None and target_room is not None:
                mapped = _map_highlight_to_room(
                    {'start': start, 'end': end}, main_room, target_room,
                )
                t_start = float(mapped.get('start', start))
                t_end = float(mapped.get('end', end))
            if t_end <= t_start:
                continue
            bridge.queue_broadcast({
                'type': 'clip_confirm_status',
                'data': {
                    'room_id': target_rid,
                    'round_key': round_key,
                    'confirm_status': 'user_confirmed',
                    'start': round(t_start, 1),
                    'end': round(t_end, 1),
                },
            })
        _log.info("精修确认: room=%s, round_key=%s, targets=%d, %.1f-%.1f",
                  room_id, round_key, len(target_room_ids), start, end)
        return {
            'success': True,
            'round_key': round_key,
            'status': 'user_confirmed',
            'target_room_ids': target_room_ids,
        }

    @server.on('cancel_refine_clip')
    async def handle_cancel_refine_clip(data):
        """取消精修：恢复 pending，解除 OCR 冻结，丢弃未确认微调。"""
        room_id = data.get('room_id', '')
        round_key = data.get('round_key', '') or data.get('clip_id', '')
        if not round_key:
            _log.warning("cancel_refine_clip: 缺少 round_key")
            return {'success': False, 'error': 'missing round_key'}
        saved = _clip_refine_state.pop(round_key, None)
        if saved and not room_id:
            room_id = saved.get('room_id', '')
        # 未确认的精修取消后允许 OCR 再升格
        with _refined_round_keys_lock:
            _refined_round_keys.discard(round_key)
        broadcast_data: dict = {
            'room_id': room_id,
            'round_key': round_key,
            'confirm_status': 'pending',
        }
        start = saved.get('start') if saved else None
        end = saved.get('end') if saved else None
        if start is None and data.get('start') is not None:
            start = float(data['start'])
        if end is None and data.get('end') is not None:
            end = float(data['end'])
        if start is not None:
            broadcast_data['start'] = round(float(start), 1)
        if end is not None:
            broadcast_data['end'] = round(float(end), 1)
        bridge.queue_broadcast({
            'type': 'clip_confirm_status',
            'data': broadcast_data,
        })
        _log.info("精修取消: room=%s, round_key=%s", room_id, round_key)
        return {'success': True, 'round_key': round_key, 'status': 'pending'}

    @server.on('delete_clip')
    async def handle_delete_clip(data):
        """删除已入列切片：从权威快照移除并记 tombstone，防止 OCR upsert 复活。"""
        room_id = data.get('room_id', '')
        round_key = data.get('round_key', '') or data.get('clip_id', '')
        if not room_id or not round_key:
            return {'success': False, 'error': 'room_id 与 round_key 均不能为空'}
        listed_key = f"{room_id}:{round_key}"
        removed = False
        with _analysis_jobs_lock:
            if listed_key in _listed_clip_ids:
                _listed_clip_ids.pop(listed_key, None)
                removed = True
            _listed_clip_bounds.pop(listed_key, None)
            _bounded_clip_key_add(_deleted_clip_keys, listed_key)
            for task_state in _continuous_tasks.values():
                snapshots = task_state.get("listed_clips")
                if snapshots and listed_key in snapshots:
                    snapshots.pop(listed_key, None)
            for job_state in _analysis_jobs.values():
                snapshots = job_state.get("listed_clips")
                if snapshots and listed_key in snapshots:
                    snapshots.pop(listed_key, None)
        # 解除精修冻结与残留状态，避免引用已删切片
        with _refined_round_keys_lock:
            _refined_round_keys.discard(round_key)
        _clip_refine_state.pop(round_key, None)
        _log.info("删除切片: room=%s, round_key=%s, removed=%s", room_id, round_key, removed)
        return {'success': True, 'removed': removed}

    # ── 职责域子模块注册（拆分自 room_handler，保持 WebSocket 路由不变）──
    from handlers.alignment_handlers import register_alignment_handlers
    register_alignment_handlers(
        server,
        bridge=bridge,
        manager=manager,
        broadcast_rooms=_broadcast_rooms,
        bridge_executor=_bridge_executor,
        recording_executor=_recording_executor,
    )

    from handlers.recording_handlers import register_recording_handlers
    register_recording_handlers(
        server,
        bridge=bridge,
        manager=manager,
        broadcast_rooms=_broadcast_rooms,
        bridge_executor=_bridge_executor,
        recording_executor=_recording_executor,
        recording_semaphore=_recording_semaphore,
        recording_starting=_recording_starting,
        recording_wait_queue=_recording_wait_queue,
        recording_history=recording_history,
        recording_history_lock=_recording_history_lock,
        max_recording_history=_MAX_RECORDING_HISTORY,
        save_recording_history=_save_recording_history,
        load_settings=load_settings,
        expand_user_path=_expand_user_path,
        reattach_shared_preview=_reattach_shared_preview_after_recording_start,
    )

    from handlers.export_handlers import get_queue_export, register_export_handlers
    register_export_handlers(
        server,
        bridge=bridge,
        manager=manager,
        bridge_executor=_bridge_executor,
        load_settings=load_settings,
        expand_user_path=_expand_user_path,
        purge_stale_analysis_jobs=_purge_stale_analysis_jobs,
        ext_export_jobs=export_jobs,
        ext_export_jobs_lock=_export_jobs_lock,
        ext_export_cancelled_jobs=_export_cancelled_jobs,
    )

    # 全局唯一导出入队入口（原 room_handler 旧导出管道已删除，避免双队列并发突破
    # export_max_concurrent 上限）：所有路径（手动 export_clip / export_clip_by_id /
    # AI 自动导出 / 延后导出）统一走 handlers.export_handlers 的队列与 semaphore
    # semaphore 替换仍由 export_handlers 在 _export_queue.empty() 且无在途任务时保护。
    queue_export = get_queue_export()

    from handlers.analysis_handlers import register_analysis_handlers
    register_analysis_handlers(
        server,
        bridge=bridge,
        manager=manager,
        bridge_executor=_bridge_executor,
        ai_executor=_ai_executor,
        load_settings=load_settings,
        safe_float=_safe_float,
        analyze_scene_or_rounds=_analyze_scene_or_rounds,
        validate_synced_analysis_targets=_validate_synced_analysis_targets,
        continuous_analysis_loop=_continuous_analysis_loop,
        auto_export_highlights=_auto_export_highlights,
        build_continuous_status_payload=_build_continuous_status_payload,
        map_highlight_to_room=_map_highlight_to_room,
        recording_media_start=_recording_media_start,
        min_highlight_duration_for_queue=_min_highlight_duration_for_queue,
        valorant_round_key=_valorant_round_key,
        should_broadcast_clip_list_update=_should_broadcast_clip_list_update,
        analysis_jobs=_analysis_jobs,
        analysis_jobs_lock=_analysis_jobs_lock,
        continuous_tasks=_continuous_tasks,
        refined_round_keys=_refined_round_keys,
        refined_round_keys_lock=_refined_round_keys_lock,
    )

    # ── TimelineContext 集成（已抽离至 handlers.timeline_handlers）──
    register_timeline_handlers(server, bridge=bridge, manager=manager, queue_export=queue_export)

    from handlers.jianying_handlers import register_jianying_handlers
    register_jianying_handlers(
        server,
        bridge=bridge,
        manager=manager,
        load_settings=load_settings,
    )

    # ── 录制文件修复 handler（P2-1: 录制文件修复工具）──
    @server.on('repair_recording')
    async def handle_repair_recording(data):
        """修复录制文件（moov atom 不完整）。"""
        room_id = data.get('room_id')
        if not room_id:
            return {'success': False, 'error': 'room_id is required'}

        room = manager.get_room(room_id)
        if room is None:
            return {'success': False, 'error': '房间不存在'}

        path = getattr(room, 'record_output_path', '')
        if not path or not os.path.isfile(path):
            return {'success': False, 'error': '录制文件不存在'}

        from lsc.utils.recording_repair import repair_recording
        repaired = await asyncio.get_running_loop().run_in_executor(
            _recording_executor, lambda: repair_recording(path)
        )
        if repaired:
            return {'success': True, 'path': repaired}
        return {'success': False, 'error': '修复失败，文件可能严重损坏'}

    # 新客户端连接时由 on_connect 推送当前内存房间（不再从磁盘恢复）
