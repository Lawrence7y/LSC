"""高光分析编排器。

对 Valorant 内容使用 round_detector 回合分割，
对通用内容使用场景检测 (FFmpeg scene filter)。

用法::

    analyzer = HighlightAnalyzer(
        progress_callback=lambda stage, pct, detail: print(f"{stage}: {pct:.0f}% {detail}"),
        cancel_check=lambda: should_cancel,
    )
    highlights = analyzer.analyze("recording.mp4", game="valorant")
    if highlights is None:
        print("分析被取消")
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any

_log = logging.getLogger(__name__)

class HighlightAnalyzer:
    """高光分析门面类。

    Valorant 内容使用 round_detector 回合分割，
    通用内容使用场景检测 (FFmpeg scene filter)。

    参数:
        progress_callback: ``callback(stage: str, progress: float, detail: str)``
        cancel_check: ``callback() -> bool``，返回 True 时取消分析

    属性:
        analysis_time_sec: 最近一次分析耗时（秒），在 :meth:`analyze` 返回后可用
    """

    def __init__(
        self,
        progress_callback: Callable[[str, float, str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        self._progress_callback = progress_callback
        self._cancel_check = cancel_check
        self.analysis_time_sec: float = 0.0

    def _is_cancelled(self) -> bool:
        """检查是否应取消分析，安全处理回调异常。"""
        if self._cancel_check is not None:
            try:
                return bool(self._cancel_check())
            except Exception as exc:
                _log.warning("cancel_check 回调异常: %s", exc)
                return False
        return False

    def _report_progress(
        self,
        stage: str,
        progress: float,
        detail: str = "",
    ) -> None:
        """安全地报告进度，处理回调异常。"""
        if self._progress_callback is not None:
            try:
                self._progress_callback(stage, progress, detail)
            except Exception as exc:
                _log.debug("progress_callback 回调异常: %s", exc)

    def analyze(
        self,
        video_path: str,
        mode: str = "combined",
        whisper_model: str = "auto",
        weights: dict[str, float] | None = None,
        scene_highlights: list[dict[str, Any]] | None = None,
        absolute_threshold: float = 0.15,
        game: str = "valorant",
    ) -> list[dict[str, Any]] | None:
        """主分析入口，串联各 pipeline 并返回高光段列表。

        参数:
            video_path: 源视频文件路径
            mode: 分析模式（保留参数，实际由 game 决定路径）
            whisper_model: Whisper 模型大小（保留参数，暂未使用）
            weights: 融合权重（保留参数，暂未使用）
            scene_highlights: 场景检测结果 ``[{"start": float, "end": float, "score": float}, ...]``,
                仅 ``mode="combined"`` 时使用

        返回:
            高光段列表，格式::

                [{"start": float, "end": float, "score": float, "reason": str,
                  "speech_score": float, "visual_score": float,
                  "transcript": str}, ...]

            如果分析被取消，返回 ``None``。
        """
        start_time = time.time()

        if not os.path.isfile(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        _log.info("开始分析: video=%s, game=%s",
                  os.path.basename(video_path), game)

        # ── Valorant Round-First 快速路径 ──
        # 当 game="valorant" 时，优先使用音频能量检测回合边界，
        # 直接输出所有回合的战斗阶段（无需 Whisper/CLIP/OCR）。
        if game == "valorant":
            try:
                from lsc.analyzer.round_detector import detect_valorant_rounds
                from lsc.config import load_config as _load_cfg_rd
                _cfg_rd = _load_cfg_rd()
                _ffmpeg_rd = _cfg_rd.ffmpeg_path or "ffmpeg"

                self._report_progress("round_detect", 0.0, "Valorant 回合检测中...")
                round_segments = detect_valorant_rounds(
                    video_path,
                    ffmpeg_path=_ffmpeg_rd,
                    progress_callback=self._progress_callback,
                    cancel_check=self._cancel_check,
                )
                if self._is_cancelled():
                    return None
                if round_segments:
                    self.analysis_time_sec = time.time() - start_time
                    _log.info(
                        "Valorant 回合检测完成: %d 回合, 耗时=%.1fs",
                        len(round_segments), self.analysis_time_sec,
                    )
                    return round_segments
                _log.info("Valorant 回合检测无结果，回退到标准分析流程")
            except Exception as exc:
                _log.warning("Valorant 回合检测失败，回退到标准流程: %s", exc)

        # ── Fallback: 非 Valant 内容或无回合检测结果 → 场景高光 ──
        if self._is_cancelled():
            return None

        if scene_highlights:
            self._report_progress("scene", 50.0, "使用场景检测结果...")
            results = list(scene_highlights)
            self.analysis_time_sec = time.time() - start_time
            _log.info("fallback 场景高光: %d 段, 耗时=%.1fs",
                      len(results), self.analysis_time_sec)
            return results

        self.analysis_time_sec = time.time() - start_time
        _log.info("无检测结果, 耗时=%.1fs", self.analysis_time_sec)
        return []


def _deduplicate_highlights(
    highlights: list[dict[str, Any]],
    iou_threshold: float = 0.6,
) -> list[dict[str, Any]]:
    """基于时间重叠度（IoU）的高光片段去重。"""
    if not highlights:
        return []

    sorted_hl = sorted(
        highlights,
        key=lambda h: (h.get("score", 0.0), h.get("end", 0) - h.get("start", 0)),
        reverse=True,
    )
    kept: list[dict[str, Any]] = []

    def _iou(a: dict[str, Any], b: dict[str, Any]) -> float:
        a_start, a_end = a.get("start", 0.0), a.get("end", 0.0)
        b_start, b_end = b.get("start", 0.0), b.get("end", 0.0)
        inter_start = max(a_start, b_start)
        inter_end = min(a_end, b_end)
        if inter_end <= inter_start:
            return 0.0
        intersection = inter_end - inter_start
        union = max(a_end, b_end) - min(a_start, b_start)
        return intersection / union if union > 0 else 0.0

    for hl in sorted_hl:
        is_duplicate = False
        for kept_hl in kept:
            if _iou(hl, kept_hl) >= iou_threshold:
                is_duplicate = True
                break
        if not is_duplicate:
            kept.append(hl)

    return sorted(kept, key=lambda h: h.get("start", 0.0))


def _group_events_by_round(
    highlights: list[dict[str, Any]],
    round_marker_type: str = "round_marker",
    default_pre_pad: float = 5.0,
    default_post_pad: float = 5.0,
    max_kill_gap_in_round: float = 45.0,
) -> list[dict[str, Any]]:
    """按回合分组击杀事件，每个回合产出 1 个高光片段。"""
    if not highlights:
        return []

    kill_events: list[dict[str, Any]] = []
    round_markers: list[dict[str, Any]] = []
    other_segments: list[dict[str, Any]] = []

    for hl in highlights:
        if hl.get("type") == round_marker_type:
            round_markers.append(hl)
        elif hl.get("source") == "ocr" or (
            hl.get("source") == "sound" and hl.get("type") == "gunfire"
        ):
            kill_events.append(hl)
        else:
            other_segments.append(hl)

    if not kill_events:
        return highlights

    kill_events.sort(key=lambda e: e.get("start", 0.0))
    round_markers.sort(key=lambda m: m.get("timestamp", 0.0))

    round_boundaries: list[float] = [0.0]
    for marker in round_markers:
        ts = marker.get("timestamp", 0.0)
        if not round_boundaries or ts - round_boundaries[-1] >= 5.0:
            round_boundaries.append(ts)

    round_groups: dict[int, list[dict[str, Any]]] = {}
    for evt in kill_events:
        evt_ts = evt.get("timestamp", evt.get("start", 0.0))
        assigned_round = 0
        for bi in range(len(round_boundaries) - 1, -1, -1):
            if evt_ts >= round_boundaries[bi] - 2.0:
                assigned_round = bi
                break
        round_groups.setdefault(assigned_round, []).append(evt)

    result: list[dict[str, Any]] = list(other_segments)
    for round_idx in sorted(round_groups.keys()):
        group = round_groups[round_idx]
        if not group:
            continue
        group.sort(key=lambda e: e.get("start", 0.0))
        round_start = group[0].get("start", 0.0)
        round_end = max(e.get("end", e.get("start", 0.0)) for e in group)
        merged = {
            "start": round(round_start, 3),
            "end": round(round_end, 3),
            "score": max(e.get("score", 0.5) for e in group),
            "reason": (
                f"回合 {round_idx + 1}: {len(group)} 击杀"
                if len(group) > 1
                else group[0].get("reason", "击杀")
            ),
            "speech_score": max(e.get("speech_score", 0.0) for e in group),
            "visual_score": max(e.get("visual_score", 0.0) for e in group),
            "transcript": " ".join(
                e.get("transcript", "") for e in group if e.get("transcript")
            ),
            "source": "ocr",
            "events_in_round": len(group),
        }
        result.append(merged)

    return result


def _merge_close_segments(
    segments: list[dict[str, Any]], max_gap: float = 15.0
) -> list[dict[str, Any]]:
    """合并时间重叠或相近的高光片段。"""
    if not segments:
        return []

    sorted_segs = sorted(segments, key=lambda x: x["start"])
    merged: list[dict[str, Any]] = []

    for seg in sorted_segs:
        if not merged:
            merged.append(dict(seg))
            continue
        last = merged[-1]
        if seg["start"] - last["end"] <= max_gap:
            last["end"] = max(last["end"], seg["end"])
            last["score"] = max(last["score"], seg["score"])
            last["speech_score"] = max(
                last.get("speech_score", 0.0), seg.get("speech_score", 0.0)
            )
            last["visual_score"] = max(
                last.get("visual_score", 0.0), seg.get("visual_score", 0.0)
            )
            reasons: list[str] = []
            for r in (last.get("reason", ""), seg.get("reason", "")):
                if r:
                    for part in r.split(" + "):
                        part_stripped = part.strip()
                        if part_stripped and part_stripped not in reasons:
                            reasons.append(part_stripped)
            last["reason"] = " + ".join(reasons) if reasons else "综合评分较高"
            t1 = last.get("transcript", "").strip()
            t2 = seg.get("transcript", "").strip()
            if t1 and t2:
                if t2 in t1:
                    last["transcript"] = t1
                elif t1 in t2:
                    last["transcript"] = t2
                else:
                    last["transcript"] = f"{t1} {t2}"
            else:
                last["transcript"] = t1 or t2
        else:
            merged.append(dict(seg))

    return merged


__all__ = ["HighlightAnalyzer"]
