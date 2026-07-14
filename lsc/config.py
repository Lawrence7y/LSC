"""LSC 配置模块。"""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)


@dataclass
class ExportProfile:
    """导出配置。

    编码器选择：
      - "libx264" / "libx265": 软件编码（CPU，兼容性最好）
      - "h264_nvenc" / "hevc_nvenc": NVIDIA 硬件编码
      - "h264_qsv" / "hevc_qsv": Intel QuickSync 硬件编码
      - "h264_amf" / "hevc_amf": AMD AMF 硬件编码
      - "copy": 直接流拷贝（不重编码，最快但切口不精确）

    质量模式：
      - rate_mode="crf": 使用 crf 值（18-28，越小质量越高）
      - rate_mode="bitrate": 使用 video_bitrate 限制码率
      - rate_mode="unrestricted": 不限制质量
    """
    crf: int = 23
    codec: str = "h264_nvenc"
    preset: str = "medium"
    audio_bitrate: str = "128k"
    vertical_crop: bool = False
    # 码率控制
    rate_mode: str = "crf"  # "crf" | "bitrate" | "unrestricted"
    video_bitrate: str = "8000k"  # 仅 rate_mode="bitrate" 时生效
    # 分辨率缩放（空字符串=不缩放）
    resolution: str = ""  # e.g. "1920x1080", "1080x1920", ""
    # 帧率（0=保持源帧率）
    fps: float = 0.0
    generate_thumbnail: bool = False

    def __post_init__(self):
        """验证参数范围。"""
        # CRF 范围验证 (0-51)
        if not 0 <= self.crf <= 51:
            _log.debug("CRF %d out of range [0,51], clamping", self.crf)
        self.crf = max(0, min(51, self.crf))

        # 帧率验证 (非负)
        if self.fps < 0:
            _log.debug("FPS %.1f negative, resetting to 0", self.fps)
            self.fps = 0.0

        # 分辨率格式验证（兼容 "1920x1080" 和 "1920:1080" 两种分隔符）
        if self.resolution:
            normalized = self.resolution.replace(":", "x")
            parts = normalized.split("x", 1)
            if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                _log.warning("Invalid resolution format: %s, clearing", self.resolution)
                self.resolution = ""
            else:
                w, h = int(parts[0]), int(parts[1])
                if w <= 0 or h <= 0 or w > 7680 or h > 4320:
                    _log.warning("Resolution out of range: %s, clearing", self.resolution)
                    self.resolution = ""
                else:
                    self.resolution = normalized
        _log.debug("ExportProfile created: codec=%s crf=%d rate_mode=%s", self.codec, self.crf, self.rate_mode)

    # ── 硬件编码预设 ──
    # 常用硬件编码器快速选择
    HARDWARE_ENCODERS = {
        "nvenc": "h264_nvenc",
        "qsv": "h264_qsv",
        "amf": "h264_amf",
    }

    @property
    def is_hardware(self) -> bool:
        """是否使用硬件编码器。"""
        return self.codec in (
            "h264_nvenc", "hevc_nvenc",
            "h264_qsv", "hevc_qsv",
            "h264_amf", "hevc_amf",
        )

    @property
    def is_copy(self) -> bool:
        """是否为流拷贝模式。"""
        return self.codec == "copy"

    def ffmpeg_video_args(self) -> list[str]:
        """构建 FFmpeg 视频编码参数列表。

        返回 ``-c:v`` 及其后续参数，调用方负责处理流拷贝和视频滤镜的
        互斥关系。
        """
        if self.is_copy:
            _log.debug("ffmpeg_video_args: copy mode")
            return ["-c:v", "copy"]

        args = ["-c:v", self.codec]

        # 码率控制
        if self.rate_mode == "crf":
            # NVENC 使用 -cq 而非 -crf，且必须配合 -rc vbr 才生效
            if self.is_hardware:
                args += ["-rc", "vbr", "-cq", str(self.crf)]
            else:
                args += ["-crf", str(self.crf)]
        elif self.rate_mode == "bitrate":
            args += ["-b:v", self.video_bitrate]
        else:
            _log.debug("ffmpeg_video_args: unrestricted quality mode")

        # preset（硬件编码器使用不同的 preset 名称）
        if self.preset:
            if self.is_hardware:
                # NVENC/QSV/AMF 的 preset 名称与 libx264 不同
                hw_preset = self._hardware_preset()
                if hw_preset:
                    args += ["-preset", hw_preset]
            else:
                args += ["-preset", self.preset]

        _log.debug("ffmpeg_video_args: %s", args)
        return args

    def _hardware_preset(self) -> str:
        """将 libx264 preset 映射到硬件编码器的 preset。"""
        # NVENC presets: fast, medium, slow, p1-p7
        if self.codec.endswith("_nvenc"):
            mapping = {
                "ultrafast": "p1", "superfast": "p2", "veryfast": "p3",
                "faster": "p4", "fast": "p4", "medium": "p4", "slow": "p5",
            }
            result = mapping.get(self.preset, "p4")
            _log.debug("NVENC preset mapping: %s -> %s", self.preset, result)
            return result
        # QSV presets: veryfast, faster, fast, medium, slow, slower, veryslow
        if self.codec.endswith("_qsv"):
            valid = ("veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")
            result = self.preset if self.preset in valid else "medium"
            if result != self.preset:
                _log.debug("QSV preset %r unknown, fallback to medium", self.preset)
            return result
        # AMF presets: speed, balanced, quality
        if self.codec.endswith("_amf"):
            mapping = {"ultrafast": "speed", "fast": "speed",
                       "medium": "balanced", "slow": "quality", "slower": "quality"}
            result = mapping.get(self.preset, "balanced")
            _log.debug("AMF preset mapping: %s -> %s", self.preset, result)
            return result
        _log.debug("Unknown hardware codec %s, using preset as-is", self.codec)
        return self.preset

    def ffmpeg_audio_args(self) -> list[str]:
        """构建 FFmpeg 音频编码参数列表。"""
        if self.is_copy:
            _log.debug("ffmpeg_audio_args: copy mode")
            return ["-c:a", "copy"]
        _log.debug("ffmpeg_audio_args: aac %s", self.audio_bitrate)
        return ["-c:a", "aac", "-b:a", self.audio_bitrate]

    def ffmpeg_filter_args(self, force_reencode: bool = False) -> list[str]:
        """构建视频滤镜参数（分辨率缩放、帧率、竖屏补边）。

        返回空列表表示无需滤镜。当 codec=copy 但 force_reencode=True 时
        调用方应切换到软件编码。

        ``vertical_crop=True``（抖音竖屏）保留完整原画面，等比缩放入
        1080x1920 画布后上下（或左右）补黑边，不再中心裁剪。
        """
        filters: list[str] = []
        # 竖屏补边已固定输出 1080x1920，勿再叠加 resolution scale
        if self.resolution and not self.vertical_crop:
            w, h = self.resolution.split("x", 1)
            filters.append(f"scale={w}:{h}")
        if self.fps > 0:
            filters.append(f"fps={self.fps}")
        if self.vertical_crop:
            # 等比放入竖屏画布 + 上下/左右黑边（非裁剪）
            filters.append(
                "scale=1080:1920:force_original_aspect_ratio=decrease,"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
            )
        if not filters:
            _log.debug("ffmpeg_filter_args: no filters")
            return []
        _log.debug("ffmpeg_filter_args: %s", filters)
        return ["-vf", ",".join(filters)]


@dataclass
class Profile:
    """编码配置 profile。"""
    export: ExportProfile = field(default_factory=ExportProfile)


@dataclass
class LscConfig:
    """LSC 全局配置。"""
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    output_path: str = ""
    output_dir: str = ""
    shared_ingest_enabled: bool = False
    shared_ingest_preview_queue_bytes: int = 2 * 1024 * 1024
    shared_ingest_preview_drop_policy: str = "drop_oldest"
    shared_ingest_preview_crf: int = 23
    shared_ingest_preview_preset: str = "veryfast"
    profile: Profile = field(default_factory=Profile)

    def __post_init__(self):
        if not self.ffmpeg_path:
            self.ffmpeg_path = _find_executable("ffmpeg")
        if not self.ffprobe_path:
            self.ffprobe_path = _find_executable("ffprobe")
        if not self.output_path:
            self.output_path = os.path.join(os.path.expanduser("~"), "LSC", "recordings")
        if not self.output_dir:
            self.output_dir = self.output_path
        _log.debug("LscConfig initialized: ffmpeg=%s ffprobe=%s output=%s",
                   self.ffmpeg_path or "(not found)", self.ffprobe_path or "(not found)", self.output_dir)


def _find_executable(name: str) -> str:
    """查找可执行文件。

    查找优先级:
      1. LSC_BUNDLED_FFMPEG_DIR 环境变量(Electron 打包模式注入)
      2. 系统 PATH(shutil.which)
    返回找到的绝对路径,找不到返回空字符串。
    """
    # 1. 打包内 FFmpeg 目录(Electron 通过环境变量注入)
    bundled_dir = os.environ.get("LSC_BUNDLED_FFMPEG_DIR", "")
    if bundled_dir:
        candidate = os.path.join(bundled_dir, f"{name}.exe" if os.name == "nt" else name)
        if os.path.isfile(candidate):
            _log.info("Found %s in bundled dir: %s", name, candidate)
            return candidate
        _log.debug("Bundled dir set but %s not found in %s", name, bundled_dir)
    # 2. PATH 查找
    path = shutil.which(name)
    if path:
        _log.info("Found %s in PATH: %s", name, path)
    else:
        _log.warning("%s not found in PATH or bundled dir", name)
    return path or ""


_config_instance: LscConfig | None = None


def _load_config_overrides() -> dict:
    path = os.environ.get("LSC_CONFIG_PATH", "")
    if not path:
        # 自动发现：项目根目录下的 lsc_config.json
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        auto_path = os.path.join(_root, "lsc_config.json")
        if os.path.isfile(auto_path):
            path = auto_path
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        _log.warning("Failed to load config file %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        _log.warning("Config file %s did not contain an object", path)
        return {}
    allowed = {
        "ffmpeg_path",
        "ffprobe_path",
        "output_path",
        "output_dir",
        "shared_ingest_enabled",
        "shared_ingest_preview_queue_bytes",
        "shared_ingest_preview_drop_policy",
        "shared_ingest_preview_crf",
        "shared_ingest_preview_preset",
    }
    return {key: data[key] for key in allowed if key in data}


def preferred_hw_video_codec() -> str:
    """返回当前机器优先的视频编码器：NVENC > libx264。

    用于「必须重编码」的回退路径（共享进样 copy+滤镜、导出 copy 精确裁切等），
    避免默认落到 CPU 软编。
    """
    try:
        from lsc.core.services.mse_streamer import _check_nvenc

        if _check_nvenc():
            return "h264_nvenc"
    except Exception as exc:
        _log.debug("preferred_hw_video_codec: nvenc probe failed: %s", exc)
    return "libx264"


def export_decode_hwaccel_args(codec: str | None = None) -> list[str]:
    """导出时的解码硬解参数（降低 CPU）。

    NVENC 只加速**编码**；抖音竖屏等路径仍有 crop/scale/fps，解码默认走 CPU
    时整机 CPU 仍会冲高。有 NVENC 时优先 ``cuda`` 硬解，否则 Windows 用 ``d3d11va``。
    流拷贝模式不需要硬解。
    """
    if codec == "copy":
        return []
    try:
        from lsc.core.services.mse_streamer import _check_nvenc

        if _check_nvenc():
            return ["-hwaccel", "cuda"]
    except Exception as exc:
        _log.debug("export_decode_hwaccel cuda probe failed: %s", exc)
    import platform

    if platform.system() == "Windows":
        return ["-hwaccel", "d3d11va"]
    return []


def load_config(force_reload: bool = False) -> LscConfig:
    """加载 LSC 配置。

    返回单例实例，避免多房间场景下反复创建 LscConfig。
    """
    global _config_instance
    if force_reload or _config_instance is None:
        _config_instance = LscConfig(**_load_config_overrides())
        _log.info("LSC config loaded (singleton created)")
    return _config_instance


def reload_config() -> LscConfig:
    """重新加载 LSC 配置。

    当 FFmpeg 路径或其他关键配置变化时调用，强制重新创建单例。
    """
    global _config_instance
    _config_instance = load_config(force_reload=True)
    _log.info("LSC config reloaded")
    return _config_instance


def reset_config() -> None:
    """重置配置单例。

    下次调用 load_config() 时会重新创建实例。
    """
    global _config_instance
    _config_instance = None
    _log.debug("LSC config singleton reset")


__all__ = ["LscConfig", "load_config", "reload_config", "reset_config", "Profile", "ExportProfile"]
