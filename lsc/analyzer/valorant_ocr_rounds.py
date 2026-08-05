"""无畏契约纯 OCR 回合检测器（持续分析与文件分析统一入口）。

只扫描三个 HUD 区域：顶部计分板 + 回合计时器、中央回合横幅（准备/结算）。
切片语义：入点 = 交战阶段第一帧（交战钟 >45s 连续确认），出点 = 下回合
准备阶段第一帧；交战 + 结算 + 回放（赛事流）均在切片内。

OCR 遵循两条先验：
- 相近相似原则：时间相近的两帧属性大致相同 → 计时器外推、两帧确认、
  冻结读数忽略（回放残留）；
- 循环原则：POV = 准备→交战→结算，赛事 = 准备→交战→结算→回放。
  只有交战阶段是确定的（交战钟锚点），其余相位不确定但顺序固定；
  非游戏画面可在任意相位间穿插。
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

from lsc.analyzer.ocr_accel import (
    build_hwaccel_vf,
    ffmpeg_hwaccel_args,
    read_settings_ocr_accel,
)

_log = logging.getLogger(__name__)

BOUNDARY_SOURCE = "valorant_ocr_v1"
# 买枪倒计时通常 ≤45s（手枪局 45s）；交战钟 >45s 且上限 100s。
BUY_TIMER_MAX_SEC = 45.0
_OCR_TIMER_MAX_PLAUSIBLE_SEC = 105.0
_OCR_TIMER_JUMP_TOL_SEC = 8.0
_OCR_TIMER_STALE_SEC = 35.0  # 锚点无读数存活上限
_MAX_OPEN_SEC = 175.0        # 交战超时强制闭合
_MAX_SETTLE_SEC = 90.0       # 结算/回放后等待下一回合准备的最大秒数
_MIN_ROUND_SEC = 10.0        # 最短切片时长（过短视为假回合）
_PREP_AFTER_RESULT_SEC = 6.0        # 结算后等待下回合准备的窗口（结算画面 5s 倒计时）
_MIN_PREP_AFTER_COMBAT_SEC = 30.0   # 无结算信号时 prep 闭合所需最小交战时长
_PREP_RUN_FRAMES = 4                # 无结算信号时 prep 连续帧数要求（防交战尾段误读）
_MIDSTREAM_STREAK = 3        # 中段切入：连续 N 帧交战钟且递减才开局
_MIDSTREAM_DECREASE_SEC = 1.0
# 边界局部密扫：粗扫（1fps）定位候选后，±3s @10fps 精确定位转换首帧
_REFINE_WINDOW_SEC = 3.0
_REFINE_FPS = 10.0
_REFINE_RUN_FRAMES = 3       # 密扫连续帧确认阈值
# pending 回合（伪造出点）等待真准备信号的升级窗口
_PENDING_UPGRADE_WINDOW_SEC = 60.0
# 结算后 ≥此时长的非游戏段标注为回放（赛事流特征）
_REPLAY_MIN_SEC = 5.0
# SETTLE 内判定"新回合交战钟"的最小读数（残余外推钟通常 <90）
_NEW_ROUND_CLOCK_MIN = 85.0
# SETTLE 内距结算超过此时长后，任意交战钟均可认定新回合（买枪+开局已过）
_NEW_ROUND_AFTER_RESULT_SEC = 45.0
_CENTER_BANNER_SCALE = 3     # 中央横幅放大倍数（小图 OCR 对中文不稳）
_TOP_BAND_RATIO = 0.12       # 顶部条占帧高比例
# 中央横幅竖带覆盖顶部买枪横幅（y≈0.09-0.28）与屏幕中央结算横幅（y≈0.38-0.62）
_CENTER_CROP_RATIO = (0.34, 0.09, 0.32, 0.56)

_PREP_BANNER_KEYWORDS = (
    "购买阶段", "准备阶段", "购买",
    "購買階段", "準備階段", "購買",
    "buy", "equip", "prepar", "buy phase",
)

_END_BANNER_KEYWORDS = (
    # 中文（简体）
    "获胜", "胜利", "败北", "失败", "队伍已淘", "队伍已被淘",
    # 繁体客户端 / 港台赛事流
    "戰敗", "勝利", "獲勝", "隊伍已被淘汰", "隊伍已淘汰",
    "輻能核心已引爆", "尖刺已引爆", "尖刺已拆除",
    # 英文
    "victory", "defeat", "eliminated", "clutch", "ace", "triple",
    "spike deton", "spike defus", "time expired",
)


def _get_duration(video_path: str, ffmpeg_path: str) -> float:
    from lsc.utils.process_launcher import run_hidden

    cmd = [ffmpeg_path, "-i", video_path, "-hide_banner"]
    try:
        result = run_hidden(cmd, capture_output=True, text=True, errors="ignore", timeout=20)
        for line in result.stderr.splitlines():
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", line)
            if m:
                return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception as exc:  # noqa: BLE001
        _log.warning("时长探测失败: %s", exc)
    return 0.0


def _apply_phase_cycle_prior(labels: list[str]) -> list[str]:
    """相位循环先验平滑：交战阶段是唯一确定相位。

    - 删短噪：≤2 帧孤立 combat → 前一帧标签（结算/回放画面误检）
    - 不补缝：交战段之间的 neutral 保持原样（非游戏阶段透明；
      FSM 对 neutral 无操作，交战不会因短缝中断）
    """
    out = list(labels)
    n = len(out)
    if n < 3:
        return out

    def is_c(x: str) -> bool:
        return x == "combat"

    i = 0
    while i < n:
        if is_c(out[i]):
            j = i
            while j < n and is_c(out[j]):
                j += 1
            if j - i <= 2:
                prev = out[i - 1] if i > 0 else "neutral"
                for k in range(i, j):
                    out[k] = prev
            i = j
        else:
            i += 1
    return out


def _read_top_anchors(frame_bgr: np.ndarray) -> tuple[float | None, int | None, int | None]:
    """OCR 顶部条：回合计时器（m:ss）+ 左右比分；失败返回 None（绝不为 0）。"""
    if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
        return None, None, None
    try:
        from lsc.analyzer.ocr_detector import _get_ocr

        ocr = _get_ocr()
    except Exception as exc:  # noqa: BLE001
        _log.debug("top OCR unavailable: %s", exc)
        return None, None, None

    crop_h = max(1, int(frame_bgr.shape[0] * _TOP_BAND_RATIO))
    top = frame_bgr[:crop_h, :]
    try:
        result_ocr, _ = ocr(top)
    except Exception as exc:  # noqa: BLE001
        _log.debug("top OCR failed: %s", exc)
        return None, None, None

    confident_lines = [line for line in (result_ocr or []) if len(line) >= 3 and line[2] >= 0.40]
    if not confident_lines:
        return None, None, None

    timer_seconds: float | None = None
    score_candidates: list[tuple[float, int]] = []
    timer_pattern = re.compile(r"(\d{1,2})\s*:\s*(\d{2})")
    digit_pattern = re.compile(r"\b(\d{1,2})\b")
    for line in confident_lines:
        text = str(line[1])
        timer_match = timer_pattern.search(text)
        if timer_match and timer_seconds is None:
            minutes = int(timer_match.group(1))
            seconds = int(timer_match.group(2))
            if 0 <= seconds < 60:
                timer_seconds = float(minutes * 60 + seconds)
        if timer_match:
            continue
        digit_match = digit_pattern.fullmatch(text.strip())
        if digit_match:
            value = int(digit_match.group(1))
            if 0 <= value <= 30:
                try:
                    points = line[0]
                    x_center = sum(float(p[0]) for p in points) / len(points)
                except (TypeError, ValueError, IndexError, ZeroDivisionError):
                    x_center = float(len(score_candidates))
                score_candidates.append((x_center, value))

    left_score: int | None = None
    right_score: int | None = None
    if len(score_candidates) >= 2:
        ordered = sorted(score_candidates, key=lambda item: item[0])
        left_score = ordered[0][1]
        right_score = ordered[-1][1]
    if timer_seconds is None and left_score is None and right_score is None:
        return None, None, None
    return timer_seconds, left_score, right_score


def _read_center_banner(frame_bgr: np.ndarray) -> tuple[bool, bool]:
    """OCR 中央横幅竖带：返回 (prep_banner, end_banner)。"""
    h, w = frame_bgr.shape[:2]
    x = int(w * _CENTER_CROP_RATIO[0])
    y = int(h * _CENTER_CROP_RATIO[1])
    bw = int(w * _CENTER_CROP_RATIO[2])
    bh = int(h * _CENTER_CROP_RATIO[3])
    crop = frame_bgr[y : y + bh, x : x + bw]
    if crop.size == 0:
        return False, False
    try:
        import cv2

        crop = cv2.resize(crop, (bw * _CENTER_BANNER_SCALE, bh * _CENTER_BANNER_SCALE))
    except ImportError:
        pass
    try:
        from lsc.analyzer.ocr_detector import _get_ocr

        ocr = _get_ocr()
        result_ocr, _ = ocr(crop)
    except Exception as exc:  # noqa: BLE001
        _log.debug("center banner OCR failed: %s", exc)
        return False, False
    text = " ".join(
        str(line[1]) for line in (result_ocr or []) if len(line) >= 3 and line[2] >= 0.40
    )
    if not text:
        return False, False
    lower = text.lower()
    prep = any(k in lower for k in _PREP_BANNER_KEYWORDS)
    end = any(k in lower for k in _END_BANNER_KEYWORDS)
    if prep or end:
        _log.debug("center_banner ts_text=%s prep=%s end=%s", text[:60], prep, end)
    return prep, end


@dataclass
class _FrameSignals:
    ts: float
    timer: float | None
    left: int | None
    right: int | None
    prep_banner: bool
    end_banner: bool


class _State(Enum):
    WAIT = "wait"      # 等准备信号，或中段切入直接见交战钟
    PREP = "prep"      # 准备阶段已确认，等交战开始
    COMBAT = "combat"  # 交战已开（入点已记录）
    SETTLE = "settle"  # 结算/回放中，等下一回合准备（出点）


class OcrRoundFSM:
    """纯 OCR 相位状态机。

    入点 = 交战第一帧（PREP→COMBAT 或中段切入）；出点 = 下一回合准备第一帧。
    只有 combat 标签推进开局，结算/回放/非游戏不改变顺序约束。
    出点契约：只有 next_prep 闭合（真·下回合准备第一帧）才标记 vision_confirmed，
    超时/下一交战/扫描结束的伪造出点一律 pending（等待后续窗口确认升级）。
    """

    def __init__(self) -> None:
        self._state = _State.WAIT
        self._combat_start: float | None = None
        self._result_ts: float | None = None
        self._settle_start: float | None = None
        self._prep_run = 0
        self._prep_run_start_ts: float | None = None
        # 中段切入：WAIT 内交战钟连续确认
        self._mid_streak = 0
        self._mid_first_timer: float | None = None
        self._mid_start_ts: float | None = None

    def clone(self) -> OcrRoundFSM:
        other = OcrRoundFSM()
        other.__dict__.update(self.__dict__)
        return other

    def feed(self, label: str, ts: float, timer: float | None) -> list[dict[str, Any]]:
        """推进一帧，返回本帧新闭合的回合（可能多个，正常 0/1）。"""
        closed: list[dict[str, Any]] = []

        if self._state == _State.WAIT:
            if label == "prep":
                self._state = _State.PREP
            elif label == "combat":
                # 中段切入：交战钟连续确认且递减才开局
                if timer is not None and _is_combat_timer(timer):
                    if self._mid_streak == 0:
                        self._mid_streak = 1
                        self._mid_first_timer = float(timer)
                        self._mid_start_ts = ts
                    else:
                        self._mid_streak += 1
                    countdown_ok = (
                        self._mid_first_timer is not None
                        and float(self._mid_first_timer) - float(timer) >= _MIDSTREAM_DECREASE_SEC
                    )
                    if self._mid_streak >= _MIDSTREAM_STREAK and countdown_ok:
                        self._open_combat(
                            self._mid_start_ts if self._mid_start_ts is not None else ts
                        )
                        self._reset_mid()
                else:
                    self._reset_mid()
            return closed

        if self._state == _State.PREP:
            if label == "combat":
                self._open_combat(ts)
            return closed

        if self._state == _State.COMBAT:
            if self._combat_start is not None and ts - self._combat_start > _MAX_OPEN_SEC:
                close = self._close(end=ts, end_by="max_open_close")
                if close is not None:
                    closed.append(close)
                self._state = _State.WAIT
                return closed
            if label == "settle":
                self._state = _State.SETTLE
                self._result_ts = ts
                self._settle_start = ts
                self._reset_prep_run()
                return closed
            if label == "prep":
                # 出点=下回合准备阶段第一帧。防两类误判：
                # 1) 结算画面 5s 倒计时（距 result <6s 时忽略）；
                # 2) 交战尾段倒计时降到 ≤45s 且锚点已 stale（无 result 时
                #    要求连续 prep 游程 ≥4 帧且距交战开始 ≥30s）。
                _since_result = (ts - self._result_ts) if self._result_ts is not None else None
                if _since_result is not None and _since_result >= _PREP_AFTER_RESULT_SEC:
                    self._reset_prep_run()
                    close = self._close(end=ts, end_by="next_prep")
                    if close is not None:
                        closed.append(close)
                    self._state = _State.PREP
                elif _since_result is None and self._combat_start is not None:
                    if ts - self._combat_start >= _MIN_PREP_AFTER_COMBAT_SEC:
                        # 连续 prep 游程：记录首帧真实 PTS，非 prep 帧清零
                        if self._prep_run_start_ts is None:
                            self._prep_run_start_ts = ts
                        self._prep_run += 1
                        if self._prep_run >= _PREP_RUN_FRAMES:
                            close = self._close(end=self._prep_run_start_ts, end_by="next_prep")
                            if close is not None:
                                closed.append(close)
                            self._state = _State.PREP
                            self._reset_prep_run()
                    else:
                        self._reset_prep_run()
            else:
                # combat/neutral 等非 prep 帧：准备游程不连续，清零
                self._reset_prep_run()
            return closed

        if self._state == _State.SETTLE:
            if label == "prep":
                # 结算画面 5s 倒计时不得当准备阶段（距 result <6s 忽略）
                _since_result = (ts - self._result_ts) if self._result_ts is not None else None
                if _since_result is None or _since_result >= _PREP_AFTER_RESULT_SEC:
                    close = self._close(end=ts, end_by="next_prep")
                    if close is not None:
                        closed.append(close)
                    self._state = _State.PREP
                return closed
            if label == "combat":
                # 错过准备信号直接见新交战钟：出点=新交战首帧（宁长勿短）。
                # 距结算 <6s 的残余交战钟（stale 后尾段）不是新回合，忽略；
                # 残余钟还可能是结算时的递减外推（40-90s 余量，可持续数十秒），
                # 只有"满钟"（≈99）或距结算 ≥45s（买枪+开局已过）才认定新回合。
                _since_result = (ts - self._result_ts) if self._result_ts is not None else None
                _fresh_clock = timer is not None and float(timer) >= _NEW_ROUND_CLOCK_MIN
                _late_enough = _since_result is not None and _since_result >= _NEW_ROUND_AFTER_RESULT_SEC
                if (_fresh_clock or _late_enough) and (
                    _since_result is None or _since_result >= _PREP_AFTER_RESULT_SEC
                ):
                    close = self._close(end=ts, end_by="next_combat")
                    if close is not None:
                        closed.append(close)
                    self._open_combat(ts)
                return closed
            if self._settle_start is not None and ts - self._settle_start > _MAX_SETTLE_SEC:
                end = (self._result_ts + 5.0) if self._result_ts is not None else ts
                close = self._close(end=end, end_by="open_tail")
                if close is not None:
                    closed.append(close)
                self._state = _State.WAIT
            return closed

        return closed

    def force_close(self, end_ts: float) -> list[dict[str, Any]]:
        """收尾：扫描结束仍处于 COMBAT/SETTLE 时强制闭合，保留产出。"""
        if self._state not in (_State.COMBAT, _State.SETTLE):
            return []
        closed = self._close(end=end_ts, end_by="open_tail")
        self._state = _State.WAIT
        return [closed] if closed else []

    def _open_combat(self, ts: float) -> None:
        self._state = _State.COMBAT
        self._combat_start = ts
        self._result_ts = None
        self._settle_start = None
        self._reset_prep_run()

    def _reset_prep_run(self) -> None:
        self._prep_run = 0
        self._prep_run_start_ts = None

    def _close(self, *, end: float, end_by: str) -> dict[str, Any] | None:
        start = self._combat_start
        if start is None or end - start < _MIN_ROUND_SEC:
            return None
        # 出点契约：只有真·下回合准备第一帧（next_prep）才算确认；
        # 超时/下一交战/扫描结束的伪造出点一律 pending（等待升级或人工确认）
        out = {
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "reason": "回合交战阶段",
            "phase": "combat",
            "boundary_source": BOUNDARY_SOURCE,
            "confirm_status": "vision_confirmed" if end_by == "next_prep" else "pending",
            "start_by": "ocr_combat",
            "end_by": end_by,
            "score": 0.9,
        }
        if self._result_ts is not None:
            out["result_ts"] = round(float(self._result_ts), 3)
        return out

    def _reset_mid(self) -> None:
        self._mid_streak = 0
        self._mid_first_timer = None
        self._mid_start_ts = None


def _is_combat_timer(timer: float | None) -> bool:
    return timer is not None and BUY_TIMER_MAX_SEC < float(timer) <= _OCR_TIMER_MAX_PLAUSIBLE_SEC


def _is_prep_timer(timer: float | None) -> bool:
    return timer is not None and 0.0 < float(timer) <= BUY_TIMER_MAX_SEC


def _round_key(start: float) -> str:
    """10s 桶回合键（与消费端 _valorant_round_key 一致）：边界跨整数秒漂移时键稳定。"""
    return f"round-{int(round(float(start) / 10.0)):06d}"


def _refine_boundary_ts(
    video_path: str,
    ffmpeg_path: str,
    center_ts: float,
    target: str,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> float | None:
    """边界局部密扫：粗扫候选 ±3s @10fps，找连续 ≥3 帧目标标签游程的首帧真实 PTS。

    target="combat"：交战钟（>45s）首现帧；target="prep"：准备信号（≤45s 或横幅）首现帧。
    密扫失败返回 None（保留粗扫值，宁用粗值不丢回合）。
    """
    t0 = max(0.0, float(center_ts) - _REFINE_WINDOW_SEC)
    t1 = float(center_ts) + _REFINE_WINDOW_SEC
    try:
        frames = extract_frames_cancellable(
            video_path,
            start_sec=t0,
            end_sec=t1,
            fps=_REFINE_FPS,
            ffmpeg_path=ffmpeg_path,
            cancel_check=cancel_check,
            overlap_sec=0.0,
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("边界密扫抽帧失败: %s", exc)
        return None

    run = 0
    run_start: float | None = None
    for ts, img in frames:
        if cancel_check and cancel_check():
            return None
        try:
            timer, _, _ = _read_top_anchors(img)
            prep_banner, _ = _read_center_banner(img)
        except Exception as exc:  # noqa: BLE001
            _log.debug("边界密扫 OCR 失败: %s", exc)
            run = 0
            run_start = None
            continue
        if target == "combat":
            hit = timer is not None and _is_combat_timer(timer)
        else:
            hit = prep_banner or (timer is not None and _is_prep_timer(timer))
        if hit:
            if run == 0:
                run_start = ts
            run += 1
            if run >= _REFINE_RUN_FRAMES:
                return run_start
        else:
            run = 0
            run_start = None
    return None


def _annotate_replay(round_data: dict[str, Any], labels: list[tuple[float, str, float | None]]) -> None:
    """结算后 ≥5s 的 neutral 段标注为回放（赛事流特征），非游戏阶段透明。"""
    result_ts = round_data.get("result_ts")
    if result_ts is None:
        return
    end = float(round_data["end"])
    segs: list[list[float]] = []
    run_start: float | None = None
    last_neutral_ts: float | None = None
    for ts, label, _ in labels:
        if ts < float(result_ts) or ts > end:
            continue
        if label == "neutral":
            if run_start is None:
                run_start = ts
            last_neutral_ts = ts
        else:
            if (
                run_start is not None
                and last_neutral_ts is not None
                and last_neutral_ts - run_start >= _REPLAY_MIN_SEC
            ):
                segs.append([round(run_start, 3), round(last_neutral_ts, 3)])
            run_start = None
            last_neutral_ts = None
    if (
        run_start is not None
        and last_neutral_ts is not None
        and last_neutral_ts - run_start >= _REPLAY_MIN_SEC
    ):
        segs.append([round(run_start, 3), round(last_neutral_ts, 3)])
    if segs:
        round_data["replay_segments"] = segs


def detect_valorant_rounds_ocr(
    video_path: str,
    *,
    time_range: tuple[float, float] | None = None,
    ffmpeg_path: str = "ffmpeg",
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[str, float, str], None] | None = None,
    runtime_state: dict[str, Any] | None = None,
    finalize: bool = False,
    source_profile: str | None = None,
) -> list[dict[str, Any]]:
    """1fps 粗扫 → 双区域 OCR → 相位序列 → 循环先验 → 轻量 FSM → 边界密扫。

    持续分析与录制后全量分析共用；增量窗口经 runtime_state 跨 tick 持久化
    （FSM、交战钟锚点、计时器外推基准、比分确认、last_processed_ts 去重、
    pending 回合待升级列表）。
    source_profile: "pov" / "broadcast"（赛事流结算后长非游戏段标注为回放）。
    """
    if not os.path.isfile(video_path):
        _log.warning("视频文件不存在: %s", video_path)
        return []

    if time_range is None:
        duration = _get_duration(video_path, ffmpeg_path)
        if duration <= 0:
            return []
        scan_start, scan_end = 0.0, duration
    else:
        scan_start, scan_end = time_range
    if scan_end <= scan_start:
        return []

    state = runtime_state if runtime_state is not None else {}
    last_processed_ts = float(state.get("last_processed_ts", -1.0))

    # OCR 预热：避免首窗懒加载撞上推理引擎争用导致读取率 0
    try:
        from lsc.analyzer.ocr_detector import _get_ocr

        _get_ocr()
    except Exception as exc:  # noqa: BLE001
        _log.warning("OCR 预热失败（扫描中重试）: %s", exc)

    if progress_callback:
        progress_callback("ocr", 0.0, "OCR 抽帧")

    from lsc.utils.cancellable_ffmpeg import FFmpegCancelled

    frames = extract_frames_cancellable(
        video_path,
        start_sec=scan_start,
        end_sec=scan_end,
        fps=1.0,
        ffmpeg_path=ffmpeg_path,
        cancel_check=cancel_check,
        overlap_sec=2.0,
    )
    frames = [item for item in frames if item[0] > last_processed_ts + 0.001]
    if not frames:
        return []

    fsm = state.get("ocr_fsm")
    fsm = fsm.clone() if isinstance(fsm, OcrRoundFSM) else OcrRoundFSM()
    # 信号持久化（相近相似原则的载体）
    last_timer = state.get("last_timer")
    last_timer_ts = float(state.get("last_timer_ts", -1.0))
    last_raw_timer = state.get("last_raw_timer")
    last_raw_ts = float(state.get("last_raw_ts", -1.0))
    anchor = state.get("combat_anchor")  # (timer, ts)
    score_pending: tuple[int, int] | None = state.get("score_pending")
    prev_left = state.get("prev_left")
    prev_right = state.get("prev_right")
    timer_streak = int(state.get("timer_streak", 0) or 0)
    timer_streak_val: float | None = state.get("timer_streak_val")

    labels: list[tuple[float, str, float | None]] = []
    closed_rounds: list[dict[str, Any]] = []
    first_prep_ts: float | None = None  # 本窗口最早的准备信号帧（pending 升级锚点）

    for _, (ts, img) in enumerate(frames):
        if cancel_check and cancel_check():
            raise FFmpegCancelled("cancelled during ocr scan")
        raw_timer, left, right = _read_top_anchors(img)
        prep_banner, end_banner = _read_center_banner(img)

        # ── 计时器可信度（相近相似原则） ──
        timer = raw_timer
        extrapolated: float | None = None
        if last_timer is not None and last_timer_ts > 0:
            extrapolated = float(last_timer) - (ts - last_timer_ts)
            if extrapolated <= 0.0:
                extrapolated = None
        # 冻结读数：锚点解除后读数几乎不变 → 回放/非实时画面残留，
        # 不得建立锚点、不得判准备（回放画面冻结的 90s+ 钟会误开假回合）
        frozen = (
            anchor is None
            and raw_timer is not None
            and last_raw_timer is not None
            and last_raw_ts > 0
            and ts - last_raw_ts > 2.5
            and abs(float(raw_timer) - float(last_raw_timer)) < 0.5
        )
        if raw_timer is not None and not frozen:
            if _is_combat_timer(raw_timer):
                anchor = (float(raw_timer), ts)
            elif (
                anchor is not None
                and extrapolated is not None
                and float(raw_timer) < float(extrapolated) - _OCR_TIMER_JUMP_TOL_SEC
            ):
                # 明显跳变重置（远小于外推轨迹）：回合结束，解除锚点
                anchor = None
            # 跳向更大值且偏差超容差 → 误读丢弃
            if (
                extrapolated is not None
                and float(raw_timer) > float(extrapolated) + _OCR_TIMER_JUMP_TOL_SEC
            ):
                timer = None
            else:
                last_timer = float(raw_timer)
                last_timer_ts = ts
            last_raw_timer = float(raw_timer)
            last_raw_ts = ts
        elif extrapolated is not None:
            timer = extrapolated  # 外推 1:1 走秒
        else:
            timer = None

        # 锚点 stale：长时间读不到计时器，外推不再可信
        if anchor is not None:
            anchor_timer, anchor_ts = anchor
            if (last_timer_ts > 0 and ts - last_timer_ts > _OCR_TIMER_STALE_SEC) or (
                anchor_timer - (ts - anchor_ts) <= 0.0
            ):
                anchor = None

        # 两帧确认：timer 相位需要连续 2 帧一致读数（递减轨迹中 val 每帧更新）
        timer_phase: str | None = None
        if timer is not None and not frozen:
            new_val = float(timer)
            if timer_streak_val is not None and abs(new_val - timer_streak_val) <= 1.0:
                timer_streak += 1
                timer_streak_val = new_val
            else:
                timer_streak = 1
                timer_streak_val = new_val
            if timer_streak >= 2:
                if _is_combat_timer(new_val):
                    timer_phase = "combat"
                elif _is_prep_timer(new_val):
                    timer_phase = "prep"
        else:
            timer_streak = 0
            timer_streak_val = None

        # 比分两帧确认 → 结算信号（变化帧建立 pending，下一帧同值才确认）
        score_confirmed = False
        if left is not None and right is not None:
            if score_pending is not None:
                if (left, right) == score_pending:
                    score_confirmed = True
                    score_pending = None
                else:
                    # 读数变化/回落 → 上一帧是误读，清除待确认
                    score_pending = None
            elif (prev_left, prev_right) != (left, right):
                score_pending = (left, right)
        elif score_pending is not None and (left is not None or right is not None):
            score_pending = None
        if left is not None:
            prev_left = left
        if right is not None:
            prev_right = right

        # ── 相位判定：锚点存活 = 交战延续（交战是唯一确定相位） ──
        # 结算信号（比分两帧确认/结算横幅）优先于锚点：提前团灭时钟未走完，
        # 结算画面仍须解除锚点进入结算，否则回合被压到锚点归零才结束。
        if end_banner or score_confirmed:
            anchor = None
            label = "settle"
        elif anchor is not None:
            label = "combat"
        elif prep_banner:
            label = "prep"
        elif timer_phase is not None:
            label = timer_phase
        else:
            label = "neutral"
        labels.append((ts, label, timer))
        if label == "prep" and first_prep_ts is None:
            first_prep_ts = ts

        _log.debug("ocr_label ts=%.1f label=%s timer=%s raw=%s anchor=%s",
                   ts, label, f"{timer:.0f}" if timer else None,
                   f"{raw_timer:.0f}" if raw_timer else None,
                   f"{anchor[0]:.0f}@{anchor[1]:.1f}" if anchor else None)

    # 循环先验平滑（帧级，仅删孤立 combat 噪点，不补缝——非游戏阶段透明）
    smoothed = _apply_phase_cycle_prior([label for _, label, _ in labels])
    for (ts, _, timer), label in zip(labels, smoothed, strict=True):
        closed = fsm.feed(label, ts, timer)
        if closed:
            closed_rounds.extend(closed)

    # 收尾：扫描末端强制闭合未结束回合（伪造出点 → pending）
    if finalize:
        closed = fsm.force_close(end_ts=float(scan_end))
        if closed:
            closed_rounds.extend(closed)

    # ── pending 跨窗口自动升级 ──
    # 伪造出点（超时/下一交战/收尾）的回合登记到 runtime_state；后续窗口
    # 检测到真准备信号且落在 (end, end+60s) 内时，产出升级事件（同 round_key，
    # 出点=真准备帧，vision_confirmed）。
    pending_out = dict(state.get("pending_out_rounds") or {})
    for r in closed_rounds:
        if r.get("confirm_status") == "vision_confirmed":
            pending_out.pop(_round_key(float(r["start"])), None)
        else:
            pending_out[_round_key(float(r["start"]))] = {
                "start": float(r["start"]),
                "end": float(r["end"]),
                "result_ts": r.get("result_ts"),
            }
    if first_prep_ts is not None and pending_out:
        upgraded: list[dict[str, Any]] = []
        for key, p in list(pending_out.items()):
            if p["end"] < first_prep_ts <= p["end"] + _PENDING_UPGRADE_WINDOW_SEC:
                upgraded.append({
                    "start": round(float(p["start"]), 3),
                    "end": round(float(first_prep_ts), 3),
                    "reason": "回合交战阶段",
                    "phase": "combat",
                    "boundary_source": BOUNDARY_SOURCE,
                    "confirm_status": "vision_confirmed",
                    "start_by": "ocr_combat",
                    "end_by": "next_prep",
                    "score": 0.9,
                    "round_key": key,
                    "upgraded": True,
                })
                if p.get("result_ts") is not None:
                    upgraded[-1]["result_ts"] = round(float(p["result_ts"]), 3)
                del pending_out[key]
                _log.info("pending 回合升级: round_key=%s end %.1f -> %.1f (next_prep)",
                          key, p["end"], first_prep_ts)
        closed_rounds.extend(upgraded)

    # ── 边界局部密扫：帧级精度（真实视频帧 PTS，非 1fps 网格） ──
    refine_targets = [r for r in closed_rounds if not r.get("upgraded")]
    total_refine = len(refine_targets)
    for idx, r in enumerate(refine_targets, 1):
        if cancel_check and cancel_check():
            break
        if progress_callback and total_refine:
            progress_callback("refine", idx / max(total_refine, 1), f"边界精修 {idx}/{total_refine}")
        start_ts = _refine_boundary_ts(
            video_path, ffmpeg_path, float(r["start"]), "combat", cancel_check=cancel_check,
        )
        if start_ts is not None:
            r["start"] = round(start_ts, 3)
        if r.get("confirm_status") == "vision_confirmed" and r.get("end_by") == "next_prep":
            end_ts = _refine_boundary_ts(
                video_path, ffmpeg_path, float(r["end"]), "prep", cancel_check=cancel_check,
            )
            if end_ts is not None and end_ts > float(r["start"]) + _MIN_ROUND_SEC:
                r["end"] = round(end_ts, 3)

    # ── 非游戏阶段透明标注：结算后 ≥5s 的 neutral 段 = 回放 ──
    for r in closed_rounds:
        _annotate_replay(r, labels)

    # 回写持久化状态
    state["ocr_fsm"] = fsm
    state["last_timer"] = last_timer
    state["last_timer_ts"] = last_timer_ts
    state["last_raw_timer"] = last_raw_timer
    state["last_raw_ts"] = last_raw_ts
    state["combat_anchor"] = anchor
    state["score_pending"] = score_pending
    state["prev_left"] = prev_left
    state["prev_right"] = prev_right
    state["timer_streak"] = timer_streak
    state["timer_streak_val"] = timer_streak_val
    state["pending_out_rounds"] = pending_out
    state["last_processed_ts"] = max(last_processed_ts, float(frames[-1][0]))

    # ── 相邻回合修整：出点（粗扫/兜底值）不得越过下一回合入点（密扫值） ──
    closed_rounds.sort(key=lambda r: float(r["start"]))
    kept_rounds: list[dict[str, Any]] = []
    for r in closed_rounds:
        if kept_rounds and float(kept_rounds[-1]["end"]) > float(r["start"]):
            kept_rounds[-1]["end"] = round(float(r["start"]), 3)
            if float(kept_rounds[-1]["end"]) - float(kept_rounds[-1]["start"]) < _MIN_ROUND_SEC:
                kept_rounds.pop()  # 修整后过短：丢弃重叠前段
        kept_rounds.append(r)
    closed_rounds = kept_rounds

    for r in closed_rounds:
        if source_profile:
            r["source_profile"] = source_profile

    _log.info("OCR 回合检测: %d 回合, %d 帧 (range=%.1f-%.1f)",
              len(closed_rounds), len(frames), scan_start, scan_end)
    return closed_rounds


_HYBRID_EXTRACT_MAX_WIDTH = 640


def extract_frames_cancellable(
    video_path: str,
    *,
    start_sec: float,
    end_sec: float,
    fps: float,
    ffmpeg_path: str,
    cancel_check: Callable[[], bool] | None = None,
    overlap_sec: float = 2.0,
) -> list[tuple[float, np.ndarray]]:
    """Extract downscaled frames via memory pipe; dedupe by showinfo pts.

    统一抽到 640×360 固定尺寸（非 16:9 letterbox 补边），rawvideo(bgr24)
    内存管道直通 numpy；mjpeg 路径保留为最后兜底。
    """
    from lsc.utils.cancellable_ffmpeg import FFmpegCancelled
    from lsc.utils.process_launcher import prepare_launch

    scan_start = max(0.0, start_sec - overlap_sec)
    scan_end = end_sec + overlap_sec
    scan_duration = max(0.0, scan_end - scan_start)
    if scan_duration <= 0.0:
        return []

    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("opencv-python 未安装，无法解码抽帧") from exc

    _raw_w = int(_HYBRID_EXTRACT_MAX_WIDTH)
    _raw_h = 360
    _raw_frame_bytes = _raw_w * _raw_h * 3

    cpu_vf = (
        f"fps={fps:.3f},"
        f"scale={_raw_w}:{_raw_h}:force_original_aspect_ratio=decrease,"
        f"pad={_raw_w}:{_raw_h}:(ow-iw)/2:(oh-ih)/2,format=bgr24,showinfo"
    )
    gpu_hw, gpu_vf = build_hwaccel_vf(
        cpu_vf,
        gpu_scale_pattern=r"scale=\d+:\d+:force_original_aspect_ratio=decrease",
    )

    def _build_pipe_cmd(vf_str: str, raw: bool) -> list[str]:
        cmd = [
            ffmpeg_path,
            "-y",
            "-loglevel", "info",
            "-ss", f"{scan_start:.3f}",
            "-t", f"{scan_duration:.3f}",
            "-i", video_path,
            "-vf", vf_str,
        ]
        if raw:
            cmd += ["-f", "rawvideo", "pipe:1"]
        else:
            cmd += ["-q:v", "2", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"]
        return cmd

    attempts: list[tuple[list[str], bool]] = []
    if gpu_hw and gpu_vf != cpu_vf:
        gpu_cmd = _build_pipe_cmd(gpu_vf, raw=True)
        attempts.append(([gpu_cmd[0], *gpu_hw, *gpu_cmd[1:]], True))
    hwaccel_args = ffmpeg_hwaccel_args(read_settings_ocr_accel())
    cpu_cmd = _build_pipe_cmd(cpu_vf, raw=True)
    if hwaccel_args:
        attempts.append(([cpu_cmd[0], *hwaccel_args, *cpu_cmd[1:]], True))
    attempts.append((cpu_cmd, True))
    mjpeg_vf = f"fps={fps:.3f},scale={_raw_w}:-2,showinfo"
    attempts.append((_build_pipe_cmd(mjpeg_vf, raw=False), False))

    env, creation_flags, cwd = prepare_launch(ffmpeg_path)
    frame_ts_pattern = re.compile(r"pts_time:(\d+\.?\d*)")

    for attempt_i, (cmd, is_raw) in enumerate(attempts):
        if cancel_check and cancel_check():
            raise FFmpegCancelled("ffmpeg cancelled")
        popen_kwargs: dict = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": env,
        }
        if creation_flags:
            popen_kwargs["creationflags"] = creation_flags
        if cwd:
            popen_kwargs["cwd"] = cwd
        proc = subprocess.Popen(cmd, **popen_kwargs)

        stderr_chunks: list[bytes] = []

        def _read_stderr() -> None:
            if proc.stderr is None:
                return
            while True:
                chunk = proc.stderr.read(8192)
                if not chunk:
                    break
                stderr_chunks.append(chunk)

        import threading as _thr

        stderr_thread = _thr.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        frames: list[tuple[float, np.ndarray]] = []
        buffer = bytearray()
        JPEG_SOI = b"\xff\xd8"
        JPEG_EOI = b"\xff\xd9"
        cancelled = False

        try:
            if proc.stdout is None:
                raise RuntimeError("FFmpeg stdout pipe unavailable")
            while True:
                if cancel_check and cancel_check():
                    cancelled = True
                    break
                chunk = proc.stdout.read(1 << 20 if is_raw else 65536)
                if not chunk:
                    break
                buffer.extend(chunk)
                if is_raw:
                    while len(buffer) >= _raw_frame_bytes:
                        img = np.frombuffer(
                            bytes(buffer[:_raw_frame_bytes]), dtype=np.uint8,
                        ).reshape(_raw_h, _raw_w, 3).copy()
                        del buffer[:_raw_frame_bytes]
                        frames.append((0.0, img))
                    continue
                while True:
                    soi_idx = buffer.find(JPEG_SOI)
                    if soi_idx < 0:
                        buffer.clear()
                        break
                    eoi_idx = buffer.find(JPEG_EOI, soi_idx + 2)
                    if eoi_idx < 0:
                        if soi_idx > 0:
                            del buffer[:soi_idx]
                        break
                    jpeg_data = bytes(buffer[soi_idx : eoi_idx + 2])
                    del buffer[: eoi_idx + 2]
                    img = cv2.imdecode(
                        np.frombuffer(jpeg_data, dtype=np.uint8),
                        cv2.IMREAD_COLOR,
                    )
                    if img is not None:
                        frames.append((0.0, img))
        except (OSError, ValueError):
            pass
        finally:
            if cancelled:
                proc.kill()
            proc.wait(timeout=10)
            stderr_thread.join(timeout=5)

        if cancelled:
            raise FFmpegCancelled("ffmpeg cancelled")

        if proc.returncode != 0 and not frames:
            last_err = b"".join(stderr_chunks).decode("utf-8", errors="replace")[-500:]
            if attempt_i + 1 < len(attempts):
                _log.warning("frame extract hwaccel 失败 (code=%s)，回退软解", proc.returncode)
                continue
            raise RuntimeError(f"frame extract failed rc={proc.returncode}: {last_err}")

        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        precise_timestamps: list[float] = []
        for match in frame_ts_pattern.finditer(stderr_text):
            ts = float(match.group(1))
            if not precise_timestamps or ts > precise_timestamps[-1] + 0.001:
                precise_timestamps.append(ts)

        result: list[tuple[float, np.ndarray]] = []
        for i, (_, img) in enumerate(frames):
            rel_ts = precise_timestamps[i] if i < len(precise_timestamps) else i / max(fps, 0.1)
            result.append((scan_start + rel_ts, img))
        return result

    return []


__all__ = [
    "BOUNDARY_SOURCE",
    "BUY_TIMER_MAX_SEC",
    "OcrRoundFSM",
    "detect_valorant_rounds_ocr",
    "extract_frames_cancellable",
    "_apply_phase_cycle_prior",
]
