"""GPU FFmpeg helper unit tests."""
from __future__ import annotations


def test_build_cuda_vf_vertical_crop_uses_letterbox() -> None:
    from lsc.utils.gpu_ffmpeg import build_cuda_vf

    args = build_cuda_vf(vertical_crop=True, fps=30)
    assert args[0] == "-vf"
    chain = args[1]
    assert "hwdownload" in chain
    assert "fps=30" in chain
    assert "force_original_aspect_ratio=decrease" in chain
    assert "pad=1080:1920" in chain
    assert "hwupload_cuda" in chain
    assert "crop=" not in chain


def test_build_cuda_vf_resolution_only() -> None:
    from lsc.utils.gpu_ffmpeg import build_cuda_vf

    args = build_cuda_vf(resolution="1280x720")
    assert "scale_cuda=1280:720" in args[1]
    assert "hwdownload" not in args[1]


def test_build_cpu_vf_letterbox_when_vertical() -> None:
    from lsc.utils.gpu_ffmpeg import build_cpu_vf

    args = build_cpu_vf(vertical_crop=True, resolution="1080x1920", fps=30)
    assert args[1].count("scale=") == 1
    assert "fps=30" in args[1]
    assert "pad=1080:1920" in args[1]
    assert "crop=" not in args[1]


def test_cuvid_decoder_name() -> None:
    from lsc.utils.gpu_ffmpeg import cuvid_decoder_name

    assert cuvid_decoder_name("h264") == "h264_cuvid"
    assert cuvid_decoder_name("hevc") == "hevc_cuvid"
    assert cuvid_decoder_name("unknown") is None


def test_input_hwaccel_args_cuvid(monkeypatch) -> None:
    from lsc.utils import gpu_ffmpeg as gf

    monkeypatch.setattr(gf, "nvenc_available", lambda: True)
    args = gf.input_hwaccel_args(codec_name="h264", prefer_cuvid=True)
    assert args[:4] == ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    assert args[-2:] == ["-c:v", "h264_cuvid"]
