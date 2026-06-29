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

_log = logging.getLogger(__name__)

# 提取音频的时长（秒）—— 3 秒在 CDN 延迟差（通常 1-3s）下仍有足够重叠
AUDIO_DURATION = 3.0
# 采样率：16 kHz mono，人声频段足够，配合抛物线插值可达到亚毫秒精度
SAMPLE_RATE = 16000
# 录制文件读取时距离直播边缘的安全缓冲（秒）
_SEEK_BUFFER = 2.0
# 互相关置信度阈值（低于此值视为内容不相关）
_CORRELATION_THRESHOLD = 0.1
# 最大并发音频提取数
_MAX_EXTRACT_WORKERS = 6


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
        for k, v in headers.items():
            cmd += ["-headers", f"{k}: {v}\r\n"]
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
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if proc.stdout is None:
            _log.warning("FFmpeg stdout 为空: source=%s", source[:200])
            return np.array([], dtype=np.float32)
        raw = proc.stdout.read()
        proc.wait(timeout=duration + 20)
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

    ref = ref_audio - np.mean(ref_audio)
    other = other_audio - np.mean(other_audio)
    ref_std = float(np.std(ref))
    other_std = float(np.std(other))
    if ref_std > 1e-10:
        ref = ref / ref_std
    if other_std > 1e-10:
        other = other / other_std

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
    score = float(abs_corr[peak] / overlap)
    _log.debug("互相关结果: offset=%.4fs (%.1fms), score=%.3f", offset_sec, offset_sec * 1000, score)
    return offset_sec, score


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

    ref_id = valid_ids[0]
    ref_audio = audio_data[ref_id]
    raw_offsets: dict[str, float] = {ref_id: 0.0}
    scores: dict[str, float] = {ref_id: 1.0}

    _log.info("开始互相关计算: 参考房间=%s (%d samples), 比较房间=%s", ref_id, len(ref_audio), valid_ids[1:])
    for rid in valid_ids[1:]:
        offset, score = compute_offset(ref_audio, audio_data[rid], SAMPLE_RATE)
        raw_offsets[rid] = offset
        scores[rid] = score
        _log.debug("互相关: %s vs %s → offset=%.4fs (%.1fms), score=%.3f", ref_id, rid, offset, offset * 1000, score)

    slowest_id = min(valid_ids, key=lambda rid: raw_offsets[rid])
    slowest_offset = raw_offsets[slowest_id]
    _log.info("最慢房间: %s (raw_offset=%.4fs)", slowest_id, slowest_offset)

    offsets: dict[str, float] = {}
    for rid in valid_ids:
        offsets[rid] = max(0.0, raw_offsets[rid] - slowest_offset)

    method = "recording" if all(rd.get("is_recording") for rd in rooms_data) else "stream"
    _log.info(
        "对齐完成: reference=%s, method=%s, offsets=%s, scores=%s",
        slowest_id, method,
        {k: f"{v:.4f}" for k, v in offsets.items()},
        {k: f"{v:.3f}" for k, v in scores.items()},
    )

    return AlignResult(
        success=True,
        offsets=offsets,
        reference_room_id=slowest_id,
        method=method,
        correlation_scores=scores,
    )
