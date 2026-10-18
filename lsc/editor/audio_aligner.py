"""音频互相关对齐模块。

对**前端预览音频**（Web Audio 采集，约 8s）做 numpy 互相关，计算内容级时间偏移量，
以进度最慢的直播为基准，为多房间导出对齐提供精确补偿。

说明（2026-09-18）：从录制文件用 FFmpeg 提取音频的对齐路径（`extract_audio_pcm` /
`align_rooms`）已删除 —— 它从未被业务代码调用，运行时对齐只走前端预览音频
（见 CLAUDE.md §5.3、docs/FEATURE-INVENTORY.md §16）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

_log = logging.getLogger(__name__)

# 采样率：16 kHz mono，人声频段足够，配合抛物线插值可达到亚毫秒精度
SAMPLE_RATE = 16000
# 互相关置信度阈值（低于此值视为内容不相关，降级为 0 偏移）
# 0.1 过低（环境噪声即可达到），0.3 可过滤不相关内容强行对齐
_CORRELATION_THRESHOLD = 0.3
_PAIRWISE_BRIDGE_THRESHOLD = 0.10
_PAIRWISE_CONSISTENCY_TOLERANCE = 0.25
_ENVELOPE_FRAME_MS = 40
_ENVELOPE_HOP_MS = 10
_ENVELOPE_BASELINE_MS = 500
_ENVELOPE_SMOOTH_MS = 80
_ENVELOPE_MIN_OFFSET = 0.15
_ENVELOPE_MIN_PEAK_RATIO = 3.0
# 不同语言解说会显著压低原始波形相关分，但公共游戏音效通常仍会让
# “原始波形候选”和“瞬态包络候选”落在同一个 lag。仅在两个独立特征
# 一致时放宽门限，避免单纯降低全局阈值把无关直播强行对齐。
_CROSS_LANGUAGE_WAVEFORM_MIN_SCORE = 0.015
_CROSS_LANGUAGE_ENVELOPE_MIN_SCORE = 0.35
_CROSS_LANGUAGE_MIN_PEAK_RATIO = 1.8
_CROSS_LANGUAGE_OFFSET_TOLERANCE = 0.06


@dataclass
class AlignResult:
    """音频互相关对齐结果。"""

    success: bool
    offsets: dict[str, float] = field(default_factory=dict)
    reference_room_id: str = ""
    method: str = ""
    correlation_scores: dict[str, float] = field(default_factory=dict)
    error: str = ""


def _parabolic_interpolation(correlation: np.ndarray, peak: int) -> float:
    """在相关性峰值两侧做抛物线拟合，返回亚样本精度的峰值位置。"""
    if peak <= 0 or peak >= len(correlation) - 1:
        return float(peak)
    y_prev = float(correlation[peak - 1])
    y_peak = float(correlation[peak])
    y_next = float(correlation[peak + 1])
    denom = y_prev - 2.0 * y_peak + y_next
    if abs(denom) < 1e-10:
        return float(peak)
    delta = 0.5 * (y_prev - y_next) / denom
    return float(peak) + delta


def _normalize_signal(audio: np.ndarray) -> np.ndarray:
    """去均值并按标准差归一化。"""
    signal = audio.astype(np.float32, copy=False) - np.mean(audio)
    std = float(np.std(signal))
    if std > 1e-10:
        signal = signal / std
    return signal  # type: ignore[no-any-return]


def _compute_waveform_offset(
    ref_audio: np.ndarray,
    other_audio: np.ndarray,
    sample_rate: int,
) -> tuple[float, float]:
    """原始波形互相关，适用于两路音频内容高度一致的情况。"""
    ref = _normalize_signal(ref_audio)
    other = _normalize_signal(other_audio)

    n = len(ref) + len(other) - 1
    n_fft = 1
    while n_fft < n:
        n_fft *= 2
    # 用 rfft(other[::-1]) 计算卷积，等价于 np.correlate(ref, other, mode="full")
    # conj(rfft(other)) 计算的是互相关 R[k]=sum ref[n]*other[n+k]，零延迟在 index 0
    # 而 np.correlate 的零延迟在 index len(other)-1，两者索引约定不同会导致 lag 计算错误
    correlation = np.fft.irfft(
        np.fft.rfft(ref, n_fft) * np.fft.rfft(other[::-1], n_fft),
        n_fft,
    )[:n]

    abs_corr = np.abs(correlation)
    peak = int(np.argmax(abs_corr))
    refined_peak = _parabolic_interpolation(abs_corr, peak)

    lag = refined_peak - (len(other) - 1)
    offset_sec = float(lag / sample_rate)

    overlap = max(1, len(other) - abs(int(round(lag))))
    score = min(1.0, float(abs_corr[peak] / overlap))
    return offset_sec, score


def _transient_envelope(audio: np.ndarray, sample_rate: int) -> tuple[np.ndarray, float]:
    """提取音量瞬态包络，弱化不同主播语音/音乐对原始波形的干扰。"""
    frame = max(1, int(sample_rate * _ENVELOPE_FRAME_MS / 1000))
    hop = max(1, int(sample_rate * _ENVELOPE_HOP_MS / 1000))
    if len(audio) < frame * 3:
        return np.array([], dtype=np.float32), 0.0

    frame_count = 1 + (len(audio) - frame) // hop
    envelope = np.empty(frame_count, dtype=np.float32)
    for idx in range(frame_count):
        start = idx * hop
        chunk = audio[start:start + frame].astype(np.float32, copy=False)
        envelope[idx] = float(np.sqrt(np.mean(chunk * chunk)))

    baseline_window = max(3, int(_ENVELOPE_BASELINE_MS / _ENVELOPE_HOP_MS))
    baseline_kernel = np.ones(baseline_window, dtype=np.float32) / baseline_window
    baseline = np.convolve(envelope, baseline_kernel, mode="same")
    transient = envelope - baseline
    transient = np.diff(transient, prepend=transient[0])

    smooth_window = max(1, int(_ENVELOPE_SMOOTH_MS / _ENVELOPE_HOP_MS))
    if smooth_window > 1:
        smooth_kernel = np.ones(smooth_window, dtype=np.float32) / smooth_window
        transient = np.convolve(transient, smooth_kernel, mode="same")

    if float(np.std(transient)) < 1e-10:
        return np.array([], dtype=np.float32), 0.0
    return transient.astype(np.float32, copy=False), sample_rate / hop


def _best_normalized_lag(
    ref: np.ndarray,
    other: np.ndarray,
    envelope_rate: float,
    max_lag_seconds: float,
) -> tuple[float, float, float]:
    """在低采样率包络上做逐 lag 归一化互相关，并返回峰值突出度。"""
    ref = ref.astype(np.float32, copy=False) - np.mean(ref)
    other = other.astype(np.float32, copy=False) - np.mean(other)
    max_lag = min(len(ref) - 1, len(other) - 1, int(max_lag_seconds * envelope_rate))
    if max_lag <= 0:
        return 0.0, 0.0, 0.0

    min_overlap = max(5, int(0.5 * envelope_rate))
    best_lag = 0
    best_score = 0.0
    scores: list[float] = []
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            ref_slice = ref[lag:min(len(ref), len(other) + lag)]
            other_slice = other[:len(ref_slice)]
        else:
            other_slice = other[-lag:min(len(other), len(ref) - lag)]
            ref_slice = ref[:len(other_slice)]
        if len(ref_slice) < min_overlap:
            continue
        denom = float(np.sqrt(np.sum(ref_slice * ref_slice) * np.sum(other_slice * other_slice)))
        score = 0.0 if denom < 1e-12 else abs(float(np.sum(ref_slice * other_slice)) / denom)
        scores.append(score)
        if score > best_score:
            best_score = score
            best_lag = lag

    if not scores:
        return 0.0, 0.0, 0.0
    background = float(np.percentile(scores, 95))
    peak_ratio = best_score / (background + 1e-6)
    return float(best_lag / envelope_rate), min(1.0, best_score), peak_ratio


def _compute_transient_envelope_offset(
    ref_audio: np.ndarray,
    other_audio: np.ndarray,
    sample_rate: int,
) -> tuple[float, float, float]:
    """低置信波形对齐的 fallback：用公共音量瞬态重新估计偏移。"""
    ref_env, envelope_rate = _transient_envelope(ref_audio, sample_rate)
    other_env, _ = _transient_envelope(other_audio, sample_rate)
    if ref_env.size == 0 or other_env.size == 0 or envelope_rate <= 0:
        return 0.0, 0.0, 0.0

    duration = min(len(ref_audio), len(other_audio)) / sample_rate
    max_lag_seconds = max(0.5, min(5.0, duration - 0.5))
    return _best_normalized_lag(ref_env, other_env, envelope_rate, max_lag_seconds)


def compute_offset(
    ref_audio: np.ndarray,
    other_audio: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
) -> tuple[float, float]:
    """通过互相关计算时间偏移量。

    参数
    ----
    ref_audio: 参考音频（float32 数组）
    other_audio: 待比较音频（float32 数组）
    sample_rate: 采样率

    返回
    ----
    (offset_seconds, correlation_score)
    - offset_seconds: 正值表示 ``other`` 比 ``ref`` 快（内容超前），
      负值表示 ``other`` 比 ``ref`` 慢（内容滞后）。
    - correlation_score: 归一化互相关峰值，0~1 之间，
      低于 ``_CORRELATION_THRESHOLD`` 时结果不可靠。
    """
    if len(ref_audio) == 0 or len(other_audio) == 0:
        _log.warning("互相关输入为空: ref=%d samples, other=%d samples", len(ref_audio), len(other_audio))
        return 0.0, 0.0

    _log.debug(
        "计算互相关偏移: ref=%d samples (%.2fs), other=%d samples (%.2fs), rate=%d",
        len(ref_audio), len(ref_audio) / sample_rate,
        len(other_audio), len(other_audio) / sample_rate,
        sample_rate,
    )

    offset_sec, score = _compute_waveform_offset(ref_audio, other_audio, sample_rate)
    if score >= _CORRELATION_THRESHOLD:
        _log.debug("互相关结果: offset=%.4fs (%.1fms), score=%.3f", offset_sec, offset_sec * 1000, score)
        return offset_sec, score

    env_offset, env_score, peak_ratio = _compute_transient_envelope_offset(ref_audio, other_audio, sample_rate)
    if (
        env_score >= _CORRELATION_THRESHOLD
        and abs(env_offset) >= _ENVELOPE_MIN_OFFSET
        and peak_ratio >= _ENVELOPE_MIN_PEAK_RATIO
    ):
        _log.info(
            "波形互相关低置信 %.3f，使用瞬态包络 fallback: offset=%.4fs, score=%.3f, peak_ratio=%.2f",
            score,
            env_offset,
            env_score,
            peak_ratio,
        )
        return env_offset, env_score

    # 跨语言解说一致性 fallback：
    # 独立人声/配乐会降低 waveform score，也会让包络峰值不够突出；
    # 但只要公共游戏音效尚可辨识，两条特征路径仍会给出相同 lag。
    # 完全无关音频的两个候选通常分散在不同位置，因此不直接放宽任一
    # 单特征阈值，而要求 offset、方向和最小证据同时成立。
    cross_language_consensus = (
        score >= _CROSS_LANGUAGE_WAVEFORM_MIN_SCORE
        and env_score >= _CROSS_LANGUAGE_ENVELOPE_MIN_SCORE
        and abs(env_offset) >= _ENVELOPE_MIN_OFFSET
        and abs(offset_sec - env_offset) <= _CROSS_LANGUAGE_OFFSET_TOLERANCE
        and peak_ratio >= _CROSS_LANGUAGE_MIN_PEAK_RATIO
    )
    if cross_language_consensus:
        consensus_score = min(
            1.0,
            max(
                _CORRELATION_THRESHOLD,
                env_score * min(1.0, peak_ratio / _ENVELOPE_MIN_PEAK_RATIO),
            ),
        )
        _log.info(
            "跨语言解说一致性对齐: offset=%.4fs, waveform_score=%.3f, "
            "envelope_score=%.3f, peak_ratio=%.2f, confidence=%.3f",
            env_offset,
            score,
            env_score,
            peak_ratio,
            consensus_score,
        )
        return env_offset, consensus_score

    _log.debug("互相关结果: offset=%.4fs (%.1fms), score=%.3f", offset_sec, offset_sec * 1000, score)
    return offset_sec, score


@dataclass(frozen=True)
class _PairwiseEdge:
    left: str
    right: str
    offset: float
    score: float


def _connected_components(room_ids: list[str], edges: list[_PairwiseEdge]) -> list[list[str]]:
    """按高置信边找连通分量。"""
    adjacency: dict[str, set[str]] = {rid: set() for rid in room_ids}
    for edge in edges:
        adjacency[edge.left].add(edge.right)
        adjacency[edge.right].add(edge.left)

    components: list[list[str]] = []
    seen: set[str] = set()
    for rid in room_ids:
        if rid in seen:
            continue
        stack = [rid]
        component: list[str] = []
        seen.add(rid)
        while stack:
            cur = stack.pop()
            component.append(cur)
            for nxt in adjacency[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        components.append(component)
    return components


def _solve_component_offsets(component: list[str], edges: list[_PairwiseEdge]) -> dict[str, float]:
    """用加权最小二乘估计单个连通分量内的全局相对快慢。"""
    if len(component) == 1:
        return {component[0]: 0.0}

    root = component[0]
    variables = [rid for rid in component if rid != root]
    var_index = {rid: idx for idx, rid in enumerate(variables)}
    rows: list[list[float]] = []
    values: list[float] = []

    component_set = set(component)
    for edge in edges:
        if edge.left not in component_set or edge.right not in component_set:
            continue
        row = [0.0] * len(variables)
        if edge.right != root:
            row[var_index[edge.right]] += 1.0
        if edge.left != root:
            row[var_index[edge.left]] -= 1.0
        weight = float(np.sqrt(max(edge.score, 1e-6)))
        rows.append([v * weight for v in row])
        values.append(edge.offset * weight)

    if not rows:
        return {rid: 0.0 for rid in component}

    matrix = np.asarray(rows, dtype=np.float64)
    target = np.asarray(values, dtype=np.float64)
    solution, *_ = np.linalg.lstsq(matrix, target, rcond=None)
    raw = {root: 0.0}
    for rid, idx in var_index.items():
        raw[rid] = float(solution[idx])
    return raw


def _edge_residual(edge: _PairwiseEdge, raw_offsets: dict[str, float]) -> float:
    if edge.left not in raw_offsets or edge.right not in raw_offsets:
        return float("inf")
    return abs((raw_offsets[edge.right] - raw_offsets[edge.left]) - edge.offset)


def _select_alignment_edges(
    room_ids: list[str],
    pair_edges: list[_PairwiseEdge],
) -> tuple[list[_PairwiseEdge], list[_PairwiseEdge]]:
    reliable_edges = [edge for edge in pair_edges if edge.score >= _CORRELATION_THRESHOLD]
    candidates = [edge for edge in pair_edges if edge.score >= _PAIRWISE_BRIDGE_THRESHOLD]
    if len(candidates) == len(reliable_edges):
        return reliable_edges, []

    consistent_low_edges: list[_PairwiseEdge] = []
    for component in _connected_components(room_ids, candidates):
        if len(component) < 3:
            continue
        component_set = set(component)
        component_edges = [
            edge for edge in candidates
            if edge.left in component_set and edge.right in component_set
        ]
        if len(component_edges) < 3:
            continue
        raw_offsets = _solve_component_offsets(component, component_edges)
        for edge in component_edges:
            if edge.score >= _CORRELATION_THRESHOLD:
                continue
            if _edge_residual(edge, raw_offsets) <= _PAIRWISE_CONSISTENCY_TOLERANCE:
                consistent_low_edges.append(edge)

    high_support: dict[str, int] = {rid: 0 for rid in room_ids}
    low_support: dict[str, int] = {rid: 0 for rid in room_ids}
    for edge in reliable_edges:
        high_support[edge.left] += 1
        high_support[edge.right] += 1
    for edge in consistent_low_edges:
        low_support[edge.left] += 1
        low_support[edge.right] += 1

    bridge_edges = [
        edge for edge in consistent_low_edges
        if (
            (high_support[edge.left] > 0 or low_support[edge.left] >= 2)
            and (high_support[edge.right] > 0 or low_support[edge.right] >= 2)
        )
    ]
    return reliable_edges + bridge_edges, bridge_edges


def _room_alignment_scores(
    room_ids: list[str],
    reliable_edges: list[_PairwiseEdge],
    bridge_edges: list[_PairwiseEdge],
) -> dict[str, float]:
    incident_scores: dict[str, list[float]] = {rid: [] for rid in room_ids}
    for edge in reliable_edges:
        incident_scores[edge.left].append(float(edge.score))
        incident_scores[edge.right].append(float(edge.score))
    for edge in bridge_edges:
        bridge_score = max(float(edge.score), _CORRELATION_THRESHOLD)
        incident_scores[edge.left].append(bridge_score)
        incident_scores[edge.right].append(bridge_score)
    return {
        rid: (float(sum(values) / len(values)) if values else 0.0)
        for rid, values in incident_scores.items()
    }


def align_audio_map(
    audio_data: dict[str, np.ndarray],
    sample_rate: int = SAMPLE_RATE,
    method: str = "audio",
) -> AlignResult:
    """对已提取的多路音频做全局对齐。

    2 路音频沿用直接互相关；3 路及以上会计算所有两两组合，并用高置信边
    构建加权图估计每个房间的全局偏移。这样不会被“第一个房间音频较脏”
    的单参考模式拖垮。
    """
    room_ids = list(audio_data.keys())
    if len(room_ids) < 2:
        _log.warning("有效音频不足 2 路: %s", room_ids)
        return AlignResult(success=False, error="有效音频不足 2 路，无法互相关对齐", method=method)

    pair_edges: list[_PairwiseEdge] = []

    _log.info("开始两两音频互相关: rooms=%s, pairs=%d", room_ids, len(room_ids) * (len(room_ids) - 1) // 2)
    for left_idx, left in enumerate(room_ids):
        for right in room_ids[left_idx + 1:]:
            offset, score = compute_offset(audio_data[left], audio_data[right], sample_rate)
            edge = _PairwiseEdge(left=left, right=right, offset=float(offset), score=float(score))
            pair_edges.append(edge)
            _log.info(
                "两两互相关: %s vs %s → offset=%.4fs (%.1fms), score=%.3f",
                left,
                right,
                offset,
                offset * 1000,
                score,
            )
    # 2 路时即使置信度低，也返回直接结果，由调用方根据 score 决定是否降级。
    if len(room_ids) == 2:
        edge = pair_edges[0]
        pair_offsets = {edge.left: 0.0, edge.right: edge.offset}
        slowest_offset = min(pair_offsets.values())
        offsets = {rid: max(0.0, pair_offsets[rid] - slowest_offset) for rid in room_ids}
        reference_room_id = min(pair_offsets, key=lambda k: pair_offsets[k])
        scores = {rid: edge.score for rid in room_ids}
        return AlignResult(
            success=True,
            offsets=offsets,
            reference_room_id=reference_room_id,
            method=method,
            correlation_scores=scores,
        )

    alignment_edges, bridge_edges = _select_alignment_edges(room_ids, pair_edges)
    strict_reliable_edges = [edge for edge in pair_edges if edge.score >= _CORRELATION_THRESHOLD]
    if bridge_edges:
        _log.info(
            "低分两两边通过一致性校验并纳入全局对齐: %s",
            [
                f"{edge.left}->{edge.right} offset={edge.offset:.4f}s score={edge.score:.3f}"
                for edge in bridge_edges
            ],
        )
    reliable_edges = alignment_edges

    if not reliable_edges:
        _log.warning("两两互相关没有高置信边，全部房间降级为 0 偏移")
        return AlignResult(
            success=True,
            offsets={rid: 0.0 for rid in room_ids},
            reference_room_id=room_ids[0],
            method=method,
            correlation_scores={rid: 0.0 for rid in room_ids},
        )

    raw_offsets: dict[str, float] = {rid: 0.0 for rid in room_ids}
    raw_by_room: dict[str, float] = {}
    components = _connected_components(room_ids, reliable_edges)
    for component in components:
        component_edges = [
            edge for edge in reliable_edges
            if edge.left in component and edge.right in component
        ]
        if len(component) < 2 or not component_edges:
            raw_by_room[component[0]] = 0.0
            raw_offsets[component[0]] = 0.0
            continue
        raw_component = _solve_component_offsets(component, component_edges)
        slowest = min(raw_component.values())
        for rid, raw in raw_component.items():
            raw_by_room[rid] = raw
            raw_offsets[rid] = max(0.0, raw - slowest)

    scores = _room_alignment_scores(room_ids, strict_reliable_edges, bridge_edges)
    reference_candidates = [rid for rid in room_ids if scores[rid] > 0]
    if reference_candidates:
        reference_room_id = min(
            reference_candidates,
            key=lambda rid: (raw_offsets.get(rid, 0.0), -scores[rid]),
        )
    else:
        reference_room_id = room_ids[0]

    _log.info(
        "全局两两对齐完成: reference=%s, offsets=%s, scores=%s",
        reference_room_id,
        {k: f"{v:.4f}" for k, v in raw_offsets.items()},
        {k: f"{v:.3f}" for k, v in scores.items()},
    )
    return AlignResult(
        success=True,
        offsets=raw_offsets,
        reference_room_id=reference_room_id,
        method=method,
        correlation_scores=scores,
    )
