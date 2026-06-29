"""音频互相关对齐模块单元测试。"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from lsc.editor.audio_aligner import (
    AUDIO_DURATION,
    SAMPLE_RATE,
    _parabolic_interpolation,
    align_rooms,
    compute_offset,
    extract_audio_pcm,
)


def _noise_signal(duration: float, sample_rate: int = SAMPLE_RATE, seed: int = 42) -> np.ndarray:
    """生成确定性白噪声（适合互相关测试）。"""
    rng = np.random.RandomState(seed)
    n_samples = int(duration * sample_rate)
    return rng.randn(n_samples).astype(np.float32)


def _shifted_window(long_signal: np.ndarray, delay_sec: float,
                    window_duration: float = AUDIO_DURATION,
                    sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """从长信号中截取一个等长窗口，模拟延迟的音频。

    delay_sec = 0  → 窗口在末尾（最快的流，最新内容）。
    delay_sec > 0  → 窗口提前 delay_sec 秒开始（更慢的流，内容更旧）。
    """
    window_samples = int(window_duration * sample_rate)
    delay_samples = int(delay_sec * sample_rate)
    end = len(long_signal)
    start = end - window_samples - delay_samples
    if start < 0:
        start = 0
    window = np.zeros(window_samples, dtype=np.float32)
    available = min(window_samples, end - start)
    if available > 0:
        window[:available] = long_signal[start:start + available]
    return window


class TestComputeOffset:
    """互相关偏移量计算测试。"""

    def test_identical_audio_returns_zero_offset(self):
        """相同音频的偏移量应为 0。"""
        audio = _noise_signal(2.0)
        offset, score = compute_offset(audio, audio, SAMPLE_RATE)
        assert abs(offset) < 0.01
        assert score > 0.9

    def test_delayed_audio_returns_negative_offset(self):
        """延迟 0.5 秒的音频应返回 ≈-0.5 秒偏移（负值=比参考慢）。"""
        long_signal = _noise_signal(AUDIO_DURATION + 1.0, seed=42)
        fastest = _shifted_window(long_signal, 0.0, AUDIO_DURATION)
        slower = _shifted_window(long_signal, 0.5, AUDIO_DURATION)
        offset, score = compute_offset(fastest, slower, SAMPLE_RATE)
        assert abs(offset - (-0.5)) < 0.01
        assert score > 0.5

    def test_empty_audio_returns_zero(self):
        """空音频应返回 (0.0, 0.0)。"""
        empty = np.array([], dtype=np.float32)
        offset, score = compute_offset(empty, empty, SAMPLE_RATE)
        assert offset == 0.0
        assert score == 0.0

    def test_one_empty_audio_returns_zero(self):
        """一个空音频一个非空音频应返回 (0.0, 0.0)。"""
        audio = _noise_signal(1.0)
        empty = np.array([], dtype=np.float32)
        offset, score = compute_offset(audio, empty, SAMPLE_RATE)
        assert offset == 0.0
        assert score == 0.0

    def test_different_signals_low_score(self):
        """不同随机种子生成的噪声互相关分数应较低。"""
        a = _noise_signal(2.0, seed=42)
        b = _noise_signal(2.0, seed=99)
        _, score = compute_offset(a, b, SAMPLE_RATE)
        assert score < 0.3

    def test_sub_sample_delay_precision(self):
        """亚样本延迟（0.3s）应被高精度检测到。"""
        long_signal = _noise_signal(AUDIO_DURATION + 0.5, seed=42)
        ref = _shifted_window(long_signal, 0.0, AUDIO_DURATION)
        delayed = _shifted_window(long_signal, 0.3, AUDIO_DURATION)
        offset, score = compute_offset(ref, delayed, SAMPLE_RATE)
        assert abs(offset - (-0.3)) < 0.01
        assert score > 0.5

    def test_millisecond_precision(self):
        """0.123s 延迟应被检测到，误差 < 1ms。"""
        long_signal = _noise_signal(AUDIO_DURATION + 0.5, seed=77)
        ref = _shifted_window(long_signal, 0.0, AUDIO_DURATION)
        delayed = _shifted_window(long_signal, 0.123, AUDIO_DURATION)
        offset, score = compute_offset(ref, delayed, SAMPLE_RATE)
        assert abs(offset - (-0.123)) < 0.001
        assert score > 0.5

    def test_signal_normalization_robustness(self):
        """不同音量（缩放）的相同音频应返回相同偏移。"""
        long_signal = _noise_signal(AUDIO_DURATION + 1.0, seed=42)
        ref = _shifted_window(long_signal, 0.0, AUDIO_DURATION)
        delayed = _shifted_window(long_signal, 0.5, AUDIO_DURATION)
        delayed_loud = delayed * 3.0
        offset1, _ = compute_offset(ref, delayed, SAMPLE_RATE)
        offset2, _ = compute_offset(ref, delayed_loud, SAMPLE_RATE)
        assert abs(offset1 - offset2) < 0.01


class TestParabolicInterpolation:
    """抛物线插值精度测试。"""

    def test_integer_peak_unchanged(self):
        """平坦区域应返回原整数位置。"""
        corr = np.array([1.0, 2.0, 1.0], dtype=np.float32)
        assert _parabolic_interpolation(corr, 1) == pytest.approx(1.0)

    def test_skewed_peak_refines_toward_higher_side(self):
        """峰值两侧不对称时，应向更高一侧做亚采样偏移。"""
        # 右侧邻居更高 → refined_peak > 整数峰值（向右偏移）
        corr_right = np.array([0.8, 1.4, 1.0], dtype=np.float32)
        result = _parabolic_interpolation(corr_right, 1)
        assert result > 1.0
        assert result < 1.5
        # 左侧邻居更高 → refined_peak < 整数峰值（向左偏移）
        corr_left = np.array([1.0, 1.4, 0.8], dtype=np.float32)
        result = _parabolic_interpolation(corr_left, 1)
        assert result < 1.0
        assert result > 0.5

    def test_boundary_returns_peak(self):
        """首尾样本不插值，直接返回原位置。"""
        corr = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        assert _parabolic_interpolation(corr, 0) == 0.0
        assert _parabolic_interpolation(corr, 2) == 2.0


class TestExtractAudioPcm:
    """FFmpeg 音频提取测试（使用 mock 避免实际调用 FFmpeg）。"""

    @patch("subprocess.Popen")
    def test_returns_empty_array_on_ffmpeg_failure(self, mock_popen):
        """FFmpeg 失败时返回空数组。"""
        mock_proc = mock_popen.return_value
        mock_proc.stdout = None
        mock_proc.wait.side_effect = Exception("FFmpeg error")

        result = extract_audio_pcm("ffmpeg", "/fake/path.mp4", duration=5.0)
        assert result.size == 0

    @patch("subprocess.Popen")
    def test_returns_empty_array_on_empty_output(self, mock_popen):
        """FFmpeg 输出为空时返回空数组。"""
        mock_proc = mock_popen.return_value
        mock_proc.stdout.read.return_value = b""
        mock_proc.wait.return_value = None

        result = extract_audio_pcm("ffmpeg", "/fake/path.mp4", duration=5.0)
        assert result.size == 0


class TestAlignRooms:
    """align_rooms 集成测试（mock extract_audio_pcm）。"""

    def test_less_than_two_rooms_returns_failure(self):
        """不足 2 个房间应返回失败。"""
        result = align_rooms([], "ffmpeg")
        assert result.success is False
        assert "至少需要" in result.error

    def test_single_room_returns_failure(self):
        """1 个房间应返回失败。"""
        rooms = [{"room_id": "r1", "source": "/fake.mp4", "seek": 0.0, "is_recording": True}]
        result = align_rooms(rooms, "ffmpeg")
        assert result.success is False

    @patch("lsc.editor.audio_aligner.extract_audio_pcm")
    def test_two_rooms_normalizes_offsets(self, mock_extract):
        """2 个房间时，偏移量应正确归一化（最慢房间 offset=0）。"""
        long_signal = _noise_signal(AUDIO_DURATION + 2.0, seed=1)
        fastest = _shifted_window(long_signal, 0.0, AUDIO_DURATION)
        slower1 = _shifted_window(long_signal, 1.0, AUDIO_DURATION)

        mock_extract.side_effect = [fastest, slower1]

        rooms = [
            {"room_id": "r1", "source": "/a.mp4", "seek": 0.0, "is_recording": True},
            {"room_id": "r2", "source": "/b.mp4", "seek": 0.0, "is_recording": True},
        ]
        result = align_rooms(rooms, "ffmpeg")
        assert result.success is True
        assert result.reference_room_id == "r2"
        assert result.offsets["r1"] == pytest.approx(1.0, abs=0.1)
        assert result.offsets["r2"] == 0.0
        assert result.method == "recording"

    @patch("lsc.editor.audio_aligner.extract_audio_pcm")
    def test_three_rooms_finds_slowest_reference(self, mock_extract):
        """3 个房间时，最慢的房间应作为基准 (offset=0)。"""
        long_signal = _noise_signal(AUDIO_DURATION + 2.0, seed=10)
        fastest = _shifted_window(long_signal, 0.0, AUDIO_DURATION)
        slower05 = _shifted_window(long_signal, 0.5, AUDIO_DURATION)
        slower1 = _shifted_window(long_signal, 1.0, AUDIO_DURATION)

        mock_extract.side_effect = [fastest, slower05, slower1]

        rooms = [
            {"room_id": "r1", "source": "/a.mp4", "seek": 0.0, "is_recording": True},
            {"room_id": "r2", "source": "/b.mp4", "seek": 0.0, "is_recording": True},
            {"room_id": "r3", "source": "/c.mp4", "seek": 0.0, "is_recording": True},
        ]
        result = align_rooms(rooms, "ffmpeg")
        assert result.success is True
        assert result.reference_room_id == "r3"
        assert result.offsets["r3"] == 0.0
        assert result.offsets["r1"] == pytest.approx(1.0, abs=0.1)
        assert result.offsets["r2"] == pytest.approx(0.5, abs=0.1)

    @patch("lsc.editor.audio_aligner.extract_audio_pcm")
    def test_extraction_failure_skips_room(self, mock_extract):
        """某个房间提取失败时，仍使用剩余房间对齐。"""
        long_signal = _noise_signal(AUDIO_DURATION + 2.0, seed=20)
        audio_a = _shifted_window(long_signal, 0.0, AUDIO_DURATION)
        audio_c = _shifted_window(long_signal, 1.0, AUDIO_DURATION)

        mock_extract.side_effect = [audio_a, Exception("fail"), audio_c]

        rooms = [
            {"room_id": "r1", "source": "/a.mp4", "seek": 0.0, "is_recording": True},
            {"room_id": "r2", "source": "/b.mp4", "seek": 0.0, "is_recording": True},
            {"room_id": "r3", "source": "/c.mp4", "seek": 0.0, "is_recording": True},
        ]
        result = align_rooms(rooms, "ffmpeg")
        assert result.success is True
        assert result.reference_room_id == "r3"
        assert result.offsets["r1"] == pytest.approx(1.0, abs=0.1)
        assert result.offsets["r3"] == 0.0
        assert "r2" not in result.offsets

    @patch("lsc.editor.audio_aligner.extract_audio_pcm")
    def test_stream_method_when_not_all_recording(self, mock_extract):
        """非全部录制时，method 应为 'stream'。"""
        audio = _noise_signal(AUDIO_DURATION)
        mock_extract.return_value = audio

        rooms = [
            {"room_id": "r1", "source": "/a.mp4", "seek": 0.0, "is_recording": True},
            {"room_id": "r2", "source": "http://stream", "seek": 0.0, "is_recording": False},
        ]
        result = align_rooms(rooms, "ffmpeg")
        assert result.success is True
        assert result.method == "stream"


class TestPreviewAudioAlignment:
    """测试预览音频对齐（base64 PCM → 互相关）的核心逻辑。"""

    def test_base64_roundtrip_preserves_samples(self) -> None:
        """base64 编解码后 PCM 样本应完全一致。"""
        import base64

        original = _noise_signal(2.0, seed=7)
        raw_bytes = original.tobytes()
        b64 = base64.b64encode(raw_bytes).decode("ascii")
        decoded = np.frombuffer(base64.b64decode(b64), dtype=np.float32)

        assert np.allclose(original, decoded)

    def test_cross_correlation_with_base64_pcm(self) -> None:
        """模拟后端 handler：解码 base64 PCM → compute_offset。"""
        import base64

        ref = _noise_signal(3.0, seed=1)
        other = _noise_signal(3.0, seed=1)
        # 将 other 延迟 0.5 秒
        delay_samples = int(0.5 * SAMPLE_RATE)
        other_shifted = np.roll(other, delay_samples)
        other_shifted[:delay_samples] = 0

        ref_b64 = base64.b64encode(ref.tobytes()).decode("ascii")
        other_b64 = base64.b64encode(other_shifted.tobytes()).decode("ascii")

        ref_decoded = np.frombuffer(base64.b64decode(ref_b64), dtype=np.float32)
        other_decoded = np.frombuffer(base64.b64decode(other_b64), dtype=np.float32)

        offset, score = compute_offset(ref_decoded, other_decoded, SAMPLE_RATE)
        assert abs(offset - (-0.5)) < 0.05
        assert score > 0.1

    def test_handler_rejects_insufficient_rooms(self) -> None:
        """不足 2 路时应返回失败。"""
        rooms_data = [{"room_id": "r1", "sample_rate": 16000, "pcm_base64": "xxx"}]
        assert len(rooms_data) < 2

    def test_handler_rejects_short_audio(self) -> None:
        """少于 1 秒的音频应被跳过。"""
        import base64

        short = np.zeros(8000, dtype=np.float32)  # 0.5s @ 16kHz
        b64 = base64.b64encode(short.tobytes()).decode("ascii")
        assert len(np.frombuffer(base64.b64decode(b64), dtype=np.float32)) < SAMPLE_RATE
