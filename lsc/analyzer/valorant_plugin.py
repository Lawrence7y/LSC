"""Valorant analyzer plugin: 纯 OCR 回合检测（持续分析 + 文件分析统一）。"""
from __future__ import annotations

import logging
import os
import time
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
# ------------------------------------------------------------------ 直播沿优先（P1-5）
# 为什么需要：中途开分析时游标从 0 起爬（首窗强制 [0, MIN_CATCHUP]），而增量窗上限
# 只有 MAX_CATCHUP_SEC=90s/窗、单窗墙钟常 > 窗口媒体（实测 OCR 0.78x 实时）⇒ 净推进
# 可能为负，backlog 单调增长。结果是最新回合要等整段历史爬完才出现（一小时录像要等
# 一小时以上），用户视角就是「现在的回合一直不来」。
# 策略：只要「已覆盖到的最右端」落后文件尾超过 LIVE_FIRST_MAX_TAIL_LAG_SEC，就把这一窗
# 让给直播沿窗口 [dur - catchup_cap, dur]（记进 coverage 账本），否则继续按游标回填。
# 两个方向都有界，且不牺牲完整性：
#   * 直播沿滞后 <= 阈值 + 一个窗；
#   * 中间缺口不属于「已覆盖」，收尾由 uncovered_ranges_for_state 逐片补扫（既有机制），
#     运行期也会在直播沿已新鲜的周期继续回填。
# 首窗（last<=0）不插队：先给一段文件头基线，再由本策略接管（既有首窗守卫测试固化）。
# 切换窗口时 room_handler 必须重置 OCR 跨窗状态（FSM/锚点/last_processed_ts），
# 非连续窗口的相位连续性是无效的，且 last_processed_ts 只增不减会把回填窗整段过滤掉。
LIVE_FIRST_MAX_TAIL_LAG_SEC = 240.0


# 赛事审计缓存每房间上限：被拒回合不清采样列表（跨窗口复用语义），
# 长播按回合累积；此上限只限制内存占用，不改变单回合判定结果
_BROADCAST_AUDIT_CACHE_MAX = 64

# 审计被「取消」时的候选重试上限。取消（扫描超时 / 录制 epoch 切换 / 停止抢占）
# 不是结构性无解：审计根本没跑完，文件还在，下一轮理应能接着跑。旧实现在异常分支
# 把整批候选盖成 ``broadcast_audit="skipped"``，而回写待审队列的条件只认
# ``pending_lookahead`` ⇒ 候选被**静默丢出队列**，此后既不会再审、也没有终态，
# 收尾只剩"listed 无终态"空转，最终落 manual_review / 导出侧报 NEVER_AUDITED。
# 现场：2026-09-14 10:34:44（360s 扫描超时取消）→ round-000097 永久滞留。
# 有界重试：真·无解（例如模型不可用）也不能无限重排，超过上限即按"跳过"落终态，
# 交给收尾兜底与导出门禁判据，而不是无限占用审计槽位。
_BROADCAST_AUDIT_CANCEL_RETRY_MAX = 3
_BROADCAST_AUDIT_CANCEL_RETRY_FIELD = "broadcast_audit_cancel_retries"

# 扫描窗口内**同步** broadcast 审计的单步媒体预算（与 room_handler 后台步
# `_BCAST_REFINE_STEP_MEDIA_SEC=18` 同量级）。
#
# 为什么必须限步（2026-09-14 现场实测）：收尾阶段 `deferred_audit=not finalizing`
# 为 False ⇒ scan_window 会在**同一个被超时包裹的调用里**先 OCR、再同步跑完
# broadcast 审计。实测（真实录像、无争抢）该窗口 OCR 只需要 51.8s（66.2s 媒体，
# 0.78x 实时），但现场同窗口烧满了 360s 扫描超时被中止——预算是需求的 7 倍，
# 说明问题不是"预算太小"，而是审计与扫描共用 ONNX/DirectML 锁互相饿死。
# 限步后单轮审计工作量有界，扫描超时留有充足余量，不再出现"窗口超时 →
# 正在跑的审计被取消 → 候选滞留"这条链（round-000097 就是这么丢的）。
_BROADCAST_INLINE_AUDIT_STEP_MEDIA_SEC = 18.0
# 分段耗时打点阈值：扫描耗时超过窗口超时预算的这个比例就打一条 WARNING，
# 把 [OCR / 审计] 两段墙钟拆开，避免下次只能靠猜（本次排查就缺这个数据）。
_SCAN_SLOW_LOG_RATIO = 0.6

# ---------------------------------------------------------------- 缺口补扫口径
# 收尾缺口补扫（sweep_gap_rounds）的输入只该包含「未定稿候选 + 已定稿真实回合覆盖
# 的区间」，否则会把已有人管的区间当成"无候选区间"再合成一次。现场（2026-09-14）：
#   * round-000015-s0（154.1-246.7）11:43 定稿、round-000071-s0（712.2-802.2）11:52
#     定稿（**后台审计路径**，正常阶段 deferred_audit=True 时插件不审计）；
#   * 11:54:33 补扫仍把 153.0-257.0 / 711.0-813.0 判为无候选并合成新候选，最终以
#     **父键** round-000015 / round-000071 定稿 ⇒ 同一真实回合两条重叠条目。
#   导出侧靠重叠去重兜住（R12/R13 → OVERLAP_DEDUP），但 requested 计数虚高、白跑
#   两次完整审计，且让收尾每轮都有"新进展"从而必然撞满补扫轮次上限。
# 已定稿跨度台账放在 **audit_cache**——两条审计路径共享同一份
# （`_rs_state['broadcast_audit_cache']`），只有写在那里补扫才看得见另一条路径的结论；
# 写入点在 `valorant_broadcast.audit_broadcast_rounds` 出口，插件只负责读。
#
# 收尾补扫只跑一次的标记必须放 runtime_state（跨调用存活）：旧实现写在每次
# `_do_scan` 新建的局部 state 上 ⇒ 标记每次都丢，收尾每轮重扫全片（本轮实测 4 次）。
_GAP_SWEEP_DONE_KEY = "gap_sweep_done"


def _finalized_span_items(audit_cache: Any) -> list[dict[str, Any]]:
    """已定稿真实回合的跨度，转成 sweep 能吃的 {start,end} 形式（只读这两个字段）。"""
    try:
        from lsc.analyzer.valorant_broadcast import finalized_spans
    except Exception:  # pragma: no cover - 分析器不可用时退化为旧行为（不排除）
        return []
    return [{"start": span[0], "end": span[1]} for span in finalized_spans(audit_cache)]


def _is_audit_cancellation(error: object) -> bool:
    """审计异常是否为「取消/中止」（可重试）而非「审计不可用」（重试无意义）。"""
    name = type(error).__name__.lower()
    if "cancel" in name:
        return True
    text = str(error or "").lower()
    return any(
        token in text
        for token in ("cancelled", "canceled", "取消", "中断", "中止", "timeout", "超时")
    )


def _preserve_audit_retry_state(previous: Any, current: dict[str, Any]) -> None:
    """同 key 候选合并时保留「审计取消重试计数」。

    合并语义是"后写入者胜"（``merged_candidates[key] = c``，rounds 覆盖 pending）。
    OCR 若在后续窗口重新产出同一回合（lookback 重叠 / 重连重扫），新候选会盖掉
    待审队列里的旧条目；丢掉计数就等于取消重试没有上界，可以无限空转占审计槽位。
    """
    if not isinstance(previous, dict):
        return
    used = previous.get(_BROADCAST_AUDIT_CANCEL_RETRY_FIELD)
    if used is None or current.get(_BROADCAST_AUDIT_CANCEL_RETRY_FIELD) is not None:
        return
    current[_BROADCAST_AUDIT_CANCEL_RETRY_FIELD] = used


def _cancel_retry_candidates(
    marked: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """从"审计被取消"的批次里挑出应下一轮续审的候选（返回 (重试项, 超限数)）。

    幂等：只递增自身计数器，不改动 ``broadcast_audit``——列表里仍显示"未审计"，
    与真实的 pending 语义一致（前端不该看到"已审计"）。
    """
    retry: list[dict[str, Any]] = []
    exhausted = 0
    for item in marked:
        if not isinstance(item, dict):
            continue
        try:
            used = int(item.get(_BROADCAST_AUDIT_CANCEL_RETRY_FIELD) or 0)
        except (TypeError, ValueError):
            used = 0
        if used >= _BROADCAST_AUDIT_CANCEL_RETRY_MAX:
            exhausted += 1
            continue
        candidate = dict(item)
        candidate[_BROADCAST_AUDIT_CANCEL_RETRY_FIELD] = used + 1
        retry.append(candidate)
    return retry, exhausted


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


def _candidate_merge_key(candidate: dict[str, Any]) -> str:
    """Return the immutable round identity for pending-queue merge.

    Prefer the explicit ``round_key`` assigned at OCR birth; fall back to the
    legacy 10-second bucket so old in-flight state remains compatible.
    """
    key = str(candidate.get("round_key") or "").strip()
    if key:
        return key
    try:
        start = float(candidate.get("start", 0.0) or 0.0)
    except (TypeError, ValueError):
        return ""
    return f"round-{int(round(start / 10.0)):06d}"


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
    *,
    source_profile: str = "pov",
) -> tuple[str, dict[str, Any]]:
    """A-02: 自适应 backlog 控制器分层决策。

    - <=30s: realtime (正常 OCR + 有界审计)
    - 30–60s: catchup (扩大新增媒体，取消非必要空闲)
    - 60–180s: priority-catchup (粗扫优先，候选审计限额)
    - >180s: degraded-catchup (降低单秒成本，候选仍公平审计)

    ocr_sample_interval 恒为 1.0：顶部计时器必须保持 1fps（计时器跳变/
    短横幅是回合边界证据，降频会漏检）。降本旋钮是 center_sentinel_sec
    （中央横幅哨兵采样间隔，只影响高成本中央 OCR）。

    官方解说 / broadcast 分支以切片质量为最高优先级：即使长时间落后
    （>180s），也不放大中央哨兵间隔、不缩小单轮审计配额，避免“第一个
    切片好、后面越来越糙”。
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

    # 官方解说 / broadcast：不允许因 backlog 降低中央 OCR 采样和审计配额。
    if source_profile == "broadcast":
        audit_quota = max(audit_quota, 2)
        center_sentinel_sec = 4.0

    policy = {
        "mode": mode,
        "audit_quota": audit_quota,
        "ocr_sample_interval": 1.0,
        "center_sentinel_sec": center_sentinel_sec,
        "backlog_sec": round(float(backlog_sec), 1),
        "audit_queue_depth": int(audit_queue_depth),
    }
    return mode, policy


def live_first_scan_window(
    *,
    last_analyzed: float,
    current_dur: float,
    tail_lag_sec: float | None,
    catchup_cap: float,
    lookback_sec: float,
) -> tuple[float, float] | None:
    """直播沿优先窗口：返回 None 表示走常规增量（沿游标回填缺口）。

    ``tail_lag_sec`` = 文件尾 - 「已覆盖到的最右端」（由 coverage 账本算出）。
    返回的窗口必须与常规增量窗**不重叠**，否则没必要插队（常规窗本来就扫到）。
    """
    if tail_lag_sec is None:
        return None
    try:
        lag = float(tail_lag_sec)
        last = float(last_analyzed)
        dur = float(current_dur)
        cap = max(1.0, float(catchup_cap))
        lookback = max(0.0, float(lookback_sec))
    except (TypeError, ValueError):
        return None
    if dur <= 0.0 or lag <= LIVE_FIRST_MAX_TAIL_LAG_SEC:
        return None
    if last <= 0.0:
        # 首窗不插队：先扫文件头基线（既有首窗语义，有守卫测试固化）。
        return None
    if last + cap >= dur - 1.0:
        # 常规追赶窗已经够到文件尾：它本身就是「直播沿窗」，不需要插队。
        return None
    scan_end = dur
    scan_start = max(0.0, dur - cap)
    if scan_start <= last + lookback + 1.0:
        # 直播沿窗会与游标窗重叠：没有可插队的空间。
        return None
    if scan_end <= scan_start + 1.0:
        return None
    return (round(scan_start, 3), round(float(scan_end), 3))


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
    tail_lag_sec: float | None = None,
) -> tuple[tuple[float, float], bool, int, bool]:
    """增量扫描预算：从已分析点回看 lookback 再向前追赶，绝不跳窗漏扫。

    首窗（last_analyzed<=0）同样受 catchup_cap 约束，禁止一次扫完整场已录内容。
    throughput_history：近 N 次扫描吞吐（媒体秒/墙钟秒），用于自适应窗口。
    kick_interval：旧版兼容的名义间隔；新调用必须优先传 scan_cycle_sec。
    scan_cycle_sec：实际两次扫描启动之间的墙钟周期（秒）。
    lookback_sec：回看秒数；不传/非法时用稳态 8s，失败重试由上层传 30s。
    tail_lag_sec：文件尾 - 已覆盖最右端；超过阈值时本窗让给直播沿（P1-5）。
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
    live_first_range = live_first_scan_window(
        last_analyzed=last,
        current_dur=dur,
        tail_lag_sec=tail_lag_sec,
        catchup_cap=catchup_cap,
        lookback_sec=lookback,
    )
    if live_first_range is not None:
        scan_duration = max(1.0, live_first_range[1] - live_first_range[0])
        return (
            live_first_range,
            True,
            window_scan_timeout(scan_duration, use_ocr=True),
            False,
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
            tail_lag_sec=state.get("tail_lag_sec"),
        )
        state["full_rescan"] = full_rescan
        # 直播沿优先（P1-5）：本窗是否插队到文件尾（room_handler 据此定 scan_reason、
        # 决定是否推进主游标、以及是否重置 OCR 跨窗状态）。
        state["live_first"] = bool(
            not full_rescan
            and float(scan_range[1]) >= float(current_dur) - 1.0
            and float(scan_range[0]) > float(state.get("last_analyzed", 0.0) or 0.0) + 1.0
            and float(state.get("tail_lag_sec") or 0.0) > LIVE_FIRST_MAX_TAIL_LAG_SEC
        )
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
        _stage_t0 = time.monotonic()
        _ocr_elapsed = 0.0
        _audit_elapsed = 0.0
        _audit_batch_n = 0
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
            _ocr_elapsed = time.monotonic() - _stage_t0
            if state.get("scan_succeeded") is False:
                return []
            ocr_candidates = [dict(item) for item in rounds if isinstance(item, dict)]
            _runtime_state = state.get("runtime_state")
            _has_broadcast_pending = (
                isinstance(_runtime_state, dict)
                and bool(_runtime_state.get("broadcast_pending_rounds"))
            )
            # 收尾缺口补扫**不依赖"本轮有新候选"**：它要抓的恰恰是"画面静止导致
            # OCR 全程没产出候选"的漏检。现场（2026-09-14 12:40 会话）：收尾各轮
            # OCR 恒为 0 回合、待审队列已排空 ⇒ 整个 broadcast 分支被跳过，补扫
            # 一次没跑，最后 650.2-736.1（85.9s）无人巡检（离线复跑该区间 5.6s、
            # 确认没漏回合，但机制上必须补上）。故收尾期无条件进入该分支。
            if state.get("valorant_profile") == "broadcast" and (
                rounds or _has_broadcast_pending or bool(state.get("finalize"))
            ):
                from lsc.analyzer.valorant_broadcast import audit_broadcast_rounds
                from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier

                runtime_state = state.get("runtime_state")
                if not isinstance(runtime_state, dict):
                    runtime_state = {}
                    state["runtime_state"] = runtime_state
                classifier = runtime_state.get("broadcast_classifier")
                if not isinstance(classifier, ValorantFrameClassifier):
                    classifier = ValorantFrameClassifier(profile="broadcast")
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
                    merged_candidates: dict[str, dict[str, Any]] = {}
                    for candidate in [*pending, *rounds]:
                        if not isinstance(candidate, dict):
                            continue
                        candidate_key = _candidate_merge_key(candidate)
                        if not candidate_key:
                            continue
                        c = dict(candidate)
                        c.setdefault("round_key", candidate_key)
                        if "broadcast_audit" not in c:
                            c["broadcast_audit"] = "pending_lookahead"
                            c["confirm_status"] = "pending"
                            c["boundary_refined"] = False
                            c["broadcast_review_required"] = True
                        merged_candidates[candidate_key] = c
                    runtime_state["broadcast_pending_rounds"] = sorted(
                        merged_candidates.values(),
                        key=lambda x: float(x.get("start", 0.0) or 0.0),
                    )
                    state["scan_succeeded"] = True
                    state["last_analyzed"] = window.end_sec
                    return [dict(c) for c in runtime_state["broadcast_pending_rounds"]]

                # backlog 失控时只保存候选，推迟 90s lookahead 和视觉分类到
                # 停录收尾；候选保存在 runtime_state，下一窗口可继续复用。
                if bool(state.get("realtime_fast_mode")) and not bool(state.get("finalize")):
                    deferred_candidates: dict[str, dict[str, Any]] = {}
                    for candidate in [*pending, *rounds]:
                        if not isinstance(candidate, dict):
                            continue
                        candidate_key = _candidate_merge_key(candidate)
                        if not candidate_key:
                            continue
                        deferred = dict(candidate)
                        deferred.setdefault("round_key", candidate_key)
                        deferred["broadcast_audit"] = "pending_lookahead"
                        deferred["broadcast_review_required"] = True
                        deferred["broadcast_audit_reason"] = "deferred_until_finalization"
                        deferred_candidates[candidate_key] = deferred
                    runtime_state["broadcast_pending_rounds"] = sorted(
                        deferred_candidates.values(),
                        key=lambda x: float(x.get("start", 0.0) or 0.0),
                    )
                    state["scan_succeeded"] = True
                    state["last_analyzed"] = window.end_sec
                    return []
                merged_candidates: dict[str, dict[str, Any]] = {}
                for candidate in [*pending, *rounds]:
                    if not isinstance(candidate, dict):
                        continue
                    candidate_key = _candidate_merge_key(candidate)
                    if not candidate_key:
                        continue
                    c = dict(candidate)
                    c.setdefault("round_key", candidate_key)
                    _preserve_audit_retry_state(
                        merged_candidates.get(candidate_key), c
                    )
                    merged_candidates[candidate_key] = c

                is_final_scan = bool(state.get("finalize"))
                available_end = None if is_final_scan else state.get("current_dur")
                sorted_candidates = sorted(
                    merged_candidates.values(),
                    key=lambda x: float(x.get("start", 0.0) or 0.0),
                )

                # A-02 / A-03: 粗扫与 broadcast 深度审计解耦。
                # 粗扫优先推进 coverage；深度审计按有界配额消费，
                # 禁止在单次扫描中阻塞处理全部候选导致 coverage 滞后发散。
                audit_batch: list[dict[str, Any]] = []
                remaining_pending: list[dict[str, Any]] = []

                if is_final_scan:
                    # 收尾缺口补扫：live 增量会漏掉"画面静止"区间里的回合（实测
                    # 2026-09-12：1530-1632 / 1740-1802 两段真实交战无候选），
                    # 收尾时对无候选区间做低频视觉巡检并合成候选，与 OCR 候选走
                    # 同一套审计/门禁（只补候选，不放宽判据）。
                    # 只补扫一次：标记存 runtime_state（跨调用存活）。旧实现写在本轮
                    # 局部 state 上，每次都丢 ⇒ 收尾每轮重扫全片（本轮实测 4 次）。
                    _sweep_done = bool(
                        state.get(_GAP_SWEEP_DONE_KEY)
                        or runtime_state.get(_GAP_SWEEP_DONE_KEY)
                    )
                    _sweep_ok = False
                    try:
                        from lsc.analyzer.valorant_broadcast import sweep_gap_rounds

                        # 已定稿切片覆盖过的区间也算"已覆盖"，否则同一真实回合会被
                        # 再合成一次（本轮 153.0-257.0 / 711.0-813.0 就是已定稿的
                        # s0 区间，重复候选最终以父键定稿 → 同回合两条重叠条目）。
                        _sweep_input = [
                            *sorted_candidates,
                            *_finalized_span_items(audit_cache),
                        ]
                        _swept = [] if _sweep_done else sweep_gap_rounds(
                            video_path,
                            _sweep_input,
                            duration=float(state.get("current_dur", 0.0) or 0.0),
                            classifier=classifier,
                            ffmpeg_path=state.get("ffmpeg_path") or "ffmpeg",
                            cancel_check=cancel_check,
                        )
                        _sweep_ok = True
                    except Exception as exc:  # noqa: BLE001 - 补扫失败不得影响收尾
                        _log.warning("收尾缺口补扫失败（忽略）: %s", redact_text(exc))
                        _swept = []
                    if _sweep_ok:
                        # 「跑过一遍」就算完成，不管有没有合成出候选：巡检出"确实没有
                        # 漏掉的交战"同样是结论，不能下一轮再把同一批缺口重扫一遍。
                        # 标记必须落 runtime_state 才跨调用存活（state 是每轮新建的）。
                        # 失败时**不打**标记，留给后续轮次重试。
                        state[_GAP_SWEEP_DONE_KEY] = True
                        runtime_state[_GAP_SWEEP_DONE_KEY] = True
                    for _item in _swept:
                        _key = str(_item.get("start", 0.0))
                        merged_candidates.setdefault(f"gap-{_key}", _item)
                    if _swept:
                        sorted_candidates = sorted(
                            merged_candidates.values(),
                            key=lambda x: float(x.get("start", 0.0) or 0.0),
                        )
                    audit_batch = sorted_candidates
                else:
                    backlog_sec = max(0.0, float(state.get("current_dur", 0.0) or 0.0) - float(window.end_sec))
                    tp_hist = list(state.get("throughput_history") or [])
                    tp_avg = (sum(tp_hist) / len(tp_hist)) if tp_hist else None
                    mode_name, policy = decide_backlog_policy(
                        backlog_sec, tp_avg, len(sorted_candidates),
                        source_profile=state.get("valorant_profile", "pov"),
                    )
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
                # 计时与计数必须**无条件**初始化：空候选窗是常态（收尾尾部窗 OCR
                # 本就 0 回合），只在 `if audit_batch:` 内赋值会让下面的耗时打点抛
                # UnboundLocalError，被兜底 except 吞成 "scan_window failed"、把整窗
                # 判失败 ⇒ 上层按扫描重试耗尽后放弃收尾（2026-09-15 真机：phase=error、
                # 17 段停在待确认、草稿永不生成）。
                _audit_batch_n = len(audit_batch)
                _audit_t0 = time.monotonic()
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
                            # 限步：单个候选的审计一次只推进 18s 媒体（见常量注释）。
                            # 不限步时审计会与被超时包裹的扫描争抢 ONNX/DirectML 锁，
                            # 把整窗拖到扫描超时（现场实测该窗口无争抢只需 51.8s）。
                            max_media_step_sec=_BROADCAST_INLINE_AUDIT_STEP_MEDIA_SEC,
                        )
                    except Exception as exc:
                        _audit_cancelled = _is_audit_cancellation(exc)
                        _log.warning(
                            "Valorant broadcast audit skipped; retaining OCR candidates: %s",
                            redact_text(exc),
                        )
                        audited_rounds = _mark_broadcast_audit_skipped(
                            [dict(item) for item in audit_batch],
                            exc,
                        )
                        if _audit_cancelled:
                            # 取消 ≠ 结构性无解：审计没跑完，必须把候选留在待审队列里续审。
                            # 否则候选既无终态、又不在队列（现场 pending_queue_depth=0），
                            # 收尾只剩空转，最终被兜底判 manual_review。
                            _retry_items, _exhausted = _cancel_retry_candidates(
                                audited_rounds
                            )
                            for _retry_item in _retry_items:
                                remaining_pending.append(_retry_item)
                            if _retry_items or _exhausted:
                                _log.warning(
                                    "赛事审计被取消，候选重新排队续审: retry=%d, 超限落终态=%d",
                                    len(_retry_items),
                                    _exhausted,
                                )

                for item in audited_rounds:
                    if item.get("broadcast_audit") == "pending_lookahead":
                        remaining_pending.append(dict(item))

                # 已定稿跨度由分析器在审计出口写进共享 audit_cache
                # （两条审计路径共用一份，插件侧不再自己维护，避免各记一半）。
                runtime_state["broadcast_pending_rounds"] = remaining_pending
                rounds = [
                    item for item in audited_rounds
                    if item.get("broadcast_audit") != "pending_lookahead"
                ]
                _audit_elapsed = time.monotonic() - _audit_t0
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
        _total_elapsed = time.monotonic() - _stage_t0
        if (
            window.timeout_sec > 0
            and _total_elapsed >= _SCAN_SLOW_LOG_RATIO * float(window.timeout_sec)
        ):
            # 分段耗时打点：下次现场可据此判断慢在哪一段（OCR 抽帧/识别 vs 同步审计），
            # 而不是只能看到"扫描超时"。本次排查正是缺这条数据，
            # 只能靠离线复跑才测出"该窗口无争抢仅需 51.8s，预算 360s 被烧满是争抢"。
            _log.warning(
                "扫描耗时逼近超时预算: profile=%s, range=%.1f-%.1f (%.1fs 媒体), "
                "total=%.1fs, ocr=%.1fs, audit=%.1fs, timeout=%.0fs, "
                "audit_candidates=%d, pending_queue=%d",
                state.get("valorant_profile", "pov"),
                float(window.start_sec),
                float(window.end_sec),
                float(window.end_sec) - float(window.start_sec),
                _total_elapsed,
                _ocr_elapsed,
                _audit_elapsed,
                float(window.timeout_sec),
                _audit_batch_n,
                len((state.get("runtime_state") or {}).get("broadcast_pending_rounds") or []),
            )
        return rounds or []
