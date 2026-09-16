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
import time
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
from lsc.platforms.redaction import redact_text

_log = logging.getLogger(__name__)

BOUNDARY_SOURCE = "valorant_ocr_v1"
# 交战钟下限（沿用历史常量名）：交战计时读数须 >45s，上限 105s。
# ⚠️ 反向不成立：回合计时器在**最后 45 秒**同样读到 ≤45s，所以 ≤45s 读数
# 绝不能再被当作「买枪/准备相位」。实测 2026-09-12 赛事流：该推断在锚点失效的
# 尾段把每个回合的出点提前 ~45s（351.312 的回合被切在 403.125，真实结束 449.875）。
BUY_TIMER_MAX_SEC = 45.0
_OCR_TIMER_MAX_PLAUSIBLE_SEC = 105.0
_OCR_TIMER_JUMP_TOL_SEC = 8.0
_OCR_TIMER_STALE_SEC = 35.0  # 锚点无读数存活上限
_MIN_ROUND_SEC = 10.0        # 最短切片时长（过短视为假回合）
_PREP_AFTER_RESULT_SEC = 6.0        # 结算后等待下回合准备的窗口（结算画面 5s 倒计时）
_MIN_PREP_AFTER_COMBAT_SEC = 30.0   # 无结算信号时 prep 闭合所需最小交战时长
_PREP_RUN_FRAMES = 4                # 无结算信号时 prep 连续帧数要求（防交战尾段误读）
# 抽帧子窗口（秒）：持续分析追赶窗最大 480s @1fps ≈ 330MB 帧驻留（640×360×3 ≈ 0.66MB/帧）。
# 拆成 ≤60s 子窗逐块扫描后峰值降到 ~40MB，跨窗状态经函数局部变量与 runtime_state 传递。
_SUB_WINDOW_SEC = 60.0
_MIDSTREAM_STREAK = 3        # 中段切入：连续 N 帧交战钟且递减才开局
_MIDSTREAM_DECREASE_SEC = 1.0
# 边界局部密扫：粗扫（1fps）定位候选后，±3s @5fps 精确定位转换首帧
_REFINE_WINDOW_SEC = 3.0
_REFINE_END_FALLBACK_WINDOW_SEC = 15.0
_REFINE_FPS = 5.0
_REFINE_RUN_FRAMES = 2       # 密扫连续帧确认阈值（5fps 下 2 帧即 0.4s）
_REFINE_MAX_FRAMES_PER_BOUNDARY = 80  # 密扫单边界最大帧数：超限等距抽样兜底
# 结算后 ≥此时长的非游戏段标注为回放（仅 broadcast 赛事流）
_REPLAY_MIN_SEC = 5.0
_NEW_ROUND_CLOCK_MIN = 85.0
_NEW_ROUND_TIMER_RESET_SEC = 20.0
_NEW_ROUND_AFTER_RESULT_SEC = 45.0
_CENTER_SENTINEL_SEC = 4.0

# ── broadcast_mode 影子对比 ───────────────────────────────────────────
# 赛事流的 FSM 级「回放保护」(`OcrRoundFSM.feed(broadcast_mode=True)`) 目前未接入
# 生产（见 docs/reports/replay-vs-nextcombat-experiment-20260910.md）。切换前先做
# 影子模式：用同一批 OCR 标签并行跑一份 broadcast_mode=True 的 FSM，只记录
# 两份回合列表的差异，**不改变任何生效结果**。开关：环境变量置 1/true/yes/on。
BROADCAST_MODE_SHADOW_ENV = "LSC_VALORANT_BROADCAST_MODE_SHADOW"
_SHADOW_MATCH_TOLERANCE_SEC = 5.0   # 两份回合列表比对时的起点配对容差


def broadcast_mode_shadow_enabled() -> bool:
    """影子模式开关：只记录 broadcast_mode=True 的差异，不改变生效结果。"""
    return os.environ.get(BROADCAST_MODE_SHADOW_ENV, "").strip().lower() in {
        "1", "true", "yes", "on",
    }
_ROI_CACHE_W = 160
_ROI_DIFF_THRESHOLD = 2.0
_ROI_CACHE_BLACKOUT_FRAMES = 2
_CENTER_BANNER_SCALE = 3     # 中央横幅放大倍数（小图 OCR 对中文不稳）
_TOP_BAND_RATIO = 0.12       # POV 顶部条占帧高比例
_CENTER_CROP_RATIO = (0.34, 0.09, 0.32, 0.56)  # POV 中央横幅
# broadcast 画面常有比分板缩放/黑边/赛事包装；保留 POV ROI，同时
# 增加较宽的候选 ROI，避免用单一硬编码裁剪直接把 OCR 证据裁掉。
_BROADCAST_TOP_BAND_RATIOS = (0.12, 0.18)
_BROADCAST_WIDE_ROI_SENTINEL_SEC = 4.0
_BROADCAST_CENTER_CROP_RATIOS = (
    _CENTER_CROP_RATIO,
    (0.20, 0.06, 0.60, 0.72),
)

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
    # 注：曾含 "clutch"/"ace"/"triple"——它们是**解说高光回放的叠加字样**，
    # 会让回放被误判为"回合结束"，已移除（见 A6 与
    # docs/reports/valorant-broadcast-inpoint-rootcause-20260910.md）。
    "victory", "defeat", "eliminated",
    "spike deton", "spike defus", "time expired",
)

# 回放正向识别：命中即**否决** prep/end 判定。赛事流的回放/慢动作水印
# （REPLAY / 重播 / 慢动作）常与被误读的"回合开始/结束"横幅同域出现，
# 靠词表单独判 prep/end 会把回放当成边界。此处只做否决，不新增边界来源。
_REPLAY_BANNER_KEYWORDS = (
    "回放", "重播", "精彩回顾", "慢动作", "慢鏡",
    "replay", "instant replay", "slow motion", "slowmo",
)


def _get_duration(video_path: str, ffmpeg_path: str) -> float:
    from lsc.utils.process_launcher import run_hidden

    cmd = [ffmpeg_path, "-i", video_path, "-hide_banner"]
    try:
        result = run_hidden(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="ignore", timeout=20,
        )
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


def _parse_top_anchor_lines(
    confident_lines: list,
) -> tuple[float | None, int | None, int | None]:
    """从顶部条 OCR 行解析 (timer_seconds, left_score, right_score)。

    与多 ROI 抽帧解耦，供 _read_top_anchors 在每个 ROI 后增量判断是否已拿到
    完整读数（early-exit），以及最终合并解析。行为与原内联解析完全一致。
    """
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


def _read_top_anchors(
    frame_bgr: np.ndarray,
    source_profile: str | None = None,
) -> tuple[float | None, int | None, int | None]:
    """OCR 顶部条：计时器（m:ss）+ 左右比分；失败返回 None（绝不为 0）。

    broadcast 使用两个高度候选，但仍优先保留原 POV ROI；这样赛事包装/黑边
    改变有效坐标时不会把唯一证据裁掉。
    """
    if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
        return None, None, None
    try:
        from lsc.analyzer.ocr_detector import _get_ocr

        ocr = _get_ocr()
    except Exception as exc:  # noqa: BLE001
        _log.debug("top OCR unavailable: %s", exc)
        return None, None, None

    profile = str(source_profile or "pov").lower()
    ratios = (
        _BROADCAST_TOP_BAND_RATIOS
        if profile == "broadcast"
        else (_TOP_BAND_RATIO,)
    )
    all_lines: list = []
    for index, ratio in enumerate(ratios):
        crop_h = max(1, int(frame_bgr.shape[0] * ratio))
        try:
            result_ocr, _ = ocr(frame_bgr[:crop_h, :])
        except Exception as exc:  # noqa: BLE001
            _log.debug("top OCR failed profile=%s ratio=%.2f: %s", profile, ratio, exc)
            continue
        all_lines.extend(
            line for line in (result_ocr or [])
            if len(line) >= 3 and line[2] >= 0.40
        )
        # early-exit：主 ROI（首个、最紧的 POV ROI）已拿到完整读数（计时器 +
        # 双比分）时，跳过更宽的候选 ROI。宽 ROI 是为赛事黑边/包装 shift
        # 兜底；主 ROI 完整时它只会多一次昂贵 OCR 并可能引入冗余行污染比分
        # 排序。读数不完整时才继续跑宽 ROI 兜底（与原多 ROI 行为一致）。
        if index < len(ratios) - 1:
            _t, _l, _r = _parse_top_anchor_lines(all_lines)
            if _t is not None and _l is not None and _r is not None:
                break

    return _parse_top_anchor_lines(all_lines)


def _read_center_banner(
    frame_bgr: np.ndarray,
    source_profile: str | None = None,
) -> tuple[bool, bool]:
    """OCR 中央横幅候选 ROI：返回 (prep_banner, end_banner)。"""
    h, w = frame_bgr.shape[:2]
    profile = str(source_profile or "pov").lower()
    ratios = (
        _BROADCAST_CENTER_CROP_RATIOS
        if profile == "broadcast"
        else (_CENTER_CROP_RATIO,)
    )
    try:
        from lsc.analyzer.ocr_detector import _get_ocr

        ocr = _get_ocr()
    except Exception as exc:  # noqa: BLE001
        _log.debug("center banner OCR unavailable: %s", exc)
        return False, False

    texts: list[str] = []
    for index, ratio in enumerate(ratios):
        x = int(w * ratio[0])
        y = int(h * ratio[1])
        bw = int(w * ratio[2])
        bh = int(h * ratio[3])
        crop = frame_bgr[y : y + bh, x : x + bw]
        if crop.size == 0:
            continue
        try:
            import cv2

            crop = cv2.resize(crop, (bw * _CENTER_BANNER_SCALE, bh * _CENTER_BANNER_SCALE))
        except ImportError:
            pass
        try:
            result_ocr, _ = ocr(crop)
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "center banner OCR failed profile=%s ratio=%s: %s",
                profile,
                ratio,
                exc,
            )
            continue
        texts.extend(
            str(line[1])
            for line in (result_ocr or [])
            if len(line) >= 3 and line[2] >= 0.40
        )
        # early-exit：首个 ROI 已命中横幅关键词即跳过更宽候选。关键词是
        # any 匹配，更宽 ROI 只会重复命中同一横幅；未命中才继续跑宽 ROI
        # 兜底赛事包装/黑边 shift（与原多 ROI 行为一致）。
        if index < len(ratios) - 1 and texts:
            _joined = " ".join(texts).lower()
            # 回放水印不算边界命中，不得据此早退：更宽 ROI 里可能才是真横幅。
            if any(k in _joined for k in _REPLAY_BANNER_KEYWORDS):
                continue
            if (
                any(k in _joined for k in _PREP_BANNER_KEYWORDS)
                or any(k in _joined for k in _END_BANNER_KEYWORDS)
            ):
                break

    text = " ".join(texts)
    if not text:
        return False, False
    lower = text.lower()
    # 回放水印优先：命中回放词即否决 prep/end。赛事流的 REPLAY/重播/慢动作字样
    # 常与"回合开始/结束"同域出现（解说高光回放叠加），单靠词表会把回放当边界。
    if any(k in lower for k in _REPLAY_BANNER_KEYWORDS):
        _log.debug("center_banner 回放否决 ts_text=%s", text[:60])
        return False, False
    prep = any(k in lower for k in _PREP_BANNER_KEYWORDS)
    end = any(k in lower for k in _END_BANNER_KEYWORDS)
    if prep or end:
        _log.debug("center_banner ts_text=%s prep=%s end=%s", text[:60], prep, end)
    return prep, end


def _read_top_anchors_for_profile(
    frame_bgr: np.ndarray,
    source_profile: str | None,
    *,
    use_wide_fallback: bool = True,
) -> tuple[float | None, int | None, int | None]:
    """Call the profile-aware OCR reader while keeping legacy test hooks valid."""
    if str(source_profile or "").lower() != "broadcast":
        return _read_top_anchors(frame_bgr)
    try:
        return _read_top_anchors(
            frame_bgr,
            "broadcast" if use_wide_fallback else "broadcast_fast",
        )
    except TypeError as exc:
        # Existing plugins/tests may inject the historical one-argument reader.
        if "argument" not in str(exc).lower() and "positional" not in str(exc).lower():
            raise
        return _read_top_anchors(frame_bgr)


def _read_center_banner_for_profile(
    frame_bgr: np.ndarray,
    source_profile: str | None,
) -> tuple[bool, bool]:
    if str(source_profile or "").lower() != "broadcast":
        return _read_center_banner(frame_bgr)
    try:
        return _read_center_banner(frame_bgr, "broadcast")
    except TypeError as exc:
        if "argument" not in str(exc).lower() and "positional" not in str(exc).lower():
            raise
        return _read_center_banner(frame_bgr)


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

    入点 = 交战第一帧（PREP→COMBAT 或中段切入）；出点优先 = 下一回合准备第一帧。
    出点契约：有真·下回合准备（next_prep）→ vision_confirmed；
    SETTLE 错过准备直接见新交战钟 → next_combat 降级闭合（pending，仍入列）；
    finalize 收尾例外 → open_tail+pending。
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
        # 用于识别“错过准备阶段后重新出现的满回合计时器”，避免把
        # 同一回合内的正常交战钟误认为新回合。
        self._last_raw_timer: float | None = None

    def clone(self) -> OcrRoundFSM:
        other = OcrRoundFSM()
        other.__dict__.update(self.__dict__)
        return other

    def feed(
        self,
        label: str,
        ts: float,
        timer: float | None,
        timer_raw: bool = False,
        cand_ts: float | None = None,
        prep_banner: bool | None = None,
        broadcast_mode: bool = False,
    ) -> list[dict[str, Any]]:
        """推进一帧，返回本帧新闭合的回合（正常 0/1）。

        timer_raw：本帧计时器是否来自原始 OCR 读数（外推值不得触发新回合判定）。
        cand_ts：交战钟连续确认的首帧真实 PTS（入点回溯，消除两帧确认带来的延迟）。
        prep_banner：本帧是否由中央“购买/准备阶段”横幅确认；直接调用 FSM 的
            旧测试不传此值时保持兼容，真实 OCR 路径会传入实际值。
        """
        closed: list[dict[str, Any]] = []

        fresh_clock = (
            timer_raw
            and timer is not None
            and self._last_raw_timer is not None
            and float(timer) >= _NEW_ROUND_CLOCK_MIN
            and float(timer) - float(self._last_raw_timer) >= _NEW_ROUND_TIMER_RESET_SEC
            and self._combat_start is not None
            and ts - self._combat_start >= _MIN_PREP_AFTER_COMBAT_SEC
        )
        if timer_raw and timer is not None:
            self._last_raw_timer = float(timer)

        if self._state == _State.WAIT:
            if label == "prep":
                self._state = _State.PREP
            elif label == "combat":
                # 中段切入：交战钟连续确认且递减才开局
                if timer is not None and _is_combat_timer(timer):
                    if self._mid_streak == 0:
                        self._mid_streak = 1
                        self._mid_first_timer = float(timer)
                        self._mid_start_ts = cand_ts if cand_ts is not None else ts
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
                elif timer is None and cand_ts is not None:
                    # 有效计时器被跳变保护置空（结算后残余钟 → 新回合满钟的过渡），
                    # 此时只能靠原始读数判据：cand_ts 非空 = 原始交战钟已连续确认
                    # （上游 combat_raw_streak ≥2），再要求 _MIDSTREAM_STREAK 帧。
                    # 删除「≤45s 计时器 = prep」后，原先借 PREP 状态开局的后门消失，
                    # 没有这条兜底，跨窗/结算后的新回合会一直开不出来。
                    if self._mid_streak == 0:
                        self._mid_start_ts = cand_ts
                    self._mid_streak += 1
                    if self._mid_streak >= _MIDSTREAM_STREAK:
                        self._open_combat(
                            self._mid_start_ts if self._mid_start_ts is not None else ts
                        )
                        self._reset_mid()
                else:
                    self._reset_mid()
            return closed

        if self._state == _State.PREP:
            if label == "combat":
                self._open_combat(cand_ts if cand_ts is not None else ts)
            return closed

        if self._state == _State.COMBAT:
            if label == "combat" and fresh_clock:
                if broadcast_mode:
                    _log.info(
                        "赛事回放保护：忽略未伴随准备阶段的新交战钟 (state=COMBAT, ts=%.1f, timer=%.1f)",
                        ts,
                        float(timer) if timer is not None else -1.0,
                    )
                    return closed
                # 准备阶段漏检时，新的满回合计时器是比低计时器更可靠的
                # 分界信号。旧回合以 pending 闭合，避免提前确认或吞掉新回合。
                close = self._close(
                    end=cand_ts if cand_ts is not None else ts,
                    end_by="next_combat",
                )
                if close is not None:
                    closed.append(close)
                self._open_combat(cand_ts if cand_ts is not None else ts)
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
                if broadcast_mode:
                    if not timer_raw or timer is None:
                        return closed
                    _since_result = (ts - self._result_ts) if self._result_ts is not None else None
                    _fresh_clock = fresh_clock
                    _late_raw_combat = (
                        _is_combat_timer(timer)
                        and _since_result is not None
                        and _since_result >= _NEW_ROUND_AFTER_RESULT_SEC
                    )
                    if (_fresh_clock or _late_raw_combat) and (
                        _since_result is None or _since_result >= _PREP_AFTER_RESULT_SEC
                    ):
                        close = self._close(end=cand_ts if cand_ts is not None else ts, end_by="next_combat")
                        if close is not None:
                            closed.append(close)
                            _log.info(
                                "赛事 SETTLE 未见准备阶段，降级闭合 next_combat (ts=%.1f, timer=%.1f)",
                                ts,
                                float(timer),
                            )
                        self._open_combat(cand_ts if cand_ts is not None else ts)
                    return closed
                # 错过准备信号直接见新交战钟：以降级出点 next_combat 闭合旧回合
                # （pending，可入列待调），再开新回合——禁止整回合放弃造成长空窗漏检。
                # 外推残余钟（timer_raw=False）永不触发；须原始读数：
                #   - 满钟 ≥85，或
                #   - 距结算 ≥45s 且仍为交战钟（>45）。
                _since_result = (ts - self._result_ts) if self._result_ts is not None else None
                if not timer_raw or timer is None:
                    return closed
                # 已经进入 SETTLE 且距结算足够久时，满钟本身就是新回合信号；
                # 不再要求与上一读数相差固定阈值，兼容跨窗口缺少中间读数的情况。
                _fresh_clock = (
                    fresh_clock
                    or float(timer) >= _NEW_ROUND_CLOCK_MIN
                )
                _late_raw_combat = (
                    _is_combat_timer(timer)
                    and _since_result is not None
                    and _since_result >= _NEW_ROUND_AFTER_RESULT_SEC
                )
                if (_fresh_clock or _late_raw_combat) and (
                    _since_result is None or _since_result >= _PREP_AFTER_RESULT_SEC
                ):
                    close = self._close(end=cand_ts if cand_ts is not None else ts, end_by="next_combat")
                    if close is not None:
                        closed.append(close)
                        _log.info(
                            "SETTLE 错过准备直接见新交战钟: 旧回合降级闭合 next_combat（ts=%.1f）",
                            ts,
                        )
                    else:
                        _log.info(
                            "SETTLE 错过准备直接见新交战钟: 旧回合过短未产出，开新回合（ts=%.1f）",
                            ts,
                        )
                    self._open_combat(cand_ts if cand_ts is not None else ts)
                return closed

        return closed

    def force_close(self, end_ts: float) -> list[dict[str, Any]]:
        """收尾例外：扫描结束仍处于 COMBAT/SETTLE 时以 open_tail+pending 产出，
        避免最后一回合因缺少下一回合准备信号而永久丢失。"""
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
        # 出点契约：真·下回合准备第一帧（next_prep）→ vision_confirmed；
        # next_combat / open_tail 等降级出点 → pending（可入列待调，不自动导出）
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


def _is_buy_phase_onset(prev_raw: float | None, value: float | None) -> bool:
    """本帧是否为「新买枪阶段首帧」：读数 ≤45s **且**相对上一原始读数上跳 ≥20s。

    买枪倒计时（0:30→0:00）与「交战尾段最后 45 秒」读数都落在 ≤45s，只能靠这个物理
    上跳区分——买枪阶段由上一回合结束（读数趋 0）跳到 0:30，而交战尾段是同一条回合
    时钟的**连续下降**：

    - 实测 2026-09-12 赛事流 226s 抽帧 = 买枪/装备界面 + 顶中 `ROUND 5 0:03`
      （30s 买枪倒计时）⇒ 上跳（≈0 → 30）成立 ⇒ 判买枪相位 ✅；
    - 同一录像 395–403s 是真实交战尾段：46→45→44 连续下降（Δ=-1）⇒ 不成立 ⇒
      不再被判成买枪/准备 ✅。旧实现无条件把 ≤45s 判成 prep，于是每条真实回合都在
      真实出点前 ~45s 被 `next_prep` 收尾（351.312 的回合被切在 403.125，真实 449.875）。

    没有前序读数（录制起点/跨窗首帧）时不判买枪：宁可不闭合，也不开假回合。
    """
    if value is None or prev_raw is None:
        return False
    current = float(value)
    if not 0.0 < current <= BUY_TIMER_MAX_SEC:
        return False
    return current - float(prev_raw) >= _NEW_ROUND_TIMER_RESET_SEC


def _round_key(start: float) -> str:
    """10s 桶回合键（与消费端 _valorant_round_key 一致）：边界跨整数秒漂移时键稳定。"""
    return f"round-{int(round(float(start) / 10.0)):06d}"


def _refine_boundary_ts(
    video_path: str,
    ffmpeg_path: str,
    center_ts: float,
    target: str,
    *,
    min_start_ts: float | None = None,
    cancel_check: Callable[[], bool] | None = None,
    window_sec: float | None = None,
    source_profile: str | None = None,
    frame_provider: Any | None = None,
) -> float | None:
    """边界局部密扫：粗扫候选 ±3s @5fps，找连续 ≥2 帧目标标签游程的首帧真实 PTS。

    target="combat"：交战钟（>45s）首现帧；target="prep"：准备横幅首现帧（不接受
    ≤45s 计时器读数——回合最后 45 秒同样是 ≤45s，会让出点提前 ~45s）；
    target="end_or_prep"：结束横幅或准备横幅首现帧，用于纠正无结算信号时的早出点。
    min_start_ts：游程首帧不得早于该时刻（prep 密扫排除结算画面低倒计时）。
    密扫失败返回 None（保留粗扫值，宁用粗值不丢回合）。
    优化：combat 密扫跳过中央横幅 OCR，prep 密扫只读中央横幅并支持提前退出。
    """
    refine_window = _REFINE_WINDOW_SEC if window_sec is None else max(0.0, float(window_sec))
    t0 = max(0.0, float(center_ts) - refine_window)
    t1 = float(center_ts) + refine_window
    try:
        if frame_provider is not None:
            frames = frame_provider.get_frames(
                video_path,
                start_sec=t0,
                end_sec=t1,
                fps=_REFINE_FPS,
                ffmpeg_path=ffmpeg_path,
                cancel_check=cancel_check,
                overlap_sec=0.0,
            )
        else:
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

    if len(frames) > _REFINE_MAX_FRAMES_PER_BOUNDARY:
        # 帧数超上限：等距抽样保住时间覆盖
        _step = len(frames) / float(_REFINE_MAX_FRAMES_PER_BOUNDARY)
        frames = [frames[int(i * _step)] for i in range(_REFINE_MAX_FRAMES_PER_BOUNDARY)]

    run = 0
    run_start: float | None = None
    for ts, img in frames:
        if cancel_check and cancel_check():
            return None
        if target == "combat":
            try:
                timer, _, _ = _read_top_anchors_for_profile(img, source_profile)
            except Exception as exc:  # noqa: BLE001
                _log.debug("边界密扫 top OCR 失败: %s", exc)
                run = 0
                run_start = None
                continue
            hit = timer is not None and _is_combat_timer(timer)
        elif target == "end_or_prep":
            try:
                prep_banner, end_banner = _read_center_banner_for_profile(img, source_profile)
            except Exception as exc:  # noqa: BLE001
                _log.debug("边界密扫 end/prep OCR 失败: %s", exc)
                prep_banner = False
                end_banner = False
            hit = (prep_banner or end_banner) and (
                min_start_ts is None or ts >= float(min_start_ts)
            )
        else:
            # target == "prep"
            # 只认中央准备横幅。曾经把「≤45s 计时器读数」当作准备首帧，导致密扫
            # 在真实出点前 ~45s 处反复"确认"同一个假边界（351.312 回合被密扫到
            # 403.125 并盖上 end_confidence=0.95）。回合计时器最后 45 秒同样是
            # ≤45s，因此该读数不含任何出点信息。
            prep_banner = False
            try:
                prep_banner, _ = _read_center_banner_for_profile(img, source_profile)
            except Exception as exc:  # noqa: BLE001
                _log.debug("边界密扫 center OCR 失败: %s", exc)
            hit = prep_banner and (min_start_ts is None or ts >= float(min_start_ts))
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


def _annotate_replay(
    round_data: dict[str, Any],
    labels: list[tuple[float, str, float | None, bool, float | None]],
) -> None:
    """结算后 ≥5s 的 neutral 段标注为回放（赛事流特征），非游戏阶段透明。"""
    result_ts = round_data.get("result_ts")
    if result_ts is None:
        return
    end = float(round_data["end"])
    segs: list[list[float]] = []
    run_start: float | None = None
    last_neutral_ts: float | None = None
    # 当前 OCR 标签包含五项：(ts, label, timer, timer_raw, combat_cand_ts)。
    # 这里仅需要前两项，不能再按旧版四元组解包，否则 broadcast profile
    # 会在每个扫描窗口触发 "too many values to unpack" 并丢弃全部回合。
    for row in labels:
        if len(row) < 2:
            continue
        ts, label = row[0], row[1]
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


# A5 安全门（2026-09-11）：`replay_segments` 是启发式产物，实测会成片误标
# （见 apply_replay_end_exclusion 文档里的实证），故裁剪前设**确认证据门槛** +
# **幅度上限**两道门。分工（依据实测校正过）：
# - 确认证据门槛 = **正确性**门。上轮那次 14.5s 误裁（窗口内 44 帧零标记、全判 combat）
#   对应回合是 `confirm_status=pending` + `broadcast_audit=pending_no_exclusion`，
#   由它挡住；
# - 幅度上限 = **荒谬窗**兜底。原设 5s 经实测**过紧**：同一段 14 分钟真实直播里连拦
#   6s/11s/16s 三次，而那三次窗口内容是 non_game（7/7、11/12 帧）与 non_game+replay
#   （11/17 帧带 REPLAY 标记）——按内容本该裁。5s 会把"长回放/长非游戏尾料"一并挡掉，
#   属于用错判据。放宽到 30s 只用于兜住明显荒谬的声明窗。
_REPLAY_END_EXCLUSION_MAX_SEC = 30.0
_REPLAY_END_CONFIRMED_STATUS = frozenset({"vision_confirmed"})
_REPLAY_END_CONFIRMED_AUDIT = frozenset({"passed"})


def apply_replay_end_exclusion(round_data: dict[str, Any]) -> float | None:
    """消费 ``replay_segments``：终点不得伸进赛后回放块（任务 A5）。

    ``_annotate_replay`` 在检测阶段把「结算后 ≥5s 的 neutral 段」标为回放；但该回合的
    最终 ``end`` 之后仍会被视觉审计再裁一次（``end_by=broadcast_exclusion``），可能落在
    回放段**内部**、或把整段回放含进片尾。两个信号独立：审计靠模型像素，回放段靠 OCR
    的「计时器不可读」间接证据——后者恰好能抓到模型漏掉的实战镜头回放。
    因此取**更早**的一方：把 ``end`` 收到第一个回放段的起点。

    下限为 ``result_ts``（结算瞬间），保证切片至少含回合结果，不至于裁到回合内容里。
    返回被裁掉的秒数；未裁剪返回 ``None``。仅写审计字段，不改动入点。

    ⚠️ **2026-09-11 加的两道安全门（实测踩到才加的）**：``replay_segments`` 是
    **启发式**产物（「计时器不可读」≠ 一定是回放），实测有成片误标：

    - 记录 `12-00-36` 起的一段真实直播里，某回合被声明回放段
      ``[[674.094,679.094],[681.094,688.094]]``，本函数据此裁掉 **14.539s**；
      但该区间逐秒 44 帧 **零 REPLAY 标记**、模型全判 ``combat``（conf 0.68–0.96、
      ``p_replay ≤ 0.007``）→ **切掉的是真实交战画面**。
    - 同一录像另一回合声明回放段 ``[252.391,258.391]``，而真实回放在 ``[269,283]``
      → 时间戳偏约 17s。

    故裁剪前要求**已有确认证据**，并对幅度设上限；不满足时**不裁剪**、把候选幅度与
    跳过原因写进审计字段（供人工复核），而不是静默按错窗口动刀。
    """
    segs = round_data.get("replay_segments")
    if not isinstance(segs, list) or not segs:
        return None
    try:
        end = float(round_data.get("end") or 0.0)
        result_ts = float(round_data.get("result_ts") or 0.0)
        starts = [
            float(seg[0])
            for seg in segs
            if isinstance(seg, (list, tuple)) and len(seg) >= 2
        ]
        if not starts:
            return None
        first_start = min(starts)
    except (TypeError, ValueError, IndexError):
        return None
    limit = max(result_ts, 0.0)
    if first_start <= limit or end <= first_start:
        return None

    def _skip(reason: str, candidate: float) -> None:
        """不裁剪，但把"本来会裁多少、为什么没裁"落进审计字段（可复核）。"""
        round_data["replay_end_exclusion_skipped"] = reason
        round_data["replay_end_exclusion_candidate_sec"] = round(candidate, 3)
        round_data["replay_end_exclusion_candidate_from"] = round(first_start, 3)
        if reason == "trim_exceeds_cap":
            # 幅度超限说明声明窗口很可能有误 → 必须人工看一眼，不能静默放过
            round_data["boundary_review_required"] = True
        _log.warning(
            "回放终点排除(A5) 跳过: 原因=%s, 候选裁剪=%.3fs, end=%.3f, 首回放段起点=%.3f",
            reason, candidate, end, first_start,
        )

    candidate_trim = round(end - first_start, 3)
    if candidate_trim > _REPLAY_END_EXCLUSION_MAX_SEC:
        _skip("trim_exceeds_cap", candidate_trim)
        return None
    confirmed = (
        str(round_data.get("confirm_status") or "") in _REPLAY_END_CONFIRMED_STATUS
        or str(round_data.get("broadcast_audit") or "") in _REPLAY_END_CONFIRMED_AUDIT
    )
    if not confirmed:
        # 自己的终点都还没确认（pending）时，不再用第二个未确认信号去裁它
        _skip("boundary_not_confirmed", candidate_trim)
        return None

    # 视觉审计已给出"精确出点"（逐帧证据截断 / 赛事审计 passed）时，不得再用启发式
    # 窗口覆盖它：两条信号在描述同一处转场，而 replay_segments 只是"计时器不可读"的
    # 间接推断。实测（2026-09-11）：end_refined=196.25（该区间逐帧 p_combat 0.59–0.82，
    # 是真实交战结束）被本函数裁到 185.922，白丢 10.3s 画面。
    #
    # 2026-09-15 扩展：旧判据只认 end_by=='broadcast_exclusion'，但「审计 passed 的
    # next_prep 出点」同样是**已定稿**出点（end_refined 由 5-10fps 密扫给出、且经
    # _has_immediate_combat_after 复核），与 broadcast_exclusion 同级，不许被启发式
    # 回放段裁早——实测该启发式本身就有 ~17s 时间戳偏差与成片误标的历史（见上）。
    # 未定稿路径（无 end_refined / 审计未 passed）语义不变：那正是 A5 要兜的
    # "模型漏掉的实战镜头回放"。只记录"本该裁多少、为什么没裁"。
    _end_by_norm = str(round_data.get("end_by") or "")
    _audit_end_finalized = bool(
        round_data.get("end_refined") is not None
        and (
            _end_by_norm == "broadcast_exclusion"
            or str(round_data.get("broadcast_audit") or "") in _REPLAY_END_CONFIRMED_AUDIT
            or str(round_data.get("confirm_status") or "") in _REPLAY_END_CONFIRMED_STATUS
        )
    )
    if _audit_end_finalized:
        _skip("visual_end_authoritative", candidate_trim)
        round_data["boundary_review_required"] = True
        return None

    trimmed = candidate_trim
    round_data["end_before_replay_exclusion"] = round(end, 3)
    round_data["replay_end_excluded_sec"] = trimmed
    round_data["end"] = round(first_start, 3)
    _log.info(
        "回放终点排除(A5): end %.3f -> %.3f (裁掉 %.3fs, result_ts=%.3f, 首回放段起点=%.3f)",
        end, first_start, trimmed, result_ts, first_start,
    )
    return trimmed


def refine_valorant_round_boundaries(
    rounds: list[dict[str, Any]],
    video_path: str,
    ffmpeg_path: str = "ffmpeg",
    *,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[str, float, str], None] | None = None,
    source_profile: str | None = None,
    frame_provider: Any | None = None,
) -> list[dict[str, Any]]:
    """对已闭合回合做边界局部密扫（±3s@10fps），返回新列表（原地亦可）。

    持续分析增量路径：粗扫先入列，再异步调用本函数精修并 upsert。
    """
    if not rounds:
        return []
    from lsc.utils.helpers import resolve_real_video_path
    video_path = resolve_real_video_path(video_path)
    out = [dict(r) for r in rounds]
    total = len(out)
    t0 = time.monotonic()

    def _call_refine(
        center: float,
        target: str,
        *,
        min_start_ts: float | None = None,
        window_sec: float | None = None,
    ) -> float | None:
        kwargs: dict[str, Any] = {"cancel_check": cancel_check}
        if min_start_ts is not None:
            kwargs["min_start_ts"] = min_start_ts
        if window_sec is not None:
            kwargs["window_sec"] = window_sec
        if source_profile:
            kwargs["source_profile"] = source_profile
        if frame_provider is not None:
            kwargs["frame_provider"] = frame_provider
        try:
            return _refine_boundary_ts(
                video_path, ffmpeg_path, center, target, **kwargs,
            )
        except TypeError as exc:
            # 保持第三方/旧版插件注入的一参签名可用；真实 reader 支持 profile。
            if "source_profile" not in str(exc):
                raise
            kwargs.pop("source_profile", None)
            return _refine_boundary_ts(
                video_path, ffmpeg_path, center, target, **kwargs,
            )

    for idx, r in enumerate(out, 1):
        if cancel_check and cancel_check():
            break
        if progress_callback and total:
            progress_callback("refine", idx / max(total, 1), f"边界精修 {idx}/{total}")
        start_coarse = float(r.get("start_coarse", r.get("start", 0.0) or 0.0))
        end_coarse = float(r.get("end_coarse", r.get("end", 0.0) or 0.0))
        start_ts = _call_refine(float(r["start"]), "combat")
        if start_ts is not None:
            r["start_refined"] = round(start_ts, 3)
            r["start"] = r["start_refined"]
            r["start_delta"] = round(abs(r["start_refined"] - start_coarse), 3)
            # 10fps 密扫由连续多帧 OCR 计时器游程背书；没有分类器置信度时使用
            # 固定高置信度表示“该边界由物理密扫确认”，后续可被视觉审计覆盖。
            r["start_confidence"] = float(r.get("start_confidence", 0.95))
        if r.get("confirm_status") == "vision_confirmed" and r.get("end_by") == "next_prep":
            _min_prep_ts = None
            if r.get("result_ts") is not None:
                _min_prep_ts = float(r["result_ts"]) + _PREP_AFTER_RESULT_SEC
            if r.get("result_ts") is None:
                # 无比分/结束横幅时，粗扫的 next_prep 可能是交战中的低计时器
                # 误读。向后扩大窗口寻找真正的结束/准备横幅，避免只在错误点
                # 附近 ±3s 内重复确认同一个误读。
                end_ts = _call_refine(
                    float(r["end"]),
                    "end_or_prep",
                    min_start_ts=float(r["end"]),
                    window_sec=_REFINE_END_FALLBACK_WINDOW_SEC,
                )
            else:
                end_ts = _call_refine(
                    float(r["end"]),
                    "prep",
                    min_start_ts=_min_prep_ts,
                )
            if end_ts is not None and end_ts > float(r["start"]) + _MIN_ROUND_SEC:
                r["end_refined"] = round(end_ts, 3)
                r["end"] = r["end_refined"]
                r["end_delta"] = round(abs(r["end_refined"] - end_coarse), 3)
                r["end_confidence"] = float(r.get("end_confidence", 0.95))
        # 不得无条件标记 boundary_refined。广播赛事必须双向物理证据齐备才算
        # 精修完成；POV/历史路径保留旧语义（任一边际密扫成功即可视为 refined）。
        is_broadcast = str(source_profile or "").strip().lower() == "broadcast"
        if is_broadcast:
            r["boundary_refined"] = bool(
                r.get("start_delta") is not None
                and r.get("start_confidence") is not None
                and r.get("end_delta") is not None
                and r.get("end_confidence") is not None
            )
        elif start_ts is not None or r.get("end_delta") is not None:
            r["boundary_refined"] = True
    # 相邻回合修整：出点不得越过下一回合入点
    out.sort(key=lambda item: float(item["start"]))
    kept: list[dict[str, Any]] = []
    for r in out:
        if kept and float(kept[-1]["end"]) > float(r["start"]):
            kept[-1]["end"] = round(float(r["start"]), 3)
            if float(kept[-1]["end"]) - float(kept[-1]["start"]) < _MIN_ROUND_SEC:
                kept.pop()
        kept.append(r)
    _log.info("边界密扫耗时: %.1fs, %d 回合", time.monotonic() - t0, total)
    return kept


def _summarize_broadcast_mode_shadow(
    primary: list[dict[str, Any]],
    shadow: list[dict[str, Any]],
) -> dict[str, Any]:
    """比对「生效回合列表」与「broadcast_mode=True 影子列表」。

    只产出可持久化的差异摘要，供切换决策取数；**不参与任何生效判定**。
    起点差距在 ``_SHADOW_MATCH_TOLERANCE_SEC`` 内视为同一回合。
    """
    tolerance = _SHADOW_MATCH_TOLERANCE_SEC

    def _spans(rounds: list[dict[str, Any]]) -> list[tuple[float, float]]:
        spans: list[tuple[float, float]] = []
        for item in rounds:
            try:
                spans.append((float(item["start"]), float(item["end"])))
            except (KeyError, TypeError, ValueError):
                continue
        return spans

    primary_spans = _spans(primary)
    shadow_spans = _spans(shadow)
    matched_primary: set[int] = set()
    paired: list[tuple[tuple[float, float], tuple[float, float]]] = []
    shadow_only: list[tuple[float, float]] = []
    for shadow_start, shadow_end in shadow_spans:
        hit = None
        for index, (primary_start, _) in enumerate(primary_spans):
            if index not in matched_primary and abs(primary_start - shadow_start) <= tolerance:
                hit = index
                break
        if hit is None:
            shadow_only.append((shadow_start, shadow_end))
        else:
            matched_primary.add(hit)
            paired.append((primary_spans[hit], (shadow_start, shadow_end)))
    primary_only = [
        span for index, span in enumerate(primary_spans) if index not in matched_primary
    ]
    resized = [
        {
            "start": round(pair[0][0], 3),
            "primary_sec": round(pair[0][1] - pair[0][0], 3),
            "shadow_sec": round(pair[1][1] - pair[1][0], 3),
        }
        for pair in paired
        if abs((pair[0][1] - pair[0][0]) - (pair[1][1] - pair[1][0])) > 1.0
    ]
    return {
        "primary_rounds": len(primary_spans),
        "shadow_rounds": len(shadow_spans),
        "shadow_only": [[round(a, 3), round(b, 3)] for a, b in shadow_only],
        "primary_only": [[round(a, 3), round(b, 3)] for a, b in primary_only],
        "resized": resized[:20],
        "primary_next_combat": sum(
            1 for item in primary if str(item.get("end_by") or "") == "next_combat"
        ),
        "shadow_next_combat": sum(
            1 for item in shadow if str(item.get("end_by") or "") == "next_combat"
        ),
    }


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
    ocr_sample_interval: float = 1.0,
    refine_boundaries: bool = True,
    fast_mode: bool = False,
) -> list[dict[str, Any]]:
    """ocr_sample_interval 秒抽一帧 → fps；默认 1.0 = 1fps，保持既有全量分析行为。

    refine_boundaries：是否立即做边界密扫。持续分析增量传 False，先入列粗边界，
    再由 Worker 调用 refine_valorant_round_boundaries 异步精修。
    fast_mode：实时追赶时保留顶部逐帧 OCR，中央横幅降低为隔帧采样；收尾不得启用。
    """
    from lsc.utils.helpers import resolve_real_video_path
    video_path = resolve_real_video_path(video_path)
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
    # 上层只有在至少抽到一帧时才能登记 coverage；空输出可能是文件尾部
    # 尚未写稳或 FFmpeg 解码失败，不能被当作成功扫描。
    state["scan_succeeded"] = False
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

    # 采样间隔 → fps：ocr_sample_interval 秒抽一帧，下限 0.25fps（4s/帧）
    sample_fps = max(0.25, 1.0 / max(float(ocr_sample_interval), 0.1))

    fsm = state.get("ocr_fsm")
    fsm = fsm.clone() if isinstance(fsm, OcrRoundFSM) else OcrRoundFSM()
    # 信号持久化（相近相似原则的载体）
    last_timer = state.get("last_timer")
    last_timer_ts = float(state.get("last_timer_ts", -1.0))
    last_raw_timer = state.get("last_raw_timer")
    last_raw_ts = float(state.get("last_raw_ts", -1.0))
    # 买枪阶段窗口右沿（见 _is_buy_phase_onset）：只有在该窗口内，≤45s 读数才判 prep
    buy_phase_until: float | None = state.get("buy_phase_until")
    anchor = state.get("combat_anchor")  # (timer, ts)
    score_pending: tuple[int, int] | None = state.get("score_pending")
    prev_left = state.get("prev_left")
    prev_right = state.get("prev_right")
    timer_streak = int(state.get("timer_streak", 0) or 0)
    timer_streak_val: float | None = state.get("timer_streak_val")
    combat_raw_streak = int(state.get("combat_raw_streak", 0) or 0)
    combat_cand_ts: float | None = state.get("combat_cand_ts")
    # 结算后抑制：残余交战钟不得重建锚点/标成 combat/prep，直到
    # 出现空档（钟走完）后再见准备/交战，或中央准备横幅 / 满钟新回合。
    post_settle_hold = bool(state.get("post_settle_hold", False))
    post_settle_gap = bool(state.get("post_settle_gap", False))
    settle_result_ts: float | None = state.get("settle_result_ts")
    _center_sentinel_raw = state.get("center_sentinel_sec")
    center_sentinel_sec = (
        float(_center_sentinel_raw)
        if _center_sentinel_raw is not None
        else _CENTER_SENTINEL_SEC
    )
    roi_cache_enabled = bool(state.get("roi_cache_enabled", True))
    next_center_sample_ts = float(state.get("next_center_sample_ts", -1.0))
    prev_top_roi = state.get("prev_top_roi")
    prev_top_result = state.get("prev_top_result", (None, None, None))
    prev_center_roi = state.get("prev_center_roi")
    _top_cache_blackout = int(state.get("_top_cache_blackout", 0) or 0)
    next_broadcast_wide_ts = float(
        state.get("next_broadcast_wide_ts", -1.0)
    )

    labels: list[tuple[float, str, float | None, bool, float | None]] = []
    prep_banner_flags: list[bool] = []
    closed_rounds: list[dict[str, Any]] = []

    # 子窗口分块扫描：整窗抽帧会把追赶窗（最长 480s @1fps ≈ 330MB）帧全部驻留
    # 内存。按 _SUB_WINDOW_SEC 分块逐窗抽帧 + OCR，峰值降到 ~40MB；
    # 跨窗状态由本函数局部变量（anchor/streak 等）与 runtime_state 承载。
    # 相邻窗 overlap_sec=2s 的重叠帧由 last_processed_ts 过滤（与增量语义一致）。
    sub_start = scan_start
    total_frames = 0
    any_frames = False
    while sub_start < scan_end:
        sub_end = min(scan_end, sub_start + _SUB_WINDOW_SEC)
        extracted_frames = extract_frames_cancellable(
            video_path,
            start_sec=sub_start,
            end_sec=sub_end,
            fps=sample_fps,
            ffmpeg_path=ffmpeg_path,
            cancel_check=cancel_check,
            overlap_sec=2.0,
        )
        if extracted_frames:
            # 即使全部帧因跨窗口去重被过滤，FFmpeg 也已成功读到该窗口。
            any_frames = True
        frames = [item for item in extracted_frames if item[0] > last_processed_ts + 0.001]
        if not frames:
            sub_start = sub_end
            continue

        total_frames += len(frames)
        for frame_index, (ts, img) in enumerate(frames):
            if cancel_check and cancel_check():
                raise FFmpegCancelled("cancelled during ocr scan")

            try:
                import cv2

                _top_h = max(
                    1,
                    int(
                        img.shape[0]
                        * (
                            _BROADCAST_TOP_BAND_RATIOS[0]
                            if source_profile == "broadcast"
                            else _TOP_BAND_RATIO
                        )
                    ),
                )
                _top_roi = cv2.resize(
                    img[:_top_h, :],
                    (_ROI_CACHE_W, max(1, int(_ROI_CACHE_W * (_top_h / max(1, img.shape[1]))))),
                )
            except Exception:  # noqa: BLE001 - cv2 缺失/形状异常时退化为每帧 OCR
                _top_roi = None
            is_dummy_frame = img is None or not img.any()
            _top_changed = (
                is_dummy_frame
                or prev_top_roi is None
                or _top_roi is None
                or float(
                    np.abs(_top_roi.astype(np.int16) - prev_top_roi.astype(np.int16)).mean()
                ) > _ROI_DIFF_THRESHOLD
            )
            broadcast_wide_due = bool(
                source_profile == "broadcast"
                and (finalize or ts >= next_broadcast_wide_ts)
            )
            if (
                _top_changed
                or _top_cache_blackout > 0
                or not roi_cache_enabled
                or broadcast_wide_due
            ):
                raw_timer, left, right = _read_top_anchors_for_profile(
                    img,
                    source_profile,
                    use_wide_fallback=broadcast_wide_due,
                )
                if broadcast_wide_due and not finalize:
                    # 官方包装的宽 ROI 是容错路径，不应在紧 ROI
                    # 未同时读全计时器+双比分时每帧重复执行。
                    # 每 4s 探测一次仍可及时捕获偏移 HUD，中间
                    # 帧由紧 ROI + 计时器外推维持连续性。
                    next_broadcast_wide_ts = (
                        float(ts) + _BROADCAST_WIDE_ROI_SENTINEL_SEC
                    )
            else:
                raw_timer, left, right = prev_top_result
            _top_cache_blackout = max(0, _top_cache_blackout - 1)
            if (raw_timer, left, right) != prev_top_result:
                # 读数变化 = 画面在动（帧差阈值可能吞掉局部数字变化），
                # 黑名单续期，未来几帧强制真实 OCR，禁止复用旧读数。
                _top_cache_blackout = _ROI_CACHE_BLACKOUT_FRAMES
            prev_top_roi = _top_roi
            prev_top_result = (raw_timer, left, right)
            # 中央横幅是高成本 OCR（A-04）。顶部计时器保持逐帧读取（1fps 由
            # 抽帧保证），中央横幅改为「哨兵 + 事件触发」采样：
            #   每 center_sentinel_sec 一次哨兵 + 计时器跳变 + 比分变化 +
            #   post_settle_hold（结算关键期）+ FSM SETTLE（上帧状态，1 帧延迟可接受）。
            # 收尾（finalize）为出点精度保持逐帧采样。
            timer_jump = (
                raw_timer is not None
                and last_raw_timer is not None
                and (
                    float(raw_timer) - float(last_raw_timer) >= _NEW_ROUND_TIMER_RESET_SEC
                    or abs(float(raw_timer) - float(last_raw_timer)) > 5.0
                )
            )
            score_changed = (
                (left is not None and prev_left is not None and left != prev_left)
                or (right is not None and prev_right is not None and right != prev_right)
            )
            sample_center = (
                is_dummy_frame
                or finalize
                or center_sentinel_sec <= 0.0
                or ts >= next_center_sample_ts
                or timer_jump
                or score_changed
                or post_settle_hold
                or fsm._state == _State.SETTLE
            )
            if sample_center:
                # 哨兵帧画面未变（中央 ROI 复用缓存）：横幅是瞬态出现物，
                # 画面无变化即无横幅，直接复用 False，省一次昂贵中央 OCR。
                try:
                    import cv2

                    _cratio = (
                        _BROADCAST_CENTER_CROP_RATIOS[0]
                        if source_profile == "broadcast"
                        else _CENTER_CROP_RATIO
                    )
                    _ch, _cw = img.shape[:2]
                    _cx = int(_cw * _cratio[0])
                    _cy = int(_ch * _cratio[1])
                    _cbw = int(_cw * _cratio[2])
                    _cbh = int(_ch * _cratio[3])
                    _center_roi = cv2.resize(
                        img[_cy : _cy + _cbh, _cx : _cx + _cbw],
                        (_ROI_CACHE_W, max(1, int(_ROI_CACHE_W * (_cbh / max(1, _cbw))))),
                    )
                except Exception:  # noqa: BLE001
                    _center_roi = None
                _center_changed = (
                    is_dummy_frame
                    or prev_center_roi is None
                    or _center_roi is None
                    or float(
                        np.abs(_center_roi.astype(np.int16) - prev_center_roi.astype(np.int16)).mean()
                    ) > _ROI_DIFF_THRESHOLD
                )
                if _center_changed:
                    prep_banner, end_banner = _read_center_banner_for_profile(img, source_profile)
                else:
                    prep_banner, end_banner = False, False
                prev_center_roi = _center_roi
            else:
                prep_banner, end_banner = False, False
            if sample_center and ts >= next_center_sample_ts:
                next_center_sample_ts = ts + center_sentinel_sec

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
                # 买枪阶段判据需要"上一帧原始读数"：下面的分支会用本帧值覆盖
                # last_raw_timer，先在此锁存。
                prev_raw_reading = last_raw_timer
                if _is_combat_timer(raw_timer):
                    if combat_raw_streak == 0:
                        combat_cand_ts = ts
                    # anchor 建立/刷新需连续 2 帧原始交战钟读数（单帧误读不得开局）
                    combat_raw_streak += 1
                    if combat_raw_streak >= 2:
                        if post_settle_hold:
                            # 结算后残余倒计时不得重建锚点；仅满钟原始读数开新回合
                            if float(raw_timer) >= _NEW_ROUND_CLOCK_MIN:
                                post_settle_hold = False
                                anchor = (float(raw_timer), ts)
                        else:
                            anchor = (float(raw_timer), ts)
                else:
                    combat_raw_streak = 0
                    combat_cand_ts = None
                    if (
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
            else:
                if raw_timer is None:
                    combat_raw_streak = 0
                    combat_cand_ts = None
                prev_raw_reading = last_raw_timer
                if extrapolated is not None:
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

            # 买枪阶段窗口：≤45s 读数只有在「新买枪阶段首帧」（相对上一原始读数上跳
            # ≥20s）之后才判 prep；窗口在该阶段内保持（同一段买枪倒计时不可能每帧都
            # 再上跳一次）。见到交战钟 / 读数消失 / 超时即关闭。
            # 这样买枪倒计时（0:30→0:00）仍被正确识别为「下回合准备」，
            # 而交战尾段最后 45 秒（同一回合时钟连续下降，无上跳）不再被误判
            # ——旧实现无条件把 ≤45s 判 prep，导致每条真实回合在真实出点前 ~45s
            # 被 next_prep 收尾（2026-09-12 实测 351.312 → 403.125，真实 449.875）。
            if buy_phase_until is not None and (
                timer is None
                or float(timer) > BUY_TIMER_MAX_SEC
                or ts > buy_phase_until
            ):
                buy_phase_until = None
            if raw_timer is not None and not frozen and _is_buy_phase_onset(
                prev_raw_reading, raw_timer
            ):
                buy_phase_until = float(ts) + BUY_TIMER_MAX_SEC

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
                    elif buy_phase_until is not None and new_val <= BUY_TIMER_MAX_SEC:
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
            timer_raw = raw_timer is not None
            if end_banner or score_confirmed:
                anchor = None
                combat_raw_streak = 0
                combat_cand_ts = None
                post_settle_hold = True
                post_settle_gap = False
                settle_result_ts = ts
                label = "settle"
            elif post_settle_hold:
                # 结算后残余倒计时会从 50 连降到 ≤45，不得当成买枪准备（否则出点
                # 提前切到非交战段）。须：准备横幅 / 满钟 / 钟走完空档后再见 prep·combat。
                if prep_banner:
                    post_settle_hold = False
                    post_settle_gap = False
                    label = "prep"
                elif (
                    timer_raw
                    and raw_timer is not None
                    and float(raw_timer) >= _NEW_ROUND_CLOCK_MIN
                ):
                    post_settle_hold = False
                    post_settle_gap = False
                    label = "combat"
                elif (
                    raw_timer is None
                    or (raw_timer is not None and float(raw_timer) <= 1.0)
                ):
                    # 只用原始读数判空档：结算后 HUD 消失时外推钟仍会走秒，
                    # 不得把外推当成「残余倒计时还在」，否则永远等不到买枪准备。
                    post_settle_gap = True
                    label = "neutral"
                elif post_settle_gap and timer_phase == "prep":
                    # 空档之后出现的买枪相位（≤45s **且相对上一读数上跳**）＝新回合准备。
                    post_settle_hold = False
                    post_settle_gap = False
                    label = "prep"
                elif (
                    timer_phase == "prep"
                    and fsm._result_ts is not None
                    and ts - fsm._result_ts >= _PREP_AFTER_RESULT_SEC
                ):
                    # 距结算超过 _PREP_AFTER_RESULT_SEC（结算画面倒计时已结束），出现
                    # 真买枪相位（上跳判据同上）即解除 hold，避免买枪阶段被整段吞掉。
                    post_settle_hold = False
                    post_settle_gap = False
                    label = "prep"
                elif post_settle_gap and timer_raw and raw_timer is not None and _is_combat_timer(
                    raw_timer
                ):
                    post_settle_hold = False
                    post_settle_gap = False
                    label = "combat"
                else:
                    label = "neutral"
            elif anchor is not None:
                label = "combat"
            elif prep_banner:
                label = "prep"
            elif timer_phase is not None:
                # 计时器相位：combat（>45s）或 prep（买枪阶段首帧且上跳，见
                # _is_buy_phase_onset——交战尾段的 ≤45s 连续下降不再算 prep）。
                label = timer_phase
            else:
                label = "neutral"
            labels.append((ts, label, timer, timer_raw, combat_cand_ts))
            prep_banner_flags.append(bool(prep_banner))

            _log.debug(
                "ocr_label ts=%.1f label=%s timer=%s raw=%s anchor=%s hold=%s gap=%s",
                ts,
                label,
                f"{timer:.0f}" if timer else None,
                f"{raw_timer:.0f}" if raw_timer else None,
                f"{anchor[0]:.0f}@{anchor[1]:.1f}" if anchor else None,
                post_settle_hold,
                post_settle_gap,
            )

            last_processed_ts = max(last_processed_ts, float(frames[-1][0]))
        sub_start = sub_end

    if not any_frames:
        return []
    state["scan_succeeded"] = True

    # 循环先验平滑（帧级，仅删孤立 combat 噪点，不补缝——非游戏阶段透明）
    smoothed = _apply_phase_cycle_prior([label for _, label, _, _, _ in labels])
    # broadcast_mode 影子：并行喂一份 broadcast_mode=True 的 FSM，只记录差异。
    # 仅在 broadcast 档启用（该分支语义只对赛事流成立），且**绝不改变生效结果**：
    # 影子回合只进 shadow_rounds，不参与后续 round_key / 密扫 / 回放标注。
    shadow_fsm: OcrRoundFSM | None = None
    shadow_rounds: list[dict[str, Any]] = []
    if broadcast_mode_shadow_enabled() and str(source_profile or "").lower() == "broadcast":
        stored_shadow = state.get("ocr_fsm_broadcast_shadow")
        shadow_fsm = (
            stored_shadow.clone()
            if isinstance(stored_shadow, OcrRoundFSM)
            else OcrRoundFSM()
        )
    for (ts, _, timer, timer_raw, cand_ts), label, prep_banner in zip(
        labels, smoothed, prep_banner_flags, strict=True
    ):
        closed = fsm.feed(
            label,
            ts,
            timer,
            timer_raw=timer_raw,
            cand_ts=cand_ts,
            prep_banner=prep_banner,
        )
        if closed:
            closed_rounds.extend(closed)
        if shadow_fsm is not None:
            shadow_closed = shadow_fsm.feed(
                label,
                ts,
                timer,
                timer_raw=timer_raw,
                cand_ts=cand_ts,
                prep_banner=prep_banner,
                broadcast_mode=True,
            )
            if shadow_closed:
                shadow_rounds.extend(shadow_closed)

    # 收尾例外：扫描末端强制闭合未结束回合（open_tail + pending，防最后一回合丢失）
    if finalize:
        closed = fsm.force_close(end_ts=float(scan_end))
        if closed:
            closed_rounds.extend(closed)
        if shadow_fsm is not None:
            shadow_closed = shadow_fsm.force_close(end_ts=float(scan_end))
            if shadow_closed:
                shadow_rounds.extend(shadow_closed)

    if shadow_fsm is not None:
        diff = _summarize_broadcast_mode_shadow(closed_rounds, shadow_rounds)
        state["ocr_fsm_broadcast_shadow"] = shadow_fsm
        state["broadcast_mode_shadow"] = diff
        totals = state.setdefault(
            "broadcast_mode_shadow_totals",
            {
                "scans": 0,
                "primary_rounds": 0,
                "shadow_rounds": 0,
                "primary_next_combat": 0,
                "shadow_next_combat": 0,
                "shadow_only": 0,
                "primary_only": 0,
                "resized": 0,
            },
        )
        totals["scans"] += 1
        for key in (
            "primary_rounds",
            "shadow_rounds",
            "primary_next_combat",
            "shadow_next_combat",
        ):
            totals[key] += int(diff[key])
        totals["shadow_only"] += len(diff["shadow_only"])
        totals["primary_only"] += len(diff["primary_only"])
        totals["resized"] += len(diff["resized"])
        _log.info(
            "broadcast_mode 影子对比 (range=%.1f-%.1f): 生效=%d 影子=%d | "
            "next_combat 生效=%d 影子=%d | 影子独有=%d 生效独有=%d | 时长变化=%d",
            scan_start,
            scan_end,
            diff["primary_rounds"],
            diff["shadow_rounds"],
            diff["primary_next_combat"],
            diff["shadow_next_combat"],
            len(diff["shadow_only"]),
            len(diff["primary_only"]),
            len(diff["resized"]),
        )
        if diff["shadow_only"] or diff["primary_only"] or diff["resized"]:
            _log.info("broadcast_mode 影子差异明细: %s", diff)

    # 候选出生即写入不可变身份：后续 start gate / 10fps 密扫允许移动边界，
    # 但 round_key 必须保持第一次粗入点的 10s 桶，避免同一回合因起点漂移被
    # 当成两个候选、或拒绝墓碑无法命中旧 all_highlights 条目。
    for r in closed_rounds:
        try:
            r.setdefault("round_key", _round_key(float(r["start"])))
        except (TypeError, ValueError):
            r["round_key"] = ""

    if refine_boundaries and closed_rounds:
        closed_rounds = refine_valorant_round_boundaries(
            closed_rounds,
            video_path,
            ffmpeg_path,
            cancel_check=cancel_check,
            progress_callback=progress_callback,
            source_profile=source_profile,
        )
    else:
        for r in closed_rounds:
            r["boundary_refined"] = False
        # 粗扫也做相邻不重叠修整（密扫路径已在 helper 内完成）
        closed_rounds.sort(key=lambda r: float(r["start"]))
        kept_rounds: list[dict[str, Any]] = []
        for r in closed_rounds:
            if kept_rounds and float(kept_rounds[-1]["end"]) > float(r["start"]):
                kept_rounds[-1]["end"] = round(float(r["start"]), 3)
                if float(kept_rounds[-1]["end"]) - float(kept_rounds[-1]["start"]) < _MIN_ROUND_SEC:
                    kept_rounds.pop()
            kept_rounds.append(r)
        closed_rounds = kept_rounds

    # ── 非游戏阶段透明标注：结算后 ≥5s 的 neutral 段 = 回放（仅 broadcast 赛事流） ──
    if source_profile == "broadcast":
        for r in closed_rounds:
            _annotate_replay(r, labels)

    # 回写持久化状态
    state["ocr_fsm"] = fsm
    state["last_timer"] = last_timer
    state["last_timer_ts"] = last_timer_ts
    state["last_raw_timer"] = last_raw_timer
    state["buy_phase_until"] = buy_phase_until
    state["next_broadcast_wide_ts"] = next_broadcast_wide_ts
    state["last_raw_ts"] = last_raw_ts
    state["combat_anchor"] = anchor
    state["combat_raw_streak"] = combat_raw_streak
    state["combat_cand_ts"] = combat_cand_ts
    state["post_settle_hold"] = post_settle_hold
    state["post_settle_gap"] = post_settle_gap
    state["score_pending"] = score_pending
    state["prev_left"] = prev_left
    state["prev_right"] = prev_right
    state["timer_streak"] = timer_streak
    state["timer_streak_val"] = timer_streak_val
    state["last_processed_ts"] = last_processed_ts
    state["next_center_sample_ts"] = next_center_sample_ts
    state["settle_result_ts"] = settle_result_ts

    for r in closed_rounds:
        if source_profile:
            r["source_profile"] = source_profile

    _log.info("OCR 回合检测: %d 回合, %d 帧 (range=%.1f-%.1f, refine=%s)",
              len(closed_rounds), total_frames, scan_start, scan_end, refine_boundaries)
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

    from lsc.utils.helpers import resolve_real_video_path
    video_path = resolve_real_video_path(video_path)

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
                from lsc.utils.process_launcher import kill_process_tree

                kill_process_tree(proc)
            proc.wait(timeout=10)
            stderr_thread.join(timeout=5)

        if cancelled:
            raise FFmpegCancelled("ffmpeg cancelled")

        if proc.returncode != 0 and not frames:
            last_err = b"".join(stderr_chunks).decode("utf-8", errors="replace")[-500:]
            safe_err = redact_text(last_err.replace("\r", " ").replace("\n", " | "))
            if attempt_i + 1 < len(attempts):
                _log.warning(
                    "frame extract hwaccel 失败 (code=%s attempt=%d/%d raw=%s)，回退软解；stderr=%s",
                    proc.returncode,
                    attempt_i + 1,
                    len(attempts),
                    is_raw,
                    safe_err or "<empty>",
                )
                continue
            raise RuntimeError(
                f"frame extract failed rc={proc.returncode}: {safe_err}"
            )

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
    "BROADCAST_MODE_SHADOW_ENV",
    "BUY_TIMER_MAX_SEC",
    "OcrRoundFSM",
    "apply_replay_end_exclusion",
    "broadcast_mode_shadow_enabled",
    "detect_valorant_rounds_ocr",
    "extract_frames_cancellable",
    "_apply_phase_cycle_prior",
    "_summarize_broadcast_mode_shadow",
]
