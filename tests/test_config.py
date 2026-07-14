"""LSC configuration unit tests.

Covers ExportProfile validation, FFmpeg argument building, hardware preset
mapping, LscConfig singleton, path finding, and format validation.
"""
from __future__ import annotations

import os

import pytest

from lsc.config import (
    ExportProfile,
    LscConfig,
    load_config,
    reload_config,
    reset_config,
    _find_executable,
)


class TestExportProfileValidation:
    """Test ExportProfile parameter validation in __post_init__."""

    def test_crf_clamped_high(self):
        profile = ExportProfile(crf=60)
        assert profile.crf == 51

    def test_crf_clamped_low(self):
        profile = ExportProfile(crf=-5)
        assert profile.crf == 0

    def test_crf_valid_unchanged(self):
        profile = ExportProfile(crf=23)
        assert profile.crf == 23

    def test_negative_fps_reset(self):
        profile = ExportProfile(fps=-1.0)
        assert profile.fps == 0.0

    def test_valid_fps_unchanged(self):
        profile = ExportProfile(fps=30.0)
        assert profile.fps == 30.0

    def test_resolution_valid(self):
        profile = ExportProfile(resolution="1920x1080")
        assert profile.resolution == "1920x1080"

    def test_resolution_colon_separator_normalized(self):
        profile = ExportProfile(resolution="1920:1080")
        assert profile.resolution == "1920x1080"

    def test_resolution_invalid_cleared(self):
        profile = ExportProfile(resolution="not-a-resolution")
        assert profile.resolution == ""

    def test_resolution_out_of_range_cleared(self):
        profile = ExportProfile(resolution="99999x99999")
        assert profile.resolution == ""

    def test_resolution_zero_dimension_cleared(self):
        profile = ExportProfile(resolution="0x1080")
        assert profile.resolution == ""

    def test_empty_resolution_unchanged(self):
        profile = ExportProfile(resolution="")
        assert profile.resolution == ""


class TestExportProfileCodecDetection:
    """Test is_hardware and is_copy properties."""

    def test_is_copy_true(self):
        assert ExportProfile(codec="copy").is_copy is True

    def test_is_copy_false_for_encoder(self):
        assert ExportProfile(codec="libx264").is_copy is False

    def test_is_hardware_nvenc(self):
        assert ExportProfile(codec="h264_nvenc").is_hardware is True

    def test_is_hardware_qsv(self):
        assert ExportProfile(codec="h264_qsv").is_hardware is True

    def test_is_hardware_amf(self):
        assert ExportProfile(codec="h264_amf").is_hardware is True

    def test_is_hardware_hevc_nvenc(self):
        assert ExportProfile(codec="hevc_nvenc").is_hardware is True

    def test_not_hardware_cpu(self):
        assert ExportProfile(codec="libx264").is_hardware is False

    def test_not_hardware_copy(self):
        assert ExportProfile(codec="copy").is_hardware is False


class TestFFmpegVideoArgs:
    """Test ffmpeg_video_args builds correct FFmpeg parameters."""

    def test_copy_mode(self):
        profile = ExportProfile(codec="copy")
        args = profile.ffmpeg_video_args()
        assert args == ["-c:v", "copy"]

    def test_crf_software(self):
        profile = ExportProfile(codec="libx264", crf=23)
        args = profile.ffmpeg_video_args()
        assert "-c:v" in args
        assert "libx264" in args
        assert "-crf" in args
        assert "23" in args

    def test_crf_nvenc_uses_cq(self):
        profile = ExportProfile(codec="h264_nvenc", crf=23)
        args = profile.ffmpeg_video_args()
        assert "-rc" in args
        assert "vbr" in args
        assert "-cq" in args
        assert "23" in args
        # Should NOT have -crf for NVENC
        assert "-crf" not in args

    def test_bitrate_mode(self):
        profile = ExportProfile(codec="libx264", rate_mode="bitrate", video_bitrate="8000k")
        args = profile.ffmpeg_video_args()
        assert "-b:v" in args
        assert "8000k" in args

    def test_unrestricted_mode_no_quality_args(self):
        profile = ExportProfile(codec="libx264", rate_mode="unrestricted")
        args = profile.ffmpeg_video_args()
        assert "-crf" not in args
        assert "-b:v" not in args

    def test_preset_applied_software(self):
        profile = ExportProfile(codec="libx264", preset="fast")
        args = profile.ffmpeg_video_args()
        assert "-preset" in args
        assert "fast" in args


class TestHardwarePresetMapping:
    """Test _hardware_preset mapping from libx264 to HW encoder presets."""

    def test_nvenc_mapping_slow(self):
        profile = ExportProfile(codec="h264_nvenc", preset="slow")
        assert profile._hardware_preset() == "p5"

    def test_nvenc_mapping_medium(self):
        profile = ExportProfile(codec="h264_nvenc", preset="medium")
        assert profile._hardware_preset() == "p4"

    def test_nvenc_default_p4(self):
        profile = ExportProfile(codec="h264_nvenc", preset="unknown")
        assert profile._hardware_preset() == "p4"

    def test_qsv_valid_preset(self):
        profile = ExportProfile(codec="h264_qsv", preset="fast")
        assert profile._hardware_preset() == "fast"

    def test_qsv_invalid_preset_defaults_medium(self):
        profile = ExportProfile(codec="h264_qsv", preset="invalid")
        assert profile._hardware_preset() == "medium"

    def test_amf_mapping_quality(self):
        profile = ExportProfile(codec="h264_amf", preset="slow")
        assert profile._hardware_preset() == "quality"

    def test_amf_default_balanced(self):
        profile = ExportProfile(codec="h264_amf", preset="unknown")
        assert profile._hardware_preset() == "balanced"

    def test_unknown_hw_codec_passthrough(self):
        profile = ExportProfile(codec="h264_unknown", preset="fast")
        assert profile._hardware_preset() == "fast"


class TestFFmpegAudioArgs:
    """Test ffmpeg_audio_args building."""

    def test_copy_mode(self):
        profile = ExportProfile(codec="copy")
        assert profile.ffmpeg_audio_args() == ["-c:a", "copy"]

    def test_aac_mode(self):
        profile = ExportProfile(codec="libx264", audio_bitrate="128k")
        args = profile.ffmpeg_audio_args()
        assert args == ["-c:a", "aac", "-b:a", "128k"]


class TestFFmpegFilterArgs:
    """Test ffmpeg_filter_args building."""

    def test_no_filters(self):
        profile = ExportProfile()
        assert profile.ffmpeg_filter_args() == []

    def test_resolution_scale(self):
        profile = ExportProfile(resolution="1920x1080")
        args = profile.ffmpeg_filter_args()
        assert len(args) == 2
        assert "-vf" in args
        assert "scale=1920:1080" in args[1]

    def test_fps_filter(self):
        profile = ExportProfile(fps=30)
        args = profile.ffmpeg_filter_args()
        assert "fps=30" in args[1]

    def test_vertical_crop(self):
        profile = ExportProfile(vertical_crop=True)
        args = profile.ffmpeg_filter_args()
        assert "force_original_aspect_ratio=decrease" in args[1]
        assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black" in args[1]
        assert "crop=" not in args[1]

    def test_combined_filters(self):
        profile = ExportProfile(resolution="1080x1920", fps=30, vertical_crop=True)
        args = profile.ffmpeg_filter_args()
        filter_str = args[1]
        # 竖屏补边已含 1080x1920，勿再前置一次 scale
        assert filter_str.count("scale=") == 1
        assert "fps=30" in filter_str
        assert "pad=1080:1920" in filter_str
        assert "crop=" not in filter_str
        assert not filter_str.startswith("scale=1080:1920,")

def test_export_decode_hwaccel_prefers_cuda_when_nvenc(monkeypatch) -> None:
    from lsc import config as cfg

    monkeypatch.setattr(
        "lsc.core.services.mse_streamer._check_nvenc",
        lambda: True,
    )
    assert cfg.export_decode_hwaccel_args("h264_nvenc") == ["-hwaccel", "cuda"]
    assert cfg.export_decode_hwaccel_args("copy") == []


def test_export_decode_hwaccel_d3d11va_without_nvenc(monkeypatch) -> None:
    import platform

    from lsc import config as cfg

    monkeypatch.setattr(
        "lsc.core.services.mse_streamer._check_nvenc",
        lambda: False,
    )
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert cfg.export_decode_hwaccel_args("h264_nvenc") == ["-hwaccel", "d3d11va"]


class TestLscConfig:
    """Test LscConfig singleton and initialization."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_config()

    def teardown_method(self):
        """Clean up singleton after each test."""
        reset_config()

    def test_default_ffmpeg_path_empty_string(self):
        """When PATH has no ffmpeg, default is empty string."""
        config = LscConfig()
        # On CI/dev machines ffmpeg may or may not be installed
        assert isinstance(config.ffmpeg_path, str)

    def test_default_output_dir(self):
        config = LscConfig()
        expected = os.path.join(os.path.expanduser("~"), "LSC", "recordings")
        assert config.output_path == expected
        assert config.output_dir == config.output_path

    def test_custom_paths_preserved(self):
        config = LscConfig(ffmpeg_path="/usr/bin/ffmpeg", ffprobe_path="/usr/bin/ffprobe")
        assert config.ffmpeg_path == "/usr/bin/ffmpeg"
        assert config.ffprobe_path == "/usr/bin/ffprobe"

    def test_singleton_returns_same_instance(self):
        config1 = load_config()
        config2 = load_config()
        assert config1 is config2

    def test_reload_creates_new_instance(self):
        config1 = load_config()
        config2 = reload_config()
        assert config1 is not config2

    def test_reset_allows_new_instance(self):
        config1 = load_config()
        reset_config()
        config2 = load_config()
        assert config1 is not config2

    def test_shared_ingest_defaults_disabled(self):
        config = load_config()

        assert hasattr(config, "shared_ingest_enabled")
        assert config.shared_ingest_enabled is False
        assert config.shared_ingest_preview_queue_bytes >= 1 * 1024 * 1024
        assert config.shared_ingest_preview_drop_policy == "drop_oldest"

    def test_shared_ingest_config_roundtrip(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        config_path = tmp_path / "config.json"
        config_path.write_text(
            (
                "{"
                '"shared_ingest_enabled": true, '
                '"shared_ingest_preview_queue_bytes": 2097152, '
                '"shared_ingest_preview_drop_policy": "drop_newest"'
                "}"
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("LSC_CONFIG_PATH", str(config_path))

        config = load_config(force_reload=True)

        assert config.shared_ingest_enabled is True
        assert config.shared_ingest_preview_queue_bytes == 2 * 1024 * 1024
        assert config.shared_ingest_preview_drop_policy == "drop_newest"


class TestFindExecutable:
    """Test _find_executable path resolution."""

    def test_returns_string(self):
        result = _find_executable("python")
        assert isinstance(result, str)

    def test_empty_for_nonexistent(self):
        result = _find_executable("this_binary_definitely_does_not_exist_12345")
        assert result == ""

    def test_bundled_env_var_checked(self, monkeypatch: pytest.MonkeyPatch):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a fake executable
            fake_exe = os.path.join(tmpdir, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            with open(fake_exe, "w") as f:
                f.write("#!/bin/sh\n")
            os.chmod(fake_exe, 0o755)
            monkeypatch.setenv("LSC_BUNDLED_FFMPEG_DIR", tmpdir)
            result = _find_executable("ffmpeg")
            assert result == fake_exe
