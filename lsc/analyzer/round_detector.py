"""无畏契约回合分割检测器（Round-First 方案）。

以音频 RMS 能量包络为主信号，区分战斗阶段和安静阶段（买枪期），
辅以回合结束音效（低频钟声）精确定位回合边界。

算法流程
--------
1. 提取 RMS 能量包络 → 识别高能量（战斗）段
2. 检测回合结束音效 → 作为回合边界硬分隔符
3. 裁剪买枪期 → 每回合起始的低能量区间
4. 时间约束验证 → 利用 Valorant 回合结构先验修正

输出：每个回合的纯战斗阶段片段，去掉买枪/准备/过渡等垃圾时间。
"""
from __future__ import annotations

import logging
import os
import tempfile
import wave
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from lsc.analyzer.ocr_accel import (
    ffmpeg_hwaccel_args,
    read_settings_ocr_accel,
    run_ffmpeg_with_hwaccel_fallback,
)
from lsc.utils.process_launcher import run_hidden

_log = logging.getLogger(__name__)


@dataclass
class ValorantRoundConfig:
    """无畏契约回合检测参数。

    时长约束不假设回合固定时长（手枪局 ~30s、长枪局 ~60-80s、加时 ~80-100s），
    仅用宽松上下限过滤明显噪声/误合并，实际边界由音频事件（RMS 能量、回合结束
    钟声）决定。
    """

    # RMS 能量检测
    rms_window_sec: float = 1.0
    smooth_kernel_sec: int = 3
    energy_percentile: float = 40.0
    combat_merge_gap: float = 8.0
    min_combat_duration: float = 3.0
    # 上限放宽到 130s：加时赛长回合（~80-100s）+ 前后 padding 不应被误判为
    # "两回合合并"而从中点强切。真正的双回合误合并靠钟声分割（Pass 3）处理。
    max_combat_duration: float = 130.0

    # 回合约束
    min_round_gap: float = 18.0
    max_round_total: float = 155.0
    # 部分长枪局买枪期可达 45s（含解说明），留 10s 安全余量
    buy_phase_max: float = 55.0

    # 买枪期裁剪
    # 降低到 25：让基线更贴近实际准备期能量，防止解说/音乐导致准备期能量偏高
    buy_energy_percentile: float = 40.0
    min_combat_after_trim: float = 3.0

    # 输出 padding
    pre_combat_pad: float = 2.0
    # 回合尾冗余裁剪：战斗实际结束（最后交火/回合结束钟声）后仅保留 tail_pad 秒
    # 余韵，砍掉死亡回放/结算画面等尾部垃圾。post_combat_pad 是无钟声可定位时的
    # 保底 padding。
    post_combat_pad: float = 5.0
    tail_pad: float = 6.0
    # 钟声落在战斗段末尾附近多少秒内，视为该回合的结束点（用于精确裁尾）。
    # HVV: 死亡回放/结算画面可达 20-30s，需要更宽的窗口才能捕获尾声钟声。
    chime_tail_window: float = 20.0

    # OCR 状态边界：完整文件分析时优先用 HUD 的"购买阶段 0:00"和胜负结算文本
    # 切完整回合。若结算文本漏检，用下一回合开始点向前回推这一段准备/过渡时长。
    # 2s 采样间隔：回合状态文字持续 ~5s+，2s 采样不会漏检，帧数减半大幅降低 OCR 开销。
    # 资源压力升高时可被外部覆盖为 3~5s。
    phase_sample_interval: float = 2.0
    # OCR 假买枪段过滤：整段最短时长（秒），挡住纯准备期误切
    min_ocr_round_duration: float = 35.0
    # HVV: 下回合 seg_start 已经是 buy_phase 裁剪后的战斗起点，
    # 不需要回退到准备期开始，-10~15s 作为安全余量足够。
    round_inactive_gap: float = 15.0

    # 全回合模式：True = 保留完整回合语义；无 OCR 时仍用适度前 pad + 钟声/post_combat
    # 收尾，禁止 ±buy_phase_max(55s) 糊边界。OCR 路径仍为权威边界。
    full_round: bool = False
    # 无 OCR 的 full_round 起点向前扩展秒数（适度上下文，远小于 buy_phase_max）
    full_round_audio_pre_pad: float = 8.0
    # 全回合模式下的 RMS 合并间隙（秒），比 combat_merge_gap(8s) 更大以防止
    # 回合内安静期（埋包架枪/转点/残局对峙）导致片段切分。30s 可覆盖绝大多数
    # 回合内安静期，同时不会跨回合合并（回合间安静期通常 30-80s）。
    full_round_merge_gap: float = 30.0


_DEFAULT_CONFIG = ValorantRoundConfig()
_SAMPLE_RATE = 8000


def detect_valorant_rounds(
    video_path: str,
    ffmpeg_path: str = "ffmpeg",
    duration: float = 0.0,
    progress_callback: Callable[[str, float, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    config: ValorantRoundConfig | None = None,
    refine_with_ocr: bool = False,
    time_range: tuple[float, float] | None = None,
) -> list[dict[str, Any]]:
    """检测无畏契约视频中的所有回合战斗阶段。

    返回每个回合的纯战斗片段（买枪期已裁掉），格式与前端高光列表兼容。

    信号源策略：音频 RMS 能量 + 低频回合结束钟声为**始终可用的主信号**（录制中
    的文件也能可靠提取音频）。``refine_with_ocr=True`` 时（仅适用于录制**已结束**
    的完整文件）额外用 OCR 回合标记 / 买枪文字校正边界，OCR 不可用或失败时静默
    退回纯音频结果——因此 OCR 永远只是增强，不是必需。

    Args:
        video_path: 视频文件路径
        ffmpeg_path: FFmpeg 可执行文件路径
        duration: 视频总时长（秒），0 则自动探测
        progress_callback: 进度回调 (stage, percent, detail)
        cancel_check: 取消检查回调
        config: 回合检测参数配置
        refine_with_ocr: 是否用 OCR 回合标记/买枪文字校正边界（仅录制结束后可用，
            录制中文件因 moov 未写完导致抽帧失败，应保持 False）

    Returns:
        回合战斗片段列表，按时间顺序排列。
        每个元素: {start, end, score, reason, phase, round_index, ...}
    """
    cfg = config or _DEFAULT_CONFIG

    if not os.path.isfile(video_path):
        _log.warning("视频文件不存在: %s", video_path)
        return []

    if duration <= 0:
        duration = _get_duration(video_path, ffmpeg_path)
        if duration <= 0:
            return []

    range_offset = 0.0
    analysis_duration = duration
    scan_range = time_range
    if time_range is not None:
        start = max(0.0, float(time_range[0]))
        end = max(start, float(time_range[1]))
        if duration > 0:
            end = min(end, duration)
        if end <= start + 1.0:
            return []
        range_offset = start
        analysis_duration = end - start
        scan_range = (start, end)

    phase_rounds: list[dict[str, Any]] = []
    if refine_with_ocr:
        try:
            if progress_callback:
                progress_callback("round_detect", 0.0, "OCR 识别回合状态边界...")
            phase_markers = _detect_round_phase_markers(
                video_path, ffmpeg_path, duration, cfg, cancel_check,
                time_range=scan_range,
            )
            phase_rounds = _build_round_segments_from_phase_markers(
                phase_markers, duration, cfg
            )
            if scan_range is not None:
                range_start, range_end = scan_range
                phase_rounds = [
                    item for item in phase_rounds
                    if float(item.get("end", 0.0)) > range_start + 0.5
                    and float(item.get("start", 0.0)) >= range_start - cfg.round_inactive_gap
                    and float(item.get("start", 0.0)) < range_end
                ]
            if phase_rounds:
                _log.info(
                    "OCR 回合状态分割: %d 个粗粒度回合 (duration=%.0fs, markers=%d)"
                    " — 将继续走音频管线裁 buy phase + 尾部垃圾",
                    len(phase_rounds), duration, len(phase_markers),
                )
            else:
                _log.warning(
                    "OCR 回合状态分割无有效回合 (duration=%.0fs, markers=%d)，"
                    "回退到纯音频检测",
                    duration, len(phase_markers),
                )
        except Exception as exc:
            _log.warning("OCR 回合状态分割失败，回退到纯音频检测: %s", exc)
            phase_rounds = []

    if progress_callback:
        progress_callback("round_detect", 0.0, "提取音频能量包络...")

    # Pass 0: 一次提取 16kHz 音频（RMS + 钟声复用）
    samples, framerate = _extract_audio_pcm(video_path, ffmpeg_path, time_range=scan_range)
    if samples is None or len(samples) < 10:
        _log.warning("音频提取失败或视频过短")
        return []

    # Pass 1: 从同一样本计算 RMS 能量包络
    rms = _compute_rms_envelope(samples, framerate)
    if len(rms) < 10:
        _log.warning("RMS 提取失败或视频过短")
        return []

    if cancel_check and cancel_check():
        return []

    if progress_callback:
        progress_callback("round_detect", 20.0, "识别战斗阶段...")

    # 平滑处理
    kernel_size = cfg.smooth_kernel_sec
    kernel = np.ones(kernel_size) / kernel_size
    smoothed = np.convolve(rms, kernel, mode="same")

    # 动态阈值
    threshold = float(np.percentile(smoothed, cfg.energy_percentile))
    if threshold == 0:
        threshold = float(np.mean(smoothed))

    # Pass 2: 识别战斗段。
    # 若有 OCR 粗粒度回合边界（phase_rounds），直接使用 OCR 边界作为权威回合段，
    # 跳过音频管线的边界修改（chime 分割 / buy phase trim / validate），
    # 因为 OCR HUD 标记直接反映回合状态，比音频能量推断更准确。
    if phase_rounds and threshold == 0:
        # OCR 模式下即使 RMS 全零（静音/测试Mock），也允许继续使用 phase 边界
        threshold = 1.0  # 占位阈值（仅 _format_output 用，buy trim 用实际 RMS）

    if threshold == 0:
        _log.warning("音频能量全为零，无法检测回合")
        return []

    if phase_rounds:
        kept_phase: list[dict[str, Any]] = []
        combat_segments = []
        for r in phase_rounds:
            span = float(r.get("end", 0.0)) - float(r.get("start", 0.0))
            if span < max(cfg.min_combat_duration, cfg.min_ocr_round_duration):
                _log.info(
                    "丢弃过短 OCR 回合 %.1f-%.1f (%.0fs < %.0fs)",
                    float(r.get("start", 0.0)), float(r.get("end", 0.0)),
                    span, cfg.min_ocr_round_duration,
                )
                continue
            s = int(round(max(0.0, float(r.get("start", 0.0)) - range_offset)))
            e = int(round(min(float(len(smoothed)), float(r.get("end", 0.0)) - range_offset)))
            if not (0 <= s < e <= len(smoothed)):
                continue
            if not _ocr_round_has_combat_energy(smoothed, s, e, threshold):
                _log.info(
                    "丢弃低能量 OCR 回合 %.1f-%.1f（疑似买枪/准备期）",
                    float(r.get("start", 0.0)), float(r.get("end", 0.0)),
                )
                continue
            kept_phase.append(r)
            combat_segments.append((s, e))
        phase_rounds = kept_phase
        _log.info(
            "战斗段 (OCR seed): %d 段 - OCR 边界为权威，跳过音频裁切",
            len(combat_segments),
        )

    if not phase_rounds:
        _merge_gap = cfg.combat_merge_gap
        if cfg.full_round:
            _merge_gap = max(_merge_gap, cfg.full_round_merge_gap)
            _log.debug("全回合模式: RMS 合并间隙 %.0fs (full_round_merge_gap=%.0f)",
                       _merge_gap, cfg.full_round_merge_gap)
        combat_segments = _find_combat_segments(
            smoothed, threshold, _merge_gap, cfg.min_combat_duration
        )

    if not combat_segments:
        _log.info("未检测到战斗段 (threshold=%.1f)", threshold)
        return []

    if not phase_rounds:
        _log.info(
            "战斗段初步检测: %d 段 (threshold=%.1f, duration=%.0fs)",
            len(combat_segments), threshold, analysis_duration,
        )

    # Onset fallback: RMS 分段数过少或分段总覆盖时长过高（>80%），
    # 说明主播语音+BGM 导致 RMS 无法有效分段（抖音直播场景）→ 切到 onset
    rms_total_coverage = sum(e - s for s, e in combat_segments) if combat_segments else 0
    rms_too_few = len(combat_segments) <= 2
    rms_too_broad = rms_total_coverage > analysis_duration * 0.8
    if ((rms_too_few or rms_too_broad) and not phase_rounds and analysis_duration > 120
            and samples is not None and framerate > 0):
        _log.info("RMS 分段失效（%d 段），切换到 onset 频谱通量检测 (duration=%.0fs)",
                  len(combat_segments), analysis_duration)
        try:
            from lsc.analyzer.onset_detector import (
                compute_spectral_flux,
                detect_onset_events,
                aggregate_onsets_to_combat_segments,
            )
            flux, flux_rate = compute_spectral_flux(samples, int(framerate))
            onset_events = detect_onset_events(flux, flux_rate)
            if onset_events:
                onset_segments = aggregate_onsets_to_combat_segments(
                    onset_events, analysis_duration
                )
                if onset_segments:
                    _log.info("Onset 检测到 %d 个战斗段", len(onset_segments))
                    # onset 段是秒级 float；_format_output / smoothed 切片要求整数秒索引
                    oformat_input = [
                        (
                            max(0, int(round(float(s["start"])))),
                            max(0, int(round(float(s["end"])))),
                        )
                        for s in onset_segments
                        if float(s.get("end", 0)) > float(s.get("start", 0))
                    ]
                    oformat_input = [(a, b) for a, b in oformat_input if b > a]
                    if not oformat_input:
                        raise ValueError("onset segments empty after int coercion")
                    results0 = _format_output(
                        oformat_input, smoothed, threshold, analysis_duration, cfg,
                        chime_timestamps=None,
                    )
                    if range_offset:
                        for item in results0:
                            item["start"] = round(float(item["start"]) + range_offset, 3)
                            item["end"] = round(float(item["end"]) + range_offset, 3)
                    results0 = _validate_first_marker(results0)
                    if refine_with_ocr and results0:
                        try:
                            results0 = _refine_rounds_with_ocr(
                                results0, video_path, ffmpeg_path, duration, cfg, cancel_check
                            )
                        except Exception as exc:
                            _log.warning("OCR 精修失败，使用纯音频结果: %s", exc)
                    _log.info("Onset 回合分割完成: %d 个回合", len(results0))
                    return results0
        except Exception as exc:
            _log.warning("Onset 检测失败，回退 RMS 结果: %s", exc)

    if cancel_check and cancel_check():
        return []

    if progress_callback:
        progress_callback("round_detect", 40.0, "检测回合结束音效...")

    # Pass 3: 回合结束音效（复用 Pass 0 提取的音频样本）
    seg_offset = scan_range[0] if scan_range else 0.0
    chime_timestamps = [
        ts + seg_offset
        for ts in _detect_chimes_from_samples(samples, framerate, cancel_check)
    ]
    local_chime_timestamps = [
        ts - range_offset
        for ts in chime_timestamps
        if range_offset <= ts <= range_offset + analysis_duration + cfg.chime_tail_window
    ]

    if cancel_check and cancel_check():
        return []

    if phase_rounds:
        # OCR 路径：OCR 边界为权威，跳过 chime 分割 / buy phase trim / validate
        # 直接用 OCR combat_segments 格式化输出
        validated = combat_segments
        if progress_callback:
            progress_callback("round_detect", 90.0, f"OCR 回合: {len(validated)} 个")
    elif cfg.full_round:
        # 全回合模式：跳过钟声切分，仅用 RMS merge_gap 合并段
        # 防止假钟声（BGM/解说/技能音效）在回合中间硬切一刀
        round_boundaries = combat_segments
        trimmed_rounds = round_boundaries  # 也不裁买枪期（full_round 已在 Pass 4 中跳过）
        validated = _validate_rounds(trimmed_rounds, cfg, analysis_duration)
        _log.debug("全回合模式: 跳过钟声分割, %d 个战斗段, 验证后 %d 个回合",
                   len(combat_segments), len(validated))
        if progress_callback:
            progress_callback("round_detect", 90.0, f"全回合: {len(validated)} 个")
    else:
        # 音频路径：用 chime 分割 -> buy phase trim -> validate 细化边界
        if progress_callback:
            progress_callback("round_detect", 60.0, "分割回合边界...")

        # 用 chime 精确分割回合
        round_boundaries = _split_by_round_end_chimes(
            combat_segments, local_chime_timestamps, cfg, rms_len=len(smoothed)
        )

        if cancel_check and cancel_check():
            return []

        if progress_callback:
            progress_callback("round_detect", 75.0, "裁剪买枪阶段...")

        # Pass 4: 买枪期裁剪
        if cfg.full_round:
            # 全回合模式：不裁买枪期，保留完整的准备阶段
            trimmed_rounds = round_boundaries
            _log.debug("全回合模式: 跳过买枪期裁剪, %d 个回合段", len(round_boundaries))
        else:
            trimmed_rounds = _trim_buy_phases(smoothed, round_boundaries, cfg)

        # Pass 5: 验证
        validated = _validate_rounds(trimmed_rounds, cfg, analysis_duration)

        if progress_callback:
            progress_callback("round_detect", 95.0, f"检测完成：{len(validated)} 个回合")

    # 格式化输出（用钟声精确裁尾：战斗结束后仅留 tail_pad 秒余韵）
    # 传入 phase_rounds 使 _format_output 能识别 OCR 确认的边界并跳过音频裁切
    # 将 phase_rounds 的时间戳转为局部时间（减去 range_offset）以匹配 validated 的局部坐标
    local_phase_rounds: list[dict[str, Any]] | None = None
    if phase_rounds:
        local_phase_rounds = []
        for pr in phase_rounds:
            lpr = dict(pr)
            try:
                lpr["start"] = float(pr.get("start", 0.0)) - range_offset
                lpr["end"] = float(pr.get("end", 0.0)) - range_offset
                if pr.get("ocr_end") is not None:
                    lpr["ocr_end"] = float(pr["ocr_end"]) - range_offset
            except (TypeError, ValueError):
                pass
            local_phase_rounds.append(lpr)
    results = _format_output(
        validated, smoothed, threshold, analysis_duration, cfg, local_chime_timestamps,
        phase_rounds=local_phase_rounds,
    )
    if range_offset:
        for item in results:
            item["start"] = round(float(item["start"]) + range_offset, 3)
            item["end"] = round(float(item["end"]) + range_offset, 3)

    # OCR/场景增强兜底（仅录制结束后，失败静默回退纯音频结果）
    # 当 phase_rounds 已提供 OCR 确认边界时，跳过此步骤（避免冗余的全量 OCR 重扫，
    # 19分钟视频全量重扫需 ~6 分钟，是性能瓶颈的主因）
    if refine_with_ocr and results and not phase_rounds:
        try:
            results = _refine_rounds_with_ocr(
                results, video_path, ffmpeg_path, duration, cfg, cancel_check
            )
        except Exception as exc:  # 增强失败绝不能拖垮主结果
            _log.warning("OCR 边界校正失败，使用纯音频结果: %s", exc)

    # Pass 6: 首标记校验 — 首标记不是可信 round_end 时不得构造从 0 开始的片段
    results = _validate_first_marker(results)

    if progress_callback:
        progress_callback("round_detect", 100.0, f"回合分割完成：{len(results)} 个战斗片段")

    _log.info(
        "回合分割完成: %d 个回合 (duration=%.0fs, chimes=%d, ocr_refine=%s)",
        len(results), analysis_duration, len(chime_timestamps), refine_with_ocr,
    )
    return results


def _extract_audio_pcm(
    video_path: str,
    ffmpeg_path: str,
    time_range: tuple[float, float] | None = None,
    sample_rate: int = 16000,
) -> tuple[np.ndarray | None, int]:
    """提取音频 PCM 原始样本（一次提取，多算法复用）。

    统一使用 16kHz 以支持钟声 FFT 的频率分析需求。

    Returns:
        (samples_array, sample_rate) 或 (None, 0) 失败时
    """
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav")
    os.close(tmp_fd)

    cmd = [ffmpeg_path, "-y", "-loglevel", "error"]
    if time_range is not None:
        cmd += ["-ss", f"{time_range[0]:.3f}", "-t", f"{time_range[1] - time_range[0]:.3f}"]
    cmd += ["-i", video_path, "-ar", str(sample_rate), "-ac", "1", "-f", "wav", tmp_path]

    try:
        run_hidden(cmd, capture_output=True, timeout=120)

        with wave.open(tmp_path, "rb") as wf:
            n_frames = wf.getnframes()
            framerate = wf.getframerate()
            raw = wf.readframes(n_frames)

        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if len(samples) == 0:
            return None, 0
        return samples, framerate

    except Exception as exc:
        _log.warning("音频 PCM 提取失败: %s", exc)
        return None, 0
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _compute_rms_envelope(
    samples: np.ndarray,
    framerate: int,
) -> np.ndarray:
    """从 PCM 样本计算 RMS 能量包络（1s 窗口）。"""
    window = framerate
    n_windows = len(samples) // window
    if n_windows < 10:
        return np.array([])
    trimmed = samples[: n_windows * window].reshape(n_windows, window)
    return np.sqrt(np.mean(trimmed**2, axis=1))


def _detect_chimes_from_samples(
    samples: np.ndarray,
    framerate: int,
    cancel_check: Callable[[], bool] | None = None,
) -> list[float]:
    """从 PCM 样本中检测回合结束钟声（低频 200-500Hz FFT 分析）。

    复用一次提取的音频样本，避免重复 FFmpeg 调用。
    """
    if len(samples) < framerate:
        return []

    window_size = int(framerate * 0.25)
    n_windows = len(samples) // window_size
    if n_windows < 4:
        return []

    trimmed = samples[:n_windows * window_size].reshape(n_windows, window_size)
    freqs = np.fft.rfftfreq(window_size, 1.0 / framerate)
    low_mask = (freqs >= 200) & (freqs <= 500)
    low_count = max(int(np.sum(low_mask)), 1)

    all_spectra = np.abs(np.fft.rfft(trimmed, axis=1))
    low_energies = np.sum(all_spectra[:, low_mask] ** 2, axis=1) / low_count

    if low_energies.max() == 0:
        return []

    mean_e = float(np.mean(low_energies))
    std_e = float(np.std(low_energies))
    threshold = mean_e + 3.0 * std_e
    if threshold == 0:
        threshold = float(np.percentile(low_energies, 90))

    prev = np.zeros_like(low_energies)
    prev[1:] = low_energies[:-1]
    spike_mask = (prev > 0) & (low_energies > prev * 5.0) & (low_energies > threshold)
    spike_indices = np.where(spike_mask)[0]

    timestamps = []
    for i in spike_indices:
        if cancel_check and cancel_check():
            break
        timestamps.append(round(i * 0.25, 3))

    if timestamps:
        merged = [timestamps[0]]
        for ts in timestamps[1:]:
            if ts - merged[-1] >= 8.0:
                merged.append(ts)
        timestamps = merged

    return timestamps


def _extract_rms_envelope(
    video_path: str,
    ffmpeg_path: str,
    time_range: tuple[float, float] | None = None,
) -> tuple[np.ndarray | None, int]:
    """提取音频 RMS 能量包络（向后兼容接口）。"""
    samples, framerate = _extract_audio_pcm(video_path, ffmpeg_path, time_range, sample_rate=_SAMPLE_RATE)
    if samples is None:
        return None, 0
    rms = _compute_rms_envelope(samples, framerate)
    if len(rms) == 0:
        return None, 0
    return rms, framerate


def _find_combat_segments(
    smoothed: np.ndarray,
    threshold: float,
    merge_gap: float,
    min_duration: float,
) -> list[tuple[int, int]]:
    """从平滑后的 RMS 中识别高能量（战斗）段。

    Returns:
        [(start_idx, end_idx), ...] 每个 idx 对应 1s 窗口
    """
    is_high = smoothed > threshold

    # 找连续高能量段
    segments: list[tuple[int, int]] = []
    i = 0
    n = len(is_high)
    while i < n:
        if is_high[i]:
            start = i
            while i < n and is_high[i]:
                i += 1
            segments.append((start, i))
        else:
            i += 1

    if not segments:
        return []

    # 合并间距小于 merge_gap 的段（同一回合内短暂安静）
    merged: list[tuple[int, int]] = [segments[0]]
    for s, e in segments[1:]:
        if s - merged[-1][1] < merge_gap:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))

    # 过滤过短段
    return [(s, e) for s, e in merged if (e - s) >= min_duration]


def _detect_round_end_chimes(
    video_path: str,
    ffmpeg_path: str,
    cancel_check: Callable[[], bool] | None = None,
    time_range: tuple[float, float] | None = None,
) -> list[float]:
    """检测回合结束音效（低频钟声 spike）。

    仅分析 200-500Hz 低频段，识别回合结束的标志性钟声。
    """
    from lsc.analyzer.sound_detector import detect_round_end_events

    try:
        events = detect_round_end_events(
            video_path, ffmpeg_path=ffmpeg_path, cancel_check=cancel_check,
            time_range=time_range,
        )
        timestamps = [e["timestamp"] for e in events]
        _log.info("回合结束音效: 检测到 %d 个", len(timestamps))
        return timestamps
    except Exception as exc:
        _log.warning("回合结束音效检测失败: %s", exc)
        return []


def _split_by_round_end_chimes(
    combat_segments: list[tuple[int, int]],
    chime_timestamps: list[float],
    cfg: ValorantRoundConfig,
    rms_len: int,
) -> list[tuple[int, int]]:
    """用回合结束音效分割战斗段。

    如果 chime 落在某个长战斗段中间（可能两个回合被误合并），
    在 chime 位置切分。

    如果没有有效 chime，直接使用战斗段作为回合。
    """
    if not chime_timestamps:
        return combat_segments

    result: list[tuple[int, int]] = []

    for seg_start, seg_end in combat_segments:
        # 找落在这个段内的 chime
        internal_chimes = [
            int(ts) for ts in chime_timestamps
            if seg_start < ts < seg_end - 3  # chime 不在段首尾 3s 内才算有效分割点
        ]
        internal_chimes = sorted(internal_chimes)

        # 过滤密集 chime：同一战斗段内若出现间隔过近的噪声簇，整段不切分。
        # 仅当钟声间距都足够大（接近真实回合间隔）时才按钟声切开。
        if len(internal_chimes) > 1:
            has_dense = any(
                internal_chimes[i] - internal_chimes[i - 1] < 12.0
                for i in range(1, len(internal_chimes))
            )
            if has_dense:
                result.append((seg_start, seg_end))
                continue
            filtered = [internal_chimes[0]]
            for ts in internal_chimes[1:]:
                if ts - filtered[-1] >= 8.0:
                    filtered.append(ts)
            internal_chimes = filtered

        if not internal_chimes:
            result.append((seg_start, seg_end))
            continue

        # 按 chime 位置切分
        prev = seg_start
        for chime_idx in sorted(internal_chimes):
            if chime_idx - prev >= cfg.min_combat_duration:
                result.append((prev, chime_idx))
            # 跳过 chime 后的过渡期（~5s）
            prev = min(chime_idx + 5, seg_end)

        # 最后一段
        if seg_end - prev >= cfg.min_combat_duration:
            result.append((prev, seg_end))

    # 补充：chime 落在两个战斗段之间时，确认两段不属于同一回合
    # 如果两段之间有 chime 且间距 < min_round_gap，合并为一个回合
    if len(result) > 1:
        final: list[tuple[int, int]] = [result[0]]
        for seg_start, seg_end in result[1:]:
            prev_end = final[-1][1]
            gap = seg_start - prev_end

            # 间隙中有 chime → 确认是不同回合，不合并
            has_chime_between = any(
                prev_end <= ts <= seg_start for ts in chime_timestamps
            )
            if has_chime_between:
                final.append((seg_start, seg_end))
            elif gap < cfg.min_round_gap:
                # 无 chime 且间距短 → 可能同一回合的短暂安静
                final[-1] = (final[-1][0], seg_end)
            else:
                final.append((seg_start, seg_end))
        result = final

    return result


def _ocr_round_has_combat_energy(
    smoothed,
    start_i: int,
    end_i: int,
    threshold: float,
    probe_sec: int = 25,
) -> bool:
    """Reject OCR segments that stay buy-phase quiet after the claimed start.

    Reject only when the post-start window stays clearly below the combat
    threshold *and* shows no energy rise. Do not require peak >= threshold,
    because the dynamic threshold is often set by later combat peaks.
    """
    import numpy as np

    if end_i <= start_i or start_i >= len(smoothed) or threshold <= 0:
        return False
    window = smoothed[start_i:min(end_i, start_i + max(5, probe_sec))]
    if len(window) < 3:
        return True
    peak = float(np.max(window))
    head = window[: min(5, len(window))]
    rest = window[min(5, len(window)) :] if len(window) > 5 else window
    head_mean = float(np.mean(head))
    rest_mean = float(np.mean(rest)) if len(rest) else head_mean
    # 纯买枪：全程远低于战斗阈值，且后半段无明显抬升
    if peak < threshold * 0.5 and rest_mean < max(head_mean * 1.3, threshold * 0.35):
        return False
    return True


def _trim_buy_phases(
    smoothed: np.ndarray,
    rounds: list[tuple[int, int]],
    cfg: ValorantRoundConfig,
) -> list[tuple[int, int]]:
    """裁剪每个回合开头的买枪期。

    买枪期特征：回合起始处连续低能量区间（低于该回合能量的 30th percentile）。
    """
    trimmed: list[tuple[int, int]] = []

    for round_idx, (seg_start, seg_end) in enumerate(rounds):
        # 检查该段前方是否有买枪期
        # 向前扩展搜索范围：从段起点往前最多 buy_phase_max 秒
        scan_start = max(0, seg_start - int(cfg.buy_phase_max))
        previous_round_end = rounds[round_idx - 1][1] if round_idx > 0 else 0
        scan_start = max(previous_round_end, scan_start)
        scan_region = smoothed[scan_start:seg_end]

        if len(scan_region) == 0:
            trimmed.append((seg_start, seg_end))
            continue

        # 该回合的准备期基线和战斗阶段抬升阈值。只用 30th percentile 容易把平滑后的
        # 买枪期尾巴误判为战斗开始；55th percentile 更接近屏障落下后的持续走位/架点能量。
        round_threshold = float(np.percentile(scan_region, cfg.buy_energy_percentile))
        phase_threshold = max(round_threshold, float(np.percentile(scan_region, 55.0)))
        phase_epsilon = max(1e-6, phase_threshold * 0.01)

        # 从准备期搜索窗口向后扫描，找第一个持续高于阶段阈值的点。允许起点早于初始
        # 高能量段，以保留屏障落下后的静步、架点和第一波对枪前摇。
        combat_start = seg_start
        search_end = min(seg_start + int(cfg.buy_phase_max), seg_end)
        for idx in range(scan_start, search_end):
            if idx < len(smoothed) and smoothed[idx] >= round_threshold:
                # 确认不是噪声：后续 3s 内至少 2s 也是高能量
                lookahead_end = min(idx + 3, seg_end, len(smoothed))
                if lookahead_end > idx:
                    high_count = np.sum(smoothed[idx:lookahead_end] >= phase_threshold - phase_epsilon)
                    if high_count >= min(2, lookahead_end - idx):
                        combat_start = idx
                        break

        # 安全阀：裁剪后战斗段太短则不裁剪
        if seg_end - combat_start < cfg.min_combat_after_trim:
            combat_start = seg_start

        trimmed_sec = combat_start - seg_start
        if trimmed_sec > 3:
            _log.debug(
                "买枪期裁剪: [%d-%d] → [%d-%d] (裁剪 %ds)",
                seg_start, seg_end, combat_start, seg_end, trimmed_sec,
            )

        trimmed.append((combat_start, seg_end))

    return trimmed


def _validate_rounds(
    rounds: list[tuple[int, int]],
    cfg: ValorantRoundConfig,
    duration: float,
) -> list[tuple[int, int]]:
    """用 Valorant 时间约束验证并修正回合列表。"""
    if not rounds:
        return []

    validated: list[tuple[int, int]] = []

    for seg_start, seg_end in rounds:
        combat_len = seg_end - seg_start

        # 过滤超长段：可能是两个回合误合并
        if combat_len > cfg.max_combat_duration:
            # 尝试在中点附近找能量低谷进行分割
            mid = (seg_start + seg_end) // 2
            validated.append((seg_start, mid))
            validated.append((mid, seg_end))
            _log.debug("超长回合分割: [%d-%d] → 两段", seg_start, seg_end)
            continue

        # 过滤过短段（< min_combat_duration）
        if combat_len < cfg.min_combat_duration:
            _log.debug("过短段丢弃: [%d-%d] (%ds)", seg_start, seg_end, combat_len)
            continue

        validated.append((seg_start, seg_end))

    # 合并间距过小的相邻段（< min_round_gap 且合并后不超长）
    if len(validated) > 1:
        merged: list[tuple[int, int]] = [validated[0]]
        for seg_start, seg_end in validated[1:]:
            gap = seg_start - merged[-1][1]
            merged_len = seg_end - merged[-1][0]
            if gap < cfg.min_round_gap * 0.3 and merged_len <= cfg.max_combat_duration:
                merged[-1] = (merged[-1][0], seg_end)
            else:
                merged.append((seg_start, seg_end))
        validated = merged

    return validated


def _format_output(
    rounds: list[tuple[int, int]],
    smoothed: np.ndarray,
    threshold: float,
    duration: float,
    cfg: ValorantRoundConfig,
    chime_timestamps: list[float] | None = None,
    phase_rounds: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """将内部回合表示转换为前端兼容的输出格式。

    cfg.full_round 且无 OCR：起点仅用适度前 pad（full_round_audio_pre_pad），
    终点走钟声 / 下回合回推 / post_combat_pad，禁止 ±buy_phase_max 糊边界。
    False（默认）时进行战斗段尾部裁剪：若战斗段末尾附近（chime_tail_window 内）存在回合结束钟声，
    以钟声位置作为战斗实际结束点，仅保留 tail_pad 秒余韵，砍掉死亡回放/结算画面
    等尾部垃圾；无钟声可定位时退回 post_combat_pad 保底 padding。

    OCR 优先：若该回合有 OCR 确认的边界（phase_rounds 中 ocr_confirmed=True），
    直接使用 OCR 的 start/end，跳过音频钟声/回推裁切，避免音频管线覆盖 OCR 精确边界。
    """
    results: list[dict[str, Any]] = []
    chimes = sorted(chime_timestamps) if chime_timestamps else []

    # 构建 OCR 回合查找索引：用 (start, end) 的四舍五入元组快速匹配。
    # 持续分析时 combat 段 end 可能被夹断到 len(smoothed)，与 OCR end 差 1s，
    # 仅靠精确坐标会丢元数据并退回 full_round → 永远无法自动入列。
    # phase_rounds 与 rounds 在 OCR seed 路径中是 1:1 同序，优先按索引取。
    ocr_by_pos: dict[tuple[int, int], dict[str, Any]] = {}
    if phase_rounds:
        for pr in phase_rounds:
            try:
                ps = int(round(float(pr.get("start", 0.0))))
                pe = int(round(float(pr.get("end", 0.0))))
                ocr_by_pos[(ps, pe)] = pr
            except (TypeError, ValueError):
                pass

    for idx, (seg_start, seg_end) in enumerate(rounds):
        # 查找该回合是否有 OCR 确认的边界
        ocr_info = None
        if phase_rounds and idx < len(phase_rounds):
            ocr_info = phase_rounds[idx]
        if ocr_info is None:
            ocr_info = ocr_by_pos.get((int(round(float(seg_start))), int(round(float(seg_end)))))
        ocr_confirmed = bool(ocr_info and ocr_info.get("ocr_confirmed"))
        ocr_end_ts = float(ocr_info["ocr_end"]) if ocr_info and ocr_info.get("ocr_end") is not None else None

        # 计算该回合的战斗强度评分
        try:
            seg_start_i = int(round(float(seg_start)))
            seg_end_i = int(round(float(seg_end)))
        except (TypeError, ValueError):
            seg_start_i, seg_end_i = 0, 0
        if seg_start_i < len(smoothed) and seg_end_i <= len(smoothed) and seg_end_i > seg_start_i:
            peak_rms = float(np.max(smoothed[seg_start_i:seg_end_i]))
            score = min(1.0, peak_rms / (threshold * 2.0))
            score = max(0.3, score)
        else:
            score = 0.5

        combat_duration = float(seg_end) - float(seg_start)
        if cfg.full_round and not ocr_info:
            # 无 OCR：适度前 pad，禁止 buy_phase_max(55s) 糊起点
            pre_pad = max(cfg.pre_combat_pad, float(cfg.full_round_audio_pre_pad))
            buy_start = max(0, int(round(float(seg_start) - pre_pad)))
            if idx > 0:
                prev_end = int(round(results[-1].get("end", 0)))
                buy_start = max(buy_start, prev_end)
            start_sec = float(buy_start)
        else:
            start_sec = max(0.0, float(seg_start) - cfg.pre_combat_pad)
        if ocr_info:
            start_sec = max(0.0, float(ocr_info.get("start", seg_start)))

        # OCR 确认的回合：直接使用 OCR 边界，跳过音频裁切
        if ocr_info:
            # 优先 OCR 权威 end；combat 段可能被夹断到 len(smoothed)
            try:
                ocr_span_end = float(ocr_info.get("end", seg_end))
            except (TypeError, ValueError):
                ocr_span_end = float(seg_end)
            combat_end_sec = ocr_span_end
            tail_reason = ocr_info.get("tail_by", "ocr_phase")

            if ocr_end_ts is not None and ocr_end_ts > float(seg_start):
                combat_end_sec = ocr_end_ts
                tail_reason = "ocr_phase"

            end_sec = min(duration, combat_end_sec + cfg.tail_pad)

            if end_sec <= start_sec + cfg.min_combat_duration:
                end_sec = min(duration, float(seg_end) + cfg.post_combat_pad)

            results.append({
                "start": round(start_sec, 3),
                "end": round(end_sec, 3),
                "score": round(score, 3),
                "reason": f"回合 {idx + 1}: 战斗阶段 ({combat_duration}s)",
                "phase": "combat",
                "round_index": idx + 1,
                "tail_by": tail_reason,
                "start_by": ocr_info.get("start_by", "ocr_buy_exit") if ocr_info else "ocr_buy_exit",
                "end_by": ocr_info.get("end_by") if ocr_info else None,
                "ocr_confirmed": ocr_confirmed,
                "speech_score": 0.0,
                "visual_score": 0.0,
                "transcript": "",
            })
            continue

        # === 无 OCR：钟声 / 回推 / post_combat（full_round 也不再用 ±buy_phase_max）===
        combat_end_sec = float(seg_end)
        tail_reason = "audio"

        if chimes:
            # 残响可能把 seg_end 拉过真正的回合结束钟声；不能只在 tip 窗口搜，
            # 否则钟声会落在 lo 之外被漏掉。取段内（或略超 seg_end）最后一声作为结束。
            hi = float(seg_end) + cfg.chime_tail_window
            candidates = [
                c for c in chimes
                if float(seg_start) + 1.0 < float(c) <= hi
            ]
            if candidates:
                in_segment = [c for c in candidates if float(c) <= float(seg_end) + 1.0]
                if in_segment:
                    combat_end_sec = max(in_segment)
                else:
                    combat_end_sec = min(candidates, key=lambda c: abs(float(c) - float(seg_end)))
                tail_reason = "chime"

        if tail_reason == "audio":
            next_start = rounds[idx + 1][0] if idx + 1 < len(rounds) else None
            if next_start is not None and float(next_start) > float(seg_end):
                inferred_end = min(
                    float(seg_end) + cfg.post_combat_pad,
                    float(next_start) - cfg.round_inactive_gap,
                )
                if inferred_end > start_sec + cfg.min_combat_duration:
                    combat_end_sec = inferred_end
                    tail_reason = "inferred"

        end_sec = min(duration, combat_end_sec + cfg.tail_pad)
        if end_sec <= start_sec + cfg.min_combat_duration:
            end_sec = min(duration, float(seg_end) + cfg.post_combat_pad)
            tail_reason = "audio"
        if idx + 1 < len(rounds):
            next_start = float(rounds[idx + 1][0])
            end_sec = min(end_sec, next_start - 1.0)

        if cfg.full_round:
            results.append({
                "start": round(start_sec, 3),
                "end": round(end_sec, 3),
                "score": round(score, 3),
                "reason": f"回合 {idx + 1}: 完整阶段 ({combat_duration}s 战斗)",
                "phase": "full_round",
                "round_index": idx + 1,
                "tail_by": tail_reason if tail_reason != "audio" else "full_round",
                "start_by": "full_round",
                "end_by": "full_round",
                "speech_score": 0.0,
                "visual_score": 0.0,
                "transcript": "",
            })
        else:
            results.append({
                "start": round(start_sec, 3),
                "end": round(end_sec, 3),
                "score": round(score, 3),
                "reason": f"回合 {idx + 1}: 战斗阶段 ({combat_duration}s)",
                "phase": "combat",
                "round_index": idx + 1,
                "tail_by": tail_reason,
                "speech_score": 0.0,
                "visual_score": 0.0,
                "transcript": "",
            })

    return results


def _validate_first_marker(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """首标记不是可信 round_end 时不得构造从 0 开始的片段。

    如果第一个回合片段从 0 开始且不是可信战斗段（phase!=combat 或 score<0.5），
    说明这是录像开头而非真正的回合开始，应丢弃该片段。
    """
    if not results:
        return results
    first = results[0]
    try:
        start = float(first.get("start", 0.0))
    except (TypeError, ValueError):
        start = 0.0
    if start == 0.0 and first.get("phase") != "combat":
        score = float(first.get("score", 0.0))
        if score < 0.5:
            return results[1:]
    return results


def _detect_round_phase_markers(
    video_path: str,
    ffmpeg_path: str,
    duration: float,
    cfg: ValorantRoundConfig,
    cancel_check: Callable[[], bool] | None = None,
    time_range: tuple[float, float] | None = None,
) -> list[dict[str, Any]]:
    """从 Valorant HUD OCR 中提取回合开始/结束状态标记。

    该路径服务于录制完成后的精修：直播录制中文件仍使用音频路径，避免 moov 未写完时
    抽帧失败。采样区域覆盖顶部计时器和中央"购买阶段/获胜/败北"提示。
    """
    try:
        from lsc.analyzer.ocr_detector import _get_ocr, _get_video_resolution
    except ImportError:
        return []

    import re
    import shutil

    width, height = _get_video_resolution(video_path, ffmpeg_path)
    if width <= 0 or height <= 0:
        return []

    # 1280x720 下约等于 x=520,y=85,w=260,h=115，只覆盖中央状态字样。
    # 小图直接 OCR 对中文不稳，因此抽帧时放大 3 倍。
    crop_ratio = (0.40625, 0.118, 0.203, 0.16)
    x = int(width * crop_ratio[0])
    y = int(height * crop_ratio[1])
    w = int(width * crop_ratio[2])
    h = int(height * crop_ratio[3])

    range_offset = 0.0
    scan_duration = duration
    if time_range is not None:
        range_start = max(0.0, float(time_range[0]))
        range_end = max(range_start, float(time_range[1]))
        if duration > 0:
            range_end = min(range_end, duration)
        if range_end <= range_start + 1.0:
            return []
        range_offset = range_start
        scan_duration = range_end - range_start

    tmp_dir = tempfile.mkdtemp(prefix="lsc_round_phase_")
    try:
        output_pattern = os.path.join(tmp_dir, "phase_%05d.jpg")
        fps = max(0.2, 1.0 / max(cfg.phase_sample_interval, 0.1))
        cmd = [ffmpeg_path, "-y", "-loglevel", "error"]
        if time_range is not None:
            cmd += ["-ss", f"{range_offset:.3f}", "-t", f"{scan_duration:.3f}"]
        cmd += [
            "-i", video_path,
            "-vf", f"fps={fps:.3f},crop={w}:{h}:{x}:{y},scale={w * 3}:{h * 3},showinfo",
            "-q:v", "2",
            output_pattern,
        ]
        hw = ffmpeg_hwaccel_args(read_settings_ocr_accel())
        result = run_ffmpeg_with_hwaccel_fallback(cmd, hwaccel_args=hw, timeout=360)

        frame_ts_pattern = re.compile(r"pts_time:(\d+\.?\d*)")
        precise_timestamps = [
            float(m.group(1)) for m in frame_ts_pattern.finditer(result.stderr)
        ]
        deduped_ts: list[float] = []
        for ts in precise_timestamps:
            if not deduped_ts or ts > deduped_ts[-1] + 0.001:
                deduped_ts.append(ts)
        precise_timestamps = deduped_ts

        ocr = _get_ocr()
        frame_files = sorted(f for f in os.listdir(tmp_dir) if f.endswith(".jpg"))
        end_markers: list[dict[str, Any]] = []
        buy_hits: list[dict[str, Any]] = []
        last_start = -999.0
        last_end = -999.0
        end_keywords = (
            # 中文
            "获胜", "胜利", "败北", "失败", "队伍已淘", "队伍已被淘",
            # 英文（中英混合客户端）
            "victory", "defeat", "eliminated", "clutch", "ace", "triple",
            "spike deton", "spike defus", "time expired",
        )

        try:
            import cv2
            has_cv2 = True
        except ImportError:
            cv2 = None  # type: ignore[assignment]
            has_cv2 = False

        for i, fname in enumerate(frame_files):
            if cancel_check and cancel_check():
                break

            fpath = os.path.join(tmp_dir, fname)
            if has_cv2:
                img = cv2.imread(fpath)
                if img is None:
                    continue
                small = cv2.resize(img, (w, h))
                hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                bright_mask = (
                    ((gray > 185) & (hsv[:, :, 1] < 80))
                    | ((hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 160))
                )
                active_ratio = float(np.sum(bright_mask)) / max(1, w * h)
                if active_ratio < 0.12:
                    continue

            try:
                result_ocr, _ = ocr(fpath)
            except Exception:
                continue

            confident_lines = [
                line for line in result_ocr or []
                if len(line) >= 3 and line[2] >= 0.40
            ]
            current_text = " ".join(line[1] for line in confident_lines)
            if not current_text:
                continue

            timestamp = (
                precise_timestamps[i]
                if i < len(precise_timestamps)
                else i * cfg.phase_sample_interval
            )
            if time_range is not None:
                timestamp += range_offset
            if timestamp < 0 or timestamp > duration + 1:
                continue

            is_buy = "购买阶段" in current_text or "购买" in current_text
            if is_buy and timestamp - last_start >= 1.0:
                buy_hits.append({
                    "timestamp": round(timestamp, 3),
                    "type": "round_start",
                    "text": current_text[:100],
                    "score": 0.8,
                })
                last_start = timestamp

            current_text_lower = current_text.lower()
            is_round_end = any(keyword in current_text_lower for keyword in end_keywords)
            if is_round_end and timestamp - last_end >= cfg.min_round_gap:
                end_markers.append({
                    "timestamp": round(timestamp, 3),
                    "type": "round_end",
                    "text": current_text[:100],
                    "score": 0.8,
                })
                last_end = timestamp

        start_markers: list[dict[str, Any]] = []
        buy_run: list[dict[str, Any]] = []
        for hit in buy_hits:
            ts = float(hit.get("timestamp", 0.0))
            prev_ts = float(buy_run[-1].get("timestamp", 0.0)) if buy_run else -999.0
            if not buy_run or ts - prev_ts <= 12.0:
                buy_run.append(hit)
                continue
            start_markers.append(buy_run[-1])
            buy_run = [hit]
        if buy_run:
            start_markers.append(buy_run[-1])

        grouped_end_markers: list[dict[str, Any]] = []
        end_run: list[dict[str, Any]] = []
        for hit in sorted(end_markers, key=lambda item: item.get("timestamp", 0.0)):
            ts = float(hit.get("timestamp", 0.0))
            prev_ts = float(end_run[-1].get("timestamp", 0.0)) if end_run else -999.0
            if not end_run or ts - prev_ts <= 8.0:
                end_run.append(hit)
                continue
            grouped_end_markers.append(end_run[-1])
            end_run = [hit]
        if end_run:
            grouped_end_markers.append(end_run[-1])

        markers = start_markers + grouped_end_markers
        markers.sort(key=lambda item: item.get("timestamp", 0.0))
        return markers
    except Exception as exc:
        _log.warning("OCR 回合状态标记检测失败: %s", exc)
        return []
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _dedupe_marker_times(times: list[float], min_gap: float) -> list[float]:
    deduped: list[float] = []
    for ts in sorted(times):
        if not deduped or ts - deduped[-1] >= min_gap:
            deduped.append(ts)
    return deduped



def _refine_rounds_with_ocr(
    rounds: list[dict[str, Any]],
    video_path: str,
    ffmpeg_path: str,
    duration: float,
    cfg: ValorantRoundConfig,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    """用 OCR 回合标记 / 买枪文字校正音频回合边界（增强兜底）。

    仅适用于录制**已结束**的完整文件（录制中因 moov 未写完抽帧会失败）。
    校正策略：对每个音频回合，若其起点附近存在"买枪→非买枪"的 OCR 标记跳变，
    用该跳变点（战斗实际开始）收紧片段起点，剔除残留的买枪期。

    OCR 引擎缺失、抽帧失败或无有效标记时，原样返回音频结果——OCR 永远只是增强。
    """
    try:
        from lsc.analyzer.ocr_detector import _detect_round_markers, _get_video_resolution
    except ImportError:
        _log.debug("OCR 模块不可用，跳过边界校正")
        return rounds

    if cancel_check and cancel_check():
        return rounds

    width, height = _get_video_resolution(video_path, ffmpeg_path)
    # Valorant 回合标记 HUD 裁剪区域（与 ocr_detector._GAME_CONFIGS["valorant"] 一致）
    marker_crop = (0.35, 0.01, 0.30, 0.06)
    # 采样间隔取 1.0s：回合校正只需秒级精度，降低 OCR 帧数开销
    markers = _detect_round_markers(
        [], video_path, ffmpeg_path, width, height, marker_crop, 1.0, "Valorant",
    )
    if not markers:
        _log.info("OCR 未检测到回合标记，保留纯音频边界")
        return rounds

    # 提取"买枪→非买枪"的战斗开始时间戳（战斗实际开始点）
    combat_start_marks: list[float] = []
    seen_buy = False
    for m in sorted(markers, key=lambda x: x.get("timestamp", 0.0)):
        if m.get("phase") == "buy":
            seen_buy = True
        elif seen_buy:
            combat_start_marks.append(m.get("timestamp", 0.0))
            seen_buy = False

    if not combat_start_marks:
        _log.info("OCR 无买枪→战斗跳变，保留纯音频边界")
        return rounds

    refined = 0
    for rd in rounds:
        rd_start = rd.get("start", 0.0)
        rd_end = rd.get("end", 0.0)
        # 找落在该回合起点前后 buy_phase_max 秒内的战斗开始标记
        candidates = [
            t for t in combat_start_marks
            if rd_start - cfg.buy_phase_max <= t <= rd_start + cfg.buy_phase_max
        ]
        if not candidates:
            continue
        # 取最接近音频起点的标记；仅当它比音频起点更晚（能剔除更多买枪期）且
        # 裁剪后仍有足够战斗时长时才采用
        new_start = min(candidates, key=lambda t: abs(t - rd_start))
        if new_start > rd_start and (rd_end - new_start) >= cfg.min_combat_after_trim:
            rd["start"] = round(new_start, 3)
            rd["tail_by"] = rd.get("tail_by", "audio")
            rd["start_by"] = "ocr"
            refined += 1

    _log.info("OCR 边界校正: %d/%d 个回合起点被收紧", refined, len(rounds))
    return rounds


def _get_duration(video_path: str, ffmpeg_path: str) -> float:
    """获取视频时长。"""
    from pathlib import Path
    ffprobe = str(Path(ffmpeg_path).with_name("ffprobe" + Path(ffmpeg_path).suffix))  # #30: basename only
    try:
        result = run_hidden(
            [ffprobe, "-v", "error", "-probesize", "50M", "-analyzeduration", "10M",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())
    except Exception as exc:
        _log.debug("ffprobe 取时长失败: %s", exc)

    # fallback: ffmpeg -i
    try:
        import re
        result = run_hidden(
            [ffmpeg_path, "-i", video_path, "-hide_banner"],
            capture_output=True, text=True, timeout=10,
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", result.stderr)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception as exc:
        _log.debug("ffmpeg fallback 取时长失败: %s", exc)

    return 0.0


def _build_round_segments_from_phase_markers(
    markers: list[dict[str, Any]],
    duration: float,
    cfg: ValorantRoundConfig,
) -> list[dict[str, Any]]:
    """Build round clips from OCR buy-phase and result markers."""
    if duration <= 0:
        return []

    starts = _dedupe_marker_times(
        [
            float(m.get("timestamp", 0.0))
            for m in markers
            if m.get("type") == "round_start"
        ],
        min_gap=cfg.min_round_gap,
    )
    ends = _dedupe_marker_times(
        [
            float(m.get("timestamp", 0.0))
            for m in markers
            if m.get("type") == "round_end"
        ],
        min_gap=cfg.min_round_gap * 0.5,
    )
    segment_starts = [s for s in starts if 0 < s < duration]
    results: list[dict[str, Any]] = []

    for idx, buy_marker in enumerate(segment_starts):
        # ponytail: reuse the existing OCR interval as the barrier-exit estimate.
        start = min(duration, round(buy_marker + cfg.phase_sample_interval, 3))
        next_buy = segment_starts[idx + 1] if idx + 1 < len(segment_starts) else None
        explicit_ends = [
            e for e in ends
            if start + cfg.min_combat_duration <= e
            and (next_buy is None or e < next_buy)
            and e <= duration
        ]

        if explicit_ends:
            end = explicit_ends[0]
            end_by = "ocr_result"
            tail_by = "ocr_phase"
            ocr_end = end
        elif next_buy is not None:
            end = next_buy
            end_by = "next_buy"
            tail_by = "ocr_phase"
            ocr_end = None
        else:
            end = duration
            end_by = "open_tail"
            tail_by = "open_tail"
            ocr_end = None

        start = max(0.0, round(start, 3))
        end = min(duration, round(end, 3))
        if end - start < max(cfg.min_combat_duration, cfg.min_ocr_round_duration):
            continue

        results.append({
            "start": start,
            "end": end,
            "score": 0.8,
            "reason": "Valorant round OCR boundary",
            "phase": "combat",
            "round_index": len(results) + 1,
            "tail_by": tail_by,
            "start_by": "ocr_buy_exit",
            "end_by": end_by,
            "ocr_confirmed": end_by in {"ocr_result", "next_buy"},
            "ocr_start": start,
            "ocr_end": ocr_end,
            "speech_score": 0.0,
            "visual_score": 0.0,
            "transcript": "",
        })

    return results


__all__ = ["detect_valorant_rounds", "ValorantRoundConfig"]
