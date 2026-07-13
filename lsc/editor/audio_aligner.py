"""音频互相关对齐模块。

通过 FFmpeg 提取各房间的音频 PCM 数据，使用 numpy 互相关计算
内容级时间偏移量，以最慢的直播为基准，为多房间导出对齐提供精确补偿。
"""

from __future__ import annotations

import logging
import subprocess
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from lsc.platforms.base import headers_to_ffmpeg_input_args
from lsc.utils.process_launcher import prepare_launch, set_stream_nonblocking

_log = logging.getLogger(__name__)

# 提取音频的时长（秒）—— 3 秒在 CDN 延迟差（通常 1-3s）下仍有足够重叠
AUDIO_DURATION = 3.0
# 采样率：16 kHz mono，人声频段足够，配合抛物线插值可达到亚毫秒精度
SAMPLE_RATE = 16000
# 录制文件读取时距离直播边缘的安全缓冲（秒）
_SEEK_BUFFER = 2.0
# 互相关置信度阈值（低于此值视为内容不相关，降级为 0 偏移）
# 0.1 过低（环境噪声即可达到），0.3 可过滤不相关内容强行对齐
_CORRELATION_THRESHOLD = 0.3
_PAIRWISE_BRIDGE_THRESHOLD = 0.10
_PAIRWISE_CONSISTENCY_TOLERANCE = 0.25
# 最大并发音频提取数
_MAX_EXTRACT_WORKERS = 6
_ENVELOPE_FRAME_MS = 40
_ENVELOPE_HOP_MS = 10
_ENVELOPE_BASELINE_MS = 500
_ENVELOPE_SMOOTH_MS = 80
_ENVELOPE_MIN_OFFSET = 0.15
_ENVELOPE_MIN_PEAK_RATIO = 3.0


@dataclass
class AlignResult:
    """音频互相关对齐结果。"""

    success: bool
    offsets: dict[str, float] = field(default_factory=dict)
    reference_room_id: str = ""
    method: str = ""
    correlation_scores: dict[str, float] = field(default_factory=dict)
    error: str = ""


def _is_network_source(source: str) -> bool:
    """判断 source 是否为网络流 URL（而非本地文件路径）。"""
    return source.startswith(("http://", "https://", "rtmp://", "rtmps://", "rtsp://", "rtsp://"))


def extract_audio_pcm(
    ffmpeg_path: str,
    source: str,
    duration: float = AUDIO_DURATION,
    sample_rate: int = SAMPLE_RATE,
    seek: float = 0.0,
    headers: dict[str, str] | None = None,
) -> np.ndarray:
    """使用 FFmpeg 提取音频为 mono PCM numpy float32 数组。

    正常返回形状为 ``(duration * sample_rate,)`` 的数组，
    提取失败或音频为空时返回空数组 ``array([])``。

    对于网络直播流 URL，自动添加重连、超时等网络参数，
    并禁用视频解码（``-vn``）以减少资源消耗。
    """
    cmd = [ffmpeg_path, "-y", "-loglevel", "warning"]

    is_network = _is_network_source(source)
    if is_network:
        cmd += [
            "-re",
            "-thread_queue_size", "1024",
            "-timeout", "10000000",
            "-rw_timeout", "15000000",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
        ]

    if headers:
        cmd += headers_to_ffmpeg_input_args(headers)
    if seek > 0:
        cmd += ["-ss", f"{seek:.3f}"]
    cmd += [
        "-i", source,
        "-vn",
        "-map", "0:a?",
        "-t", f"{duration:.1f}",
        "-ar", str(sample_rate),
        "-ac", "1",
        "-f", "s16le",
        "pipe:1",
    ]
    _log.info(
        "开始提取音频: source=%s, seek=%.1fs, duration=%.1fs, rate=%dHz, network=%s",
        source[:200], seek, duration, sample_rate, is_network,
    )
    try:
        env, creation_flags, cwd = prepare_launch(ffmpeg_path)
        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "env": env,
        }
        if creation_flags:
            popen_kwargs["creationflags"] = creation_flags
        if cwd:
            popen_kwargs["cwd"] = cwd
        proc = subprocess.Popen(cmd, **popen_kwargs)  # noqa: S603
        set_stream_nonblocking(proc.stdout)
        try:
            raw, _ = proc.communicate(timeout=duration + 20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            _log.warning("FFmpeg 音频提取超时: source=%s", source[:200])
            return np.array([], dtype=np.float32)
    except Exception as exc:
        _log.warning("FFmpeg 音频提取失败: source=%s, error=%s", source[:200], exc, exc_info=True)
        return np.array([], dtype=np.float32)

    if not raw:
        _log.warning("音频提取为空: source=%s", source[:200])
        return np.array([], dtype=np.float32)
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    _log.info("音频提取成功: source=%s, samples=%d (%.2fs)", source[:200], len(audio), len(audio) / sample_rate)
    return audio


def _parabolic_interpolation(correlation: np.ndarray, peak: int) -> float:
    """在相关性峰值两侧做抛物线拟合，返回亚样本精度的峰值位置。"""
    if peak <= 0 or peak >= len(correlation) - 1:
        return float(peak)
    y_prev = correlation[peak - 1]
    y_peak = correlation[peak]
    y_next = correlation[peak + 1]
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
    return signal


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
        raw_offsets = {edge.left: 0.0, edge.right: edge.offset}
        slowest_offset = min(raw_offsets.values())
        offsets = {rid: max(0.0, raw_offsets[rid] - slowest_offset) for rid in room_ids}
        reference_room_id = min(raw_offsets, key=raw_offsets.get)
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

    offsets: dict[str, float] = {rid: 0.0 for rid in room_ids}
    raw_by_room: dict[str, float] = {}
    components = _connected_components(room_ids, reliable_edges)
    for component in components:
        component_edges = [
            edge for edge in reliable_edges
            if edge.left in component and edge.right in component
        ]
        if len(component) < 2 or not component_edges:
            raw_by_room[component[0]] = 0.0
            offsets[component[0]] = 0.0
            continue
        raw_component = _solve_component_offsets(component, component_edges)
        slowest = min(raw_component.values())
        for rid, raw in raw_component.items():
            raw_by_room[rid] = raw
            offsets[rid] = max(0.0, raw - slowest)

    scores = _room_alignment_scores(room_ids, strict_reliable_edges, bridge_edges)
    reference_candidates = [rid for rid in room_ids if scores[rid] > 0]
    if reference_candidates:
        reference_room_id = min(
            reference_candidates,
            key=lambda rid: (offsets.get(rid, 0.0), -scores[rid]),
        )
    else:
        reference_room_id = room_ids[0]

    _log.info(
        "全局两两对齐完成: reference=%s, offsets=%s, scores=%s",
        reference_room_id,
        {k: f"{v:.4f}" for k, v in offsets.items()},
        {k: f"{v:.3f}" for k, v in scores.items()},
    )
    return AlignResult(
        success=True,
        offsets=offsets,
        reference_room_id=reference_room_id,
        method=method,
        correlation_scores=scores,
    )


def align_rooms(
    rooms_data: list[dict[str, Any]],
    ffmpeg_path: str,
) -> AlignResult:
    """对多个房间的音频进行互相关对齐。

    参数
    ----
    rooms_data: 每项包含::

        {
            "room_id": str,
            "source": str,           # 音频源路径/URL
            "seek": float,           # 提取起始偏移（秒）
            "is_recording": bool,    # 是否从录制文件提取
            "headers": dict | None,  # 直播流请求头
            "streamer_name": str,
        }

    ffmpeg_path: FFmpeg 可执行文件路径

    返回
    ----
    AlignResult，其中 offsets 为各房间相对最慢房间的偏移量：
    - 最慢房间 offset = 0
    - 其他房间 offset = 该房间内容比最慢房间快多少秒（正数）
    """
    if len(rooms_data) < 2:
        _log.warning("对齐房间不足: %d 个房间", len(rooms_data))
        return AlignResult(success=False, error="至少需要 2 个有效房间才能对齐")

    room_ids = [rd["room_id"] for rd in rooms_data]
    _log.info("开始音频对齐: rooms=%s, method=%s", room_ids, "recording" if all(rd.get("is_recording") for rd in rooms_data) else "stream")

    with ThreadPoolExecutor(max_workers=_MAX_EXTRACT_WORKERS) as pool:
        futures: dict[str, Future[np.ndarray]] = {}
        audio_data: dict[str, np.ndarray] = {}
        for rd in rooms_data:
            source_preview = rd["source"][:80] + "..." if len(rd["source"]) > 80 else rd["source"]
            _log.info("提交音频提取: room=%s, source=%s, seek=%.1fs, recording=%s", rd["room_id"], source_preview, rd.get("seek", 0.0), rd.get("is_recording", False))
            future = pool.submit(
                extract_audio_pcm,
                ffmpeg_path,
                rd["source"],
                AUDIO_DURATION,
                SAMPLE_RATE,
                seek=rd.get("seek", 0.0),
                headers=rd.get("headers"),
            )
            futures[rd["room_id"]] = future

        for rid, fut in futures.items():
            try:
                result = fut.result(timeout=AUDIO_DURATION + 25)
                if result.size > 0:
                    audio_data[rid] = result
                else:
                    _log.warning("房间 %s 音频为空，跳过", rid)
            except Exception as exc:
                _log.warning("房间 %s 音频提取失败: %s", rid, exc)

    _log.info("音频提取完成: 成功=%d, 跳过=%d, 总房间=%d", len(audio_data), len(rooms_data) - len(audio_data), len(rooms_data))
    valid_ids = list(audio_data.keys())
    if len(valid_ids) < 2:
        _log.warning("有效音频不足 2 路: %s", valid_ids)
        return AlignResult(success=False, error="有效音频不足 2 路，无法互相关对齐")

    method = "recording" if all(rd.get("is_recording") for rd in rooms_data) else "stream"
    result = align_audio_map(audio_data, SAMPLE_RATE, method=method)
    _log.info(
        "对齐完成: reference=%s, method=%s, offsets=%s, scores=%s",
        result.reference_room_id, method,
        {k: f"{v:.4f}" for k, v in result.offsets.items()},
        {k: f"{v:.3f}" for k, v in result.correlation_scores.items()},
    )
    return result
