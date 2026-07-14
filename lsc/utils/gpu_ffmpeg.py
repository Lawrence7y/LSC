"""FFmpeg GPU 辅助：CUVID 硬解、scale_cuda 滤镜、导出/预览共用。

本机实测：仅 ``-hwaccel cuda`` 在带 CPU 滤镜时仍可能走 ``h264 (native)`` 软解；
配合 ``-c:v h264_cuvid`` + ``scale_cuda`` 才能真正把解码/缩放留在 GPU。
"""
from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from lsc.utils.process_launcher import run_hidden

_log = logging.getLogger(__name__)

_CUVID_BY_CODEC = {
    "h264": "h264_cuvid",
    "hevc": "hevc_cuvid",
    "h265": "hevc_cuvid",
    "av1": "av1_cuvid",
    "mpeg2video": "mpeg2_cuvid",
    "mpeg1video": "mpeg1_cuvid",
    "vp8": "vp8_cuvid",
    "vp9": "vp9_cuvid",
    "mjpeg": "mjpeg_cuvid",
}

_nvenc_ok: bool | None = None
_scale_cuda_ok: bool | None = None


def nvenc_available() -> bool:
    global _nvenc_ok
    if _nvenc_ok is None:
        try:
            from lsc.core.services.mse_streamer import _check_nvenc

            _nvenc_ok = bool(_check_nvenc())
        except Exception as exc:
            _log.debug("nvenc probe failed: %s", exc)
            _nvenc_ok = False
    return _nvenc_ok


def scale_cuda_available(ffmpeg_path: str = "ffmpeg") -> bool:
    """检测 scale_cuda 滤镜是否可用（缓存进程级结果）。"""
    global _scale_cuda_ok
    if _scale_cuda_ok is not None:
        return _scale_cuda_ok
    try:
        result = run_hidden(
            [ffmpeg_path, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=8,
        )
        _scale_cuda_ok = "scale_cuda" in (result.stdout or "")
    except Exception as exc:
        _log.debug("scale_cuda probe failed: %s", exc)
        _scale_cuda_ok = False
    return _scale_cuda_ok


def probe_video_stream(
    video_path: str,
    ffprobe_path: str = "ffprobe",
) -> dict[str, Any]:
    """探测视频流 width/height/fps/codec_name。失败返回空 dict。"""
    if not video_path:
        return {}
    try:
        result = run_hidden(
            [
                ffprobe_path,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate,codec_name",
                "-of", "json",
                video_path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=10,
        )
        if result.returncode != 0:
            return {}
        streams = json.loads(result.stdout or "{}").get("streams") or []
        if not streams:
            return {}
        s = streams[0]
        out: dict[str, Any] = {"codec_name": (s.get("codec_name") or "").lower()}
        if s.get("width") and s.get("height"):
            out["width"] = int(s["width"])
            out["height"] = int(s["height"])
        rate = s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1"
        if isinstance(rate, str) and "/" in rate:
            num, den = rate.split("/", 1)
            den_f = float(den) if float(den) else 1.0
            out["fps"] = float(num) / den_f
        return out
    except Exception as exc:
        _log.debug("probe_video_stream failed: %s", exc)
        return {}


def cuvid_decoder_name(codec_name: str | None) -> str | None:
    if not codec_name:
        return None
    return _CUVID_BY_CODEC.get(codec_name.lower())


def input_hwaccel_args(
    *,
    codec_name: str | None = None,
    prefer_cuvid: bool = True,
    output_format_cuda: bool = True,
) -> list[str]:
    """输入侧硬解参数（插在 ``-i`` 之前）。

    优先 CUVID 解码器；无匹配时回退 ``-hwaccel cuda``。
    """
    if not nvenc_available():
        import platform
        if platform.system() == "Windows":
            return ["-hwaccel", "d3d11va"]
        return []

    args = ["-hwaccel", "cuda"]
    if output_format_cuda:
        args += ["-hwaccel_output_format", "cuda"]
    if prefer_cuvid:
        cuvid = cuvid_decoder_name(codec_name)
        if cuvid:
            # -c:v 在 -i 之前指定输入解码器
            args += ["-c:v", cuvid]
    return args


# 抖音竖屏：原画等比装入 1080x1920，上下/左右补黑边（不裁剪内容）
VERTICAL_LETTERBOX_VF = (
    "scale=1080:1920:force_original_aspect_ratio=decrease,"
    "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
)


def build_cuda_vf(
    *,
    vertical_crop: bool = False,
    resolution: str = "",
    fps: float = 0.0,
    force_original_aspect_ratio: str = "",
) -> list[str]:
    """构建尽量留在 GPU 的 ``-vf`` 链。

    竖屏补边（pad）无稳定 CUDA 版表达式时：硬解 → hwdownload → scale/pad/fps
    → hwupload_cuda → NVENC。缩放目标比旧「裁剪后拉满」更小，CPU 更轻。
    """
    filters: list[str] = []
    need_download = False

    if vertical_crop:
        need_download = True
        if fps and fps > 0:
            filters.append(f"fps={fps:g}")
        filters.append(VERTICAL_LETTERBOX_VF)
        filters.append("hwupload_cuda")
    else:
        if fps and fps > 0:
            need_download = True
            filters.append(f"fps={fps:g}")
            filters.append("hwupload_cuda")
        if resolution:
            w, h = resolution.replace("x", ":").split(":", 1)
            scale = f"scale_cuda={w}:{h}"
            if force_original_aspect_ratio:
                scale += f":force_original_aspect_ratio={force_original_aspect_ratio}"
            filters.append(scale)
        elif need_download and not filters:
            pass

    if not filters:
        return []

    if need_download:
        filters = ["hwdownload", "format=nv12", *filters]
    return ["-vf", ",".join(filters)]


def build_cpu_vf(
    *,
    vertical_crop: bool = False,
    resolution: str = "",
    fps: float = 0.0,
    force_original_aspect_ratio: str = "",
) -> list[str]:
    """CPU 滤镜回退（与 ExportProfile.ffmpeg_filter_args 行为对齐）。"""
    parts: list[str] = []
    if resolution and not vertical_crop:
        w, h = resolution.replace(":", "x").split("x", 1)
        scale = f"scale={w}:{h}"
        if force_original_aspect_ratio:
            scale += f":force_original_aspect_ratio={force_original_aspect_ratio}"
        parts.append(scale)
    if fps and fps > 0:
        parts.append(f"fps={fps:g}")
    if vertical_crop:
        parts.append(VERTICAL_LETTERBOX_VF)
    if not parts:
        return []
    return ["-vf", ",".join(parts)]


def prefer_gpu_filters(ffmpeg_path: str = "ffmpeg") -> bool:
    return nvenc_available() and scale_cuda_available(ffmpeg_path)
