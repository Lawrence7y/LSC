"""Spectral Flux onset detection for combat segment identification."""
from __future__ import annotations

import numpy as np

__all__ = [
    "compute_spectral_flux",
    "detect_onset_events",
    "aggregate_onsets_to_combat_segments",
]


def compute_spectral_flux(
    samples: np.ndarray,
    sample_rate: int = 16000,
    frame_ms: float = 46.4,
    hop_ms: float = 100.0,
    n_fft: int = 512,
) -> tuple[np.ndarray, float]:
    sample_rate = int(sample_rate)
    if sample_rate <= 0:
        return np.zeros(1), 1.0
    frame_len = max(1, int(sample_rate * frame_ms / 1000))
    hop_len = max(1, int(sample_rate * hop_ms / 1000))
    n_frames = 1 + (len(samples) - frame_len) // hop_len
    if n_frames < 2:
        return np.zeros(1), sample_rate / hop_len

    shape = (int(n_frames), int(frame_len))
    strides = (int(hop_len) * samples.strides[0], samples.strides[0])
    frames = np.lib.stride_tricks.as_strided(samples, shape=shape, strides=strides).copy()
    window = np.hanning(frame_len)
    spectra = np.abs(np.fft.rfft(frames * window, axis=1))

    norms = np.sqrt(np.sum(spectra ** 2, axis=1, keepdims=True))
    norms = np.maximum(norms, 1e-12)
    spectra_norm = spectra / norms

    diff = np.diff(spectra_norm, axis=0, prepend=spectra_norm[:1])
    diff_rectified = np.maximum(diff, 0)
    flux = np.sum(diff_rectified, axis=1)

    frame_rate = float(sample_rate) / float(hop_len)
    return flux, frame_rate


def detect_onset_events(
    flux: np.ndarray,
    frame_rate: float,
    pre_avg_ms: float = 3000,
    threshold_multiplier: float = 2.5,
    min_gap_sec: float = 8.0,
) -> list[dict]:
    n = len(flux)
    if n < 3:
        return []

    avg_win = max(3, int(frame_rate * pre_avg_ms / 1000))
    half = avg_win // 2

    cumsum = np.cumsum(np.concatenate([[0.0], flux]))
    local_mean = np.zeros(n)
    local_std = np.zeros(n)
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        window = flux[lo:hi]
        local_mean[i] = (cumsum[hi] - cumsum[lo]) / (hi - lo)
        local_std[i] = float(np.std(window)) if len(window) > 1 else 0.0

    threshold = local_mean + threshold_multiplier * local_std

    peaks: list[int] = []
    i = 1
    skip = int(frame_rate * min_gap_sec)
    while i < n - 1:
        if flux[i] > threshold[i] and flux[i] > flux[i - 1] and flux[i] > flux[i + 1]:
            peaks.append(i)
            i += max(1, skip)
        else:
            i += 1

    events = []
    for idx in peaks:
        ts = idx / frame_rate
        t_val = threshold[idx] if threshold[idx] > 1e-12 else 1e-12
        score = min(1.0, (flux[idx] / t_val) / 3.0)
        events.append({
            "timestamp": round(ts, 3),
            "type": "onset",
            "score": max(0.3, score),
            "flux_value": float(flux[idx]),
        })
    return events


def aggregate_onsets_to_combat_segments(
    onsets: list[dict],
    total_duration: float,
    merge_gap: float = 15.0,
    min_onset_density: float = 2.0,
    min_segment_sec: float = 15.0,
    padding_sec: float = 5.0,
) -> list[dict]:
    if not onsets:
        return []

    timestamps = sorted(o["timestamp"] for o in onsets)

    clusters: list[list[float]] = [[timestamps[0]]]
    for ts in timestamps[1:]:
        if ts - clusters[-1][-1] < merge_gap:
            clusters[-1].append(ts)
        else:
            clusters.append([ts])

    segments = []
    for idx, cluster in enumerate(clusters):
        start = min(cluster) - padding_sec
        end = max(cluster) + padding_sec
        seg_dur = end - start
        density = len(cluster) / max(seg_dur, 1.0) * 60

        if density >= min_onset_density and seg_dur >= min_segment_sec:
            segments.append({
                "start": max(0.0, round(start, 3)),
                "end": round(end, 3),
                "score": min(1.0, density / 5.0),
                "reason": f"交火期: {len(cluster)} 个瞬态事件 ({density:.1f}/min)",
                "phase": "combat",
                "onset_count": len(cluster),
            })

    return segments
