"""FrameProvider 低风险帧缓存回归测试。"""
from __future__ import annotations

import pytest

from lsc.analyzer.frame_provider import FrameProvider
from lsc.utils.cancellable_ffmpeg import FFmpegCancelled


def _make_extractor(calls: list[tuple[float, float, float]]):
    def _fake_extract(video_path, *, start_sec, end_sec, fps, **kwargs):
        calls.append((float(start_sec), float(end_sec), float(fps)))
        frames = []
        ts = float(start_sec)
        step = 1.0 / max(0.1, float(fps))
        while ts <= float(end_sec) + 0.001:
            frames.append((round(ts, 3), f"img-{ts:.1f}"))
            ts += step
        return frames

    return _fake_extract


def test_frame_provider_reuses_overlapping_same_fps_range() -> None:
    calls: list[tuple[float, float, float]] = []
    extractor = _make_extractor(calls)
    provider = FrameProvider(max_frames=100)

    first = provider.get_frames(
        "D:/rec.mp4", start_sec=0.0, end_sec=10.0, fps=1.0, extractor=extractor,
    )
    assert calls == [(0.0, 10.0, 1.0)]
    assert first

    second = provider.get_frames(
        "D:/rec.mp4", start_sec=5.0, end_sec=15.0, fps=1.0, extractor=extractor,
    )
    # 只补抽缺失的 [10,15]，不重复解码 [5,10]。
    assert len(calls) == 2
    assert calls[-1] == (10.0, 15.0, 1.0)
    assert second
    assert min(float(ts) for ts, _ in second) >= 5.0
    assert max(float(ts) for ts, _ in second) <= 15.0 + 0.001


def test_frame_provider_keeps_different_fps_coverage_separate() -> None:
    calls: list[tuple[float, float, float]] = []
    extractor = _make_extractor(calls)
    provider = FrameProvider(max_frames=100)

    one_fps = provider.get_frames(
        "D:/rec.mp4", start_sec=0.0, end_sec=10.0, fps=1.0, extractor=extractor,
    )
    two_fps = provider.get_frames(
        "D:/rec.mp4", start_sec=0.0, end_sec=10.0, fps=2.0, extractor=extractor,
    )
    # 1fps 缓存不能覆盖 2fps 采样网格，需要再次解码同区间。
    assert len(calls) == 2
    assert calls[0] == (0.0, 10.0, 1.0)
    assert calls[1] == (0.0, 10.0, 2.0)
    assert len(one_fps) == 11
    assert len(two_fps) == 21


def test_frame_provider_partial_decode_is_not_marked_covered() -> None:
    """媒体尚未写满时（live 录制）短解不得登记为已覆盖，否则该区间永不再解码。

    2026-09-12 现场：审计请求 [447,466] 但文件只到 447，旧实现把整段记为已覆盖，
    续扫直接从 487 开始（448-486 成为永久空洞），351.312 回合的真出点 449.875
    因此永远看不见。修复后只登记真正解出的范围，缺口会在下次请求时补抽。
    """
    calls: list[tuple[float, float]] = []

    def _lagging_extractor(video_path, *, start_sec, end_sec, fps, **kwargs):
        calls.append((float(start_sec), float(end_sec)))
        # 模拟"文件只写到 5.0s"：无论请求多长，都只解出到 5.0s
        available = min(float(end_sec), 5.0)
        frames = []
        ts = float(start_sec)
        while ts <= available + 0.001:
            frames.append((round(ts, 3), f"img-{ts:.1f}"))
            ts += 1.0
        return frames

    provider = FrameProvider(max_frames=100)
    first = provider.get_frames(
        "D:/live.mp4", start_sec=0.0, end_sec=20.0, fps=1.0, extractor=_lagging_extractor,
    )
    assert [ts for ts, _ in first] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]

    # 文件长到 20s 后再问同一区间：必须补抽缺口（约 5→20），不能命中"已覆盖"返回空
    calls.clear()
    second = provider.get_frames(
        "D:/live.mp4", start_sec=0.0, end_sec=20.0, fps=1.0,
        extractor=_make_extractor(calls),
    )
    assert len(calls) == 1
    gap_start, gap_end = calls[0][0], calls[0][1]
    assert gap_start <= 5.0 + 0.01 and gap_end == 20.0
    assert max(float(ts) for ts, _ in second) == 20.0
    assert len(second) == 21


def test_frame_provider_extraction_failure_is_non_fatal() -> None:
    def _failing_extractor(video_path, *, start_sec, end_sec, fps, **kwargs):
        raise RuntimeError("boom")

    provider = FrameProvider(max_frames=10)
    frames = provider.get_frames(
        "D:/rec.mp4",
        start_sec=0.0,
        end_sec=10.0,
        fps=1.0,
        extractor=_failing_extractor,
    )
    assert frames == []


def test_frame_provider_does_not_swallow_cancellation() -> None:
    def _cancelled_extractor(video_path, *, start_sec, end_sec, fps, **kwargs):
        raise FFmpegCancelled("cancelled")

    provider = FrameProvider(max_frames=10)
    with pytest.raises(FFmpegCancelled):
        provider.get_frames(
            "D:/rec.mp4",
            start_sec=0.0,
            end_sec=10.0,
            fps=1.0,
            extractor=_cancelled_extractor,
        )


def test_frame_provider_prefetch_merges_overlapping_ranges() -> None:
    calls: list[tuple[float, float, float]] = []
    extractor = _make_extractor(calls)
    provider = FrameProvider(max_frames=100)

    groups = provider.prefetch_ranges(
        "D:/rec.mp4",
        [(0.0, 12.0), (10.0, 20.0), (20.5, 30.0)],
        fps=1.0,
        merge_gap_sec=1.0,
        extractor=extractor,
    )

    assert groups == 1
    assert calls == [(0.0, 30.0, 1.0)]
    cached = provider.get_frames(
        "D:/rec.mp4",
        start_sec=5.0,
        end_sec=25.0,
        fps=1.0,
        extractor=extractor,
    )
    assert calls == [(0.0, 30.0, 1.0)]
    assert cached


def test_frame_provider_eviction_invalidates_old_coverage_only() -> None:
    calls: list[tuple[float, float, float]] = []
    extractor = _make_extractor(calls)
    provider = FrameProvider(max_frames=5)

    provider.get_frames(
        "D:/rec.mp4", start_sec=0.0, end_sec=9.0, fps=1.0, extractor=extractor,
    )
    # Newest five samples remain cached.
    provider.get_frames(
        "D:/rec.mp4", start_sec=5.0, end_sec=9.0, fps=1.0, extractor=extractor,
    )
    assert len(calls) == 1
    # Evicted history is no longer claimed as covered and is decoded again.
    provider.get_frames(
        "D:/rec.mp4", start_sec=0.0, end_sec=2.0, fps=1.0, extractor=extractor,
    )
    assert len(calls) == 2
