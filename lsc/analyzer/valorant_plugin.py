"""Valorant analyzer plugin: 纯 OCR 回合检测（持续分析 + 文件分析统一）。"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from lsc.analyzer.base import AnalyzerCapabilities, ScanWindow
from lsc.platforms.redaction import redact_text

_log = logging.getLogger(__name__)

# 增量回看：FSM/锚点跨窗口持久化 + last_processed_ts 去重，回看窗只承担 seek 稳定性缓冲。
# 稳态 8s（窗口净吞吐提升）；失败/重连/首窗仍用 30s 兜底。
INCREMENTAL_LOOKBACK_SEC = 30.0
STEADY_LOOKBACK_SEC = 8.0
# 单次增量窗口不能随着 scan_cycle 无限膨胀：吞吐低于 1x 时，
# “窗口越大→耗时越长→cycle 越大→下一窗口更大”的正反馈会发散。
# 追赶应由多个有界窗口完成，收尾阶段再做完整扫描。
MAX_CATCHUP_SEC = 90.0
# 自适应追赶下限：45s ≈ lookback+一回合量级
MIN_CATCHUP_SEC = 45.0


# 赛事审计缓存每房间上限：被拒回合不清采样列表（跨窗口复用语义），
# 长播按回合累积；此上限只限制内存占用，不改变单回合判定结果
_BROADCAST_AUDIT_CACHE_MAX = 64


def _cap_broadcast_audit_cache(audit_cache: dict[str, Any]) -> None:
    """限制赛事审计缓存总量：超限时优先淘汰已完成条目（最旧先出）。

    未完成条目保留跨窗口增量扫描语义（samples 复用）；已完成条目不足时
    才按插入序淘汰最旧保底防无界。
    """
    if len(audit_cache) <= _BROADCAST_AUDIT_CACHE_MAX:
        return
    overflow = len(audit_cache) - _BROADCAST_AUDIT_CACHE_MAX
    completed_keys = [
        key for key, item in audit_cache.items()
        if isinstance(item, dict) and item.get("completed")
    ]
    for key in completed_keys[:overflow]:
        audit_cache.pop(key, None)
    if len(audit_cache) > _BROADCAST_AUDIT_CACHE_MAX:
        for key in list(audit_cache)[: len(audit_cache) - _BROADCAST_AUDIT_CACHE_MAX]:
            audit_cache.pop(key, None)


def _mark_broadcast_audit_skipped(
    rounds: list[dict[str, Any]],
    error: object,
) -> list[dict[str, Any]]:
    """Keep OCR candidates when the optional broadcast audit is unavailable."""
    safe_error = redact_text(str(error or "broadcast audit unavailable"))[:240]
    marked: list[dict[str, Any]] = []
    for item in rounds:
        if not isinstance(item, dict):
            continue
        candidate = dict(item)
        candidate["broadcast_audit"] = "skipped"
        candidate["broadcast_review_required"] = True
        candidate["broadcast_audit_error"] = safe_error
        marked.append(candidate)
    return marked


def _adaptive_catchup_cap(
    throughput_history: list[float] | None,
    kick_interval: float | None = None,
    *,
    scan_cycle_sec: float | None = None,
) -> float:
    """根据实际扫描周期计算增量追赶上限。

    ``kick_interval`` 是旧版兼容参数，不能再代表分析循环的名义轮询间隔。
    持续分析应传入 ``scan_cycle_sec``（上一次实际扫描启动到本次启动的
    墙钟周期），否则 Valorant 的 5 秒轮询间隔会把追赶预算永久夹在 45 秒。
    """
    if not throughput_history:
        # 无历史时用短窗，而不是 MAX。中途开分析时 last_analyzed=0，
        # 若一次吞掉已录全部时长，analyzed_duration 会卡在 0 直到整窗 OCR 结束。
        return MIN_CATCHUP_SEC
    history = [float(v) for v in throughput_history if float(v) > 0.0]
    if not history:
        return MIN_CATCHUP_SEC
    avg = sum(history) / len(history)
    cycle = scan_cycle_sec if scan_cycle_sec is not None else kick_interval
    try:
        cycle_value = float(cycle or 0.0)
    except (TypeError, ValueError):
        cycle_value = 0.0
    # 无效/缺失实际周期时仍保守使用旧默认，避免首轮扩大到整场录像。
    cycle_value = max(1.0, cycle_value)
    return min(
        MAX_CATCHUP_SEC,
        max(MIN_CATCHUP_SEC, avg * cycle_value * 1.5),
    )


def window_scan_timeout(scan_duration_sec: float, *, use_ocr: bool) -> int:
    """单窗扫描超时（秒）：OCR 双区域抽检在负载下常需 1.5–2× 窗长。"""
    dur = max(1.0, float(scan_duration_sec))
    if not use_ocr:
        return int(max(45, int(dur / 180.0 * 12) + 45))
    return int(min(900, max(120, int(dur * 2.0) + 90)))


def decide_backlog_policy(
    backlog_sec: float,
    throughput_avg: float | None = None,
    audit_queue_depth: int = 0,
) -> tuple[str, dict[str, Any]]:
    """A-02: 自适应 backlog 控制器分层决策。

    - <=30s: realtime (正常 OCR + 有界审计)
    - 30–60s: catchup (扩大新增媒体，取消非必要空闲)
    - 60–180s: priority-catchup (粗扫优先，候选审计限额)
    - >180s: degraded-catchup (降低单秒成本，候选仍公平审计)

    ocr_sample_interval 恒为 1.0：顶部计时器必须保持 1fps（计时器跳变/
    短横幅是回合边界证据，降频会漏检）。降本旋钮是 center_sentinel_sec
    （中央横幅哨兵采样间隔，只影响高成本中央 OCR）。
    """
    del throughput_avg
    if backlog_sec <= 30.0:
        mode = "realtime"
        audit_quota = 2
        center_sentinel_sec = 4.0
    elif backlog_sec <= 60.0:
        mode = "catchup"
        audit_quota = 1
        center_sentinel_sec = 4.0
    elif backlog_sec <= 180.0:
        mode = "priority-catchup"
        audit_quota = 1
        center_sentinel_sec = 6.0
    else:
        mode = "degraded-catchup"
        audit_quota = 1
        center_sentinel_sec = 8.0

    policy = {
        "mode": mode,
        "audit_quota": audit_quota,
        "ocr_sample_interval": 1.0,
        "center_sentinel_sec": center_sentinel_sec,
        "backlog_sec": round(float(backlog_sec), 1),
        "audit_queue_depth": int(audit_queue_depth),
    }
    return mode, policy


def compute_valorant_scan_budget(
    mode: str,
    last_analyzed: float,
    current_dur: float,
    pressure: dict[str, Any] | None = None,
    *,
    throughput_history: list[float] | None = None,
    kick_interval: float = 60.0,
    scan_cycle_sec: float | None = None,
    lookback_sec: float | None = None,
) -> tuple[tuple[float, float], bool, int, bool]:
    """增量扫描预算：从已分析点回看 lookback 再向前追赶，绝不跳窗漏扫。

    首窗（last_analyzed<=0）同样受 catchup_cap 约束，禁止一次扫完整场已录内容。
    throughput_history：近 N 次扫描吞吐（媒体秒/墙钟秒），用于自适应窗口。
    kick_interval：旧版兼容的名义间隔；新调用必须优先传 scan_cycle_sec。
    scan_cycle_sec：实际两次扫描启动之间的墙钟周期（秒）。
    lookback_sec：回看秒数；不传/非法时用稳态 8s，失败重试由上层传 30s。
    """
    del pressure
    last = float(last_analyzed)
    dur = float(current_dur)
    try:
        lookback = max(0.0, float(lookback_sec)) if lookback_sec is not None else STEADY_LOOKBACK_SEC
    except (TypeError, ValueError):
        lookback = STEADY_LOOKBACK_SEC
    catchup_cap = _adaptive_catchup_cap(
        throughput_history,
        kick_interval,
        scan_cycle_sec=scan_cycle_sec,
    )
    if last <= 0.0:
        scan_start = 0.0
        scan_end = min(dur, catchup_cap)
        # 只有首窗已经覆盖当前全部已录内容时才叫 full；中途开分析要切窗追赶。
        full_rescan = scan_end >= dur - 0.5
    else:
        scan_start = max(0.0, last - lookback)
        scan_end = min(dur, last + catchup_cap)
        if scan_end < scan_start:
            scan_end = dur
        full_rescan = False
    scan_range = (round(scan_start, 3), round(float(scan_end), 3))
    scan_duration = max(1.0, scan_range[1] - scan_range[0])
    timeout = window_scan_timeout(scan_duration, use_ocr=True)
    return scan_range, True, timeout, full_rescan


class ValorantAnalyzerPlugin:
    game = "valorant"
    display_name = "Valorant"

    def capabilities(self) -> AnalyzerCapabilities:
        return AnalyzerCapabilities(
            realtime_continuous=True,
            posthoc_file=True,
            needs_ocr=True,
            needs_audio=False,
            game_specific=True,
        )

    def analyze_file(
        self,
        video_path: str,
        *,
        progress_callback: Callable[[str, float, str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None:
        """录制完成后的全文件分析（与持续分析共用纯 OCR 检测器）。"""
        if cancel_check and cancel_check():
            return None
        options = options or {}
        from lsc.analyzer.valorant_ocr_rounds import detect_valorant_rounds_ocr

        source_profile = str(options.get("valorant_profile") or "pov")
        ocr_candidates: list[dict[str, Any]] = []
        try:
            if progress_callback:
                progress_callback("round_detect", 0.0, "OCR 回合检测中...")
            rounds = detect_valorant_rounds_ocr(
                video_path,
                ffmpeg_path=options.get("ffmpeg_path") or "ffmpeg",
                cancel_check=cancel_check,
                progress_callback=progress_callback,
                finalize=True,
                source_profile=source_profile,
            ) or []
            ocr_candidates = [dict(item) for item in rounds if isinstance(item, dict)]
            if source_profile == "broadcast" and rounds:
                from lsc.analyzer.valorant_broadcast import audit_broadcast_rounds

                try:
                    rounds = audit_broadcast_rounds(
                        rounds,
                        video_path,
                        ffmpeg_path=options.get("ffmpeg_path") or "ffmpeg",
                        cancel_check=cancel_check,
                        finalize=True,
                    )
                except Exception as exc:
                    _log.warning(
                        "Valorant broadcast audit skipped; retaining OCR candidates: %s",
                        redact_text(exc),
                    )
                    rounds = _mark_broadcast_audit_skipped(ocr_candidates, exc)
            return rounds
        except Exception as exc:
            _log.warning(
                "Valorant %s analyze_file failed: %s",
                source_profile,
                redact_text(exc),
            )
            if source_profile == "broadcast":
                return _mark_broadcast_audit_skipped(ocr_candidates, exc)
            return None

    def plan_scan_window(
        self,
        state: dict[str, Any],
        current_dur: float,
        pressure: dict[str, Any],
    ) -> ScanWindow:
        scan_range, use_ocr, timeout, full_rescan = compute_valorant_scan_budget(
            mode=state.get("mode", "valorant_round"),
            last_analyzed=float(state.get("last_analyzed", 0.0) or 0.0),
            current_dur=current_dur,
            pressure=pressure,
            throughput_history=state.get("throughput_history"),
            kick_interval=float(state.get("kick_interval") or 60.0),
            scan_cycle_sec=(
                float(state.get("scan_cycle_sec"))
                if state.get("scan_cycle_sec") is not None
                else None
            ),
            lookback_sec=state.get("incremental_lookback"),
        )
        state["full_rescan"] = full_rescan
        start, end = scan_range
        return ScanWindow(
            start_sec=float(start),
            end_sec=float(end),
            timeout_sec=float(timeout),
            use_ocr=bool(use_ocr),
        )

    def scan_window(
        self,
        video_path: str,
        window: ScanWindow,
        state: dict[str, Any],
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """持续分析增量扫描：调纯 OCR 检测器。

        ``last_analyzed`` 只在实际抽帧成功后更新；输入文件缺失、抽帧失败
        或 OCR 异常都必须由上层按失败窗口重试，不能伪造覆盖进度。
        """
        # None 保持旧版测试/插件替身的兼容；真实 OCR 检测器会明确写入
        # True/False，只有明确 False 才阻止覆盖游标推进。
        state["scan_succeeded"] = None
        from lsc.utils.helpers import resolve_real_video_path
        video_path = resolve_real_video_path(video_path)
        # Contract / placeholder paths: skip on empty/missing files.
        try:
            if not os.path.isfile(video_path) or os.path.getsize(video_path) <= 0:
                state["scan_succeeded"] = False
                return []
        except OSError:
            state["scan_succeeded"] = False
            return []
        from lsc.analyzer.valorant_ocr_rounds import detect_valorant_rounds_ocr

        ocr_candidates: list[dict[str, Any]] = []
        try:
            # 增量：粗扫先返回入列；收尾/全量仍同步密扫保证终态精度
            _finalize = bool(state.get("finalize", False))
            rounds = detect_valorant_rounds_ocr(
                video_path,
                time_range=(window.start_sec, window.end_sec),
                ffmpeg_path=state.get("ffmpeg_path") or "ffmpeg",
                cancel_check=cancel_check,
                progress_callback=state.get("progress_callback"),
                runtime_state=state.get("runtime_state"),
                finalize=_finalize,
                source_profile=state.get("valorant_profile"),
                ocr_sample_interval=float(state.get("ocr_sample_interval", 1.0)),
                refine_boundaries=_finalize,
                fast_mode=bool(state.get("realtime_fast_mode", False)) and not _finalize,
            ) or []
            if state.get("scan_succeeded") is False:
                return []
            ocr_candidates = [dict(item) for item in rounds if isinstance(item, dict)]
            _runtime_state = state.get("runtime_state")
            _has_broadcast_pending = (
                isinstance(_runtime_state, dict)
                and bool(_runtime_state.get("broadcast_pending_rounds"))
            )
            if state.get("valorant_profile") == "broadcast" and (
                rounds or _has_broadcast_pending
            ):
                from lsc.analyzer.valorant_broadcast import audit_broadcast_rounds
                from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier

                runtime_state = state.get("runtime_state")
                if not isinstance(runtime_state, dict):
                    runtime_state = {}
                    state["runtime_state"] = runtime_state
                classifier = runtime_state.get("broadcast_classifier")
                if not isinstance(classifier, ValorantFrameClassifier):
                    classifier = ValorantFrameClassifier()
                    runtime_state["broadcast_classifier"] = classifier

                # 直播尖端刚闭合的 OCR 候选需要等到后视窗口写入文件后再
                # 定稿。pending 项跨窗口保存在 runtime_state，不让它在
                # 本次扫描结束时因 last_analyzed 前移而丢失。
                pending = runtime_state.get("broadcast_pending_rounds")
                if not isinstance(pending, list):
                    pending = []
                audit_cache = runtime_state.get("broadcast_audit_cache")
                if not isinstance(audit_cache, dict):
                    audit_cache = {}
                    runtime_state["broadcast_audit_cache"] = audit_cache
                _cap_broadcast_audit_cache(audit_cache)
                # 粗扫与审计解耦（A-03）：deferred_audit 模式下，粗扫在 1-2 秒内快速完成并推进游标，
                # 候选存入 broadcast_pending_rounds，交由后台审计 worker 异步精修与审计。
                # 阶段一优化：粗筛候选即刻返回给上层先行入列（标记为 pending 待复核），
                # 消除用户盲等；严禁在 scan_window 同步执行长 lookahead 抽帧卡死主循环。
                if bool(state.get("deferred_audit")) and not bool(state.get("finalize")):
                    merged_candidates: dict[float, dict[str, Any]] = {}
                    for candidate in [*pending, *rounds]:
                        if not isinstance(candidate, dict):
                            continue
                        try:
                            candidate_key = round(float(candidate["start"]), 3)
                        except (KeyError, TypeError, ValueError):
                            continue
                        c = dict(candidate)
                        if "broadcast_audit" not in c:
                            c["broadcast_audit"] = "pending_lookahead"
                            c["confirm_status"] = "pending"
                            c["boundary_refined"] = False
                            c["broadcast_review_required"] = True
                        merged_candidates[candidate_key] = c
                    runtime_state["broadcast_pending_rounds"] = list(merged_candidates.values())
                    state["scan_succeeded"] = True
                    state["last_analyzed"] = window.end_sec
                    return [dict(c) for c in merged_candidates.values()]

                # backlog 失控时只保存候选，推迟 90s lookahead 和视觉分类到
                # 停录收尾；候选保存在 runtime_state，下一窗口可继续复用。
                if bool(state.get("realtime_fast_mode")) and not bool(state.get("finalize")):
                    deferred_candidates: dict[float, dict[str, Any]] = {}
                    for candidate in [*pending, *rounds]:
                        if not isinstance(candidate, dict):
                            continue
                        try:
                            candidate_key = round(float(candidate["start"]), 3)
                        except (KeyError, TypeError, ValueError):
                            continue
                        deferred = dict(candidate)
                        deferred["broadcast_audit"] = "pending_lookahead"
                        deferred["broadcast_review_required"] = True
                        deferred["broadcast_audit_reason"] = "deferred_until_finalization"
                        deferred_candidates[candidate_key] = deferred
                    runtime_state["broadcast_pending_rounds"] = list(
                        deferred_candidates.values()
                    )
                    state["scan_succeeded"] = True
                    state["last_analyzed"] = window.end_sec
                    return []
                merged_candidates: dict[float, dict[str, Any]] = {}
                for candidate in [*pending, *rounds]:
                    if not isinstance(candidate, dict):
                        continue
                    try:
                        candidate_key = round(float(candidate["start"]), 3)
                    except (KeyError, TypeError, ValueError):
                        continue
                    merged_candidates[candidate_key] = dict(candidate)

                is_final_scan = bool(state.get("finalize"))
                available_end = None if is_final_scan else state.get("current_dur")
                sorted_candidates = [merged_candidates[k] for k in sorted(merged_candidates)]

                # A-02 / A-03: 粗扫与 broadcast 深度审计解耦。
                # 粗扫优先推进 coverage；深度审计按有界配额消费，
                # 禁止在单次扫描中阻塞处理全部候选导致 coverage 滞后发散。
                audit_batch: list[dict[str, Any]] = []
                remaining_pending: list[dict[str, Any]] = []

                if is_final_scan:
                    audit_batch = sorted_candidates
                else:
                    backlog_sec = max(0.0, float(state.get("current_dur", 0.0) or 0.0) - float(window.end_sec))
                    tp_hist = list(state.get("throughput_history") or [])
                    tp_avg = (sum(tp_hist) / len(tp_hist)) if tp_hist else None
                    mode_name, policy = decide_backlog_policy(backlog_sec, tp_avg, len(sorted_candidates))
                    state["backlog_mode"] = mode_name
                    state["audit_queue_depth"] = len(sorted_candidates)
                    quota = policy.get("audit_quota", 1)

                    # 每次有界消费最多 quota 个候选，其余保留在 pending 队列中等待下一轮 kick
                    audit_batch = sorted_candidates[:quota]
                    for c in sorted_candidates[quota:]:
                        c_copy = dict(c)
                        c_copy["broadcast_audit"] = "pending_lookahead"
                        remaining_pending.append(c_copy)

                audited_rounds: list[dict[str, Any]] = []
                if audit_batch:
                    try:
                        audited_rounds = audit_broadcast_rounds(
                            audit_batch,
                            video_path,
                            ffmpeg_path=state.get("ffmpeg_path") or "ffmpeg",
                            cancel_check=cancel_check,
                            classifier=classifier,
                            available_end=(
                                float(available_end)
                                if available_end is not None
                                else None
                            ),
                            audit_cache=audit_cache,
                            finalize=is_final_scan,
                        )
                    except Exception as exc:
                        _log.warning(
                            "Valorant broadcast audit skipped; retaining OCR candidates: %s",
                            redact_text(exc),
                        )
                        audited_rounds = _mark_broadcast_audit_skipped(
                            [dict(item) for item in audit_batch],
                            exc,
                        )

                for item in audited_rounds:
                    if item.get("broadcast_audit") == "pending_lookahead":
                        remaining_pending.append(dict(item))

                runtime_state["broadcast_pending_rounds"] = remaining_pending
                rounds = [
                    item for item in audited_rounds
                    if item.get("broadcast_audit") != "pending_lookahead"
                ]
        except Exception as exc:
            _log.warning(
                "Valorant %s scan_window failed: %s",
                state.get("valorant_profile", "pov"),
                redact_text(exc),
            )
            # 真实 OCR 检测器会在进入扫描时把 scan_succeeded 置为 False；
            # audit 自身失败则检测器已明确置 True，仍可保留候选并标记待审。
            if state.get("scan_succeeded") is not True:
                state["scan_succeeded"] = False
            if state.get("valorant_profile") == "broadcast":
                rounds = _mark_broadcast_audit_skipped(ocr_candidates, exc)
            else:
                rounds = []
        if state.get("scan_succeeded") is False:
            return []
        state["scan_succeeded"] = True
        state["last_analyzed"] = window.end_sec
        return rounds or []
