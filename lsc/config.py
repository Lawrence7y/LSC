"""LSC 配置模块。"""
from __future__ import annotations

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
    codec: str = "libx264"
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

    def __post_init__(self):
        """验证参数范围。"""
        # CRF 范围验证 (0-51)
        self.crf = max(0, min(51, self.crf))

        # 帧率验证 (非负)
        if self.fps < 0:
            self.fps = 0.0

        # 分辨率格式验证
        if self.resolution:
            parts = self.resolution.split("x", 1)
            if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                _log.warning("Invalid resolution format: %s, clearing", self.resolution)
                self.resolution = ""
            else:
                w, h = int(parts[0]), int(parts[1])
                if w <= 0 or h <= 0 or w > 7680 or h > 4320:
                    _log.warning("Resolution out of range: %s, clearing", self.resolution)
                    self.resolution = ""

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
        # unrestricted: 不添加质量参数

        # preset（硬件编码器使用不同的 preset 名称）
        if self.preset:
            if self.is_hardware:
                # NVENC/QSV/AMF 的 preset 名称与 libx264 不同
                hw_preset = self._hardware_preset()
                if hw_preset:
                    args += ["-preset", hw_preset]
            else:
                args += ["-preset", self.preset]

        return args

    def _hardware_preset(self) -> str:
        """将 libx264 preset 映射到硬件编码器的 preset。"""
        # NVENC presets: fast, medium, slow, p1-p7
        if self.codec.endswith("_nvenc"):
            mapping = {
                "ultrafast": "p1", "superfast": "p2", "veryfast": "p3",
                "faster": "p4", "fast": "p5", "medium": "p6", "slow": "p7",
            }
            return mapping.get(self.preset, "p6")
        # QSV presets: veryfast, faster, fast, medium, slow, slower, veryslow
        if self.codec.endswith("_qsv"):
            return self.preset if self.preset in (
                "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"
            ) else "medium"
        # AMF presets: speed, balanced, quality
        if self.codec.endswith("_amf"):
            mapping = {"ultrafast": "speed", "fast": "speed",
                       "medium": "balanced", "slow": "quality", "slower": "quality"}
            return mapping.get(self.preset, "balanced")
        return self.preset

    def ffmpeg_audio_args(self) -> list[str]:
        """构建 FFmpeg 音频编码参数列表。"""
        if self.is_copy:
            return ["-c:a", "copy"]
        return ["-c:a", "aac", "-b:a", self.audio_bitrate]

    def ffmpeg_filter_args(self, force_reencode: bool = False) -> list[str]:
        """构建视频滤镜参数（分辨率缩放、帧率、竖屏裁剪）。

        返回空列表表示无需滤镜。当 codec=copy 但 force_reencode=True 时
        调用方应切换到软件编码。
        """
        filters: list[str] = []
        if self.resolution:
            w, h = self.resolution.split("x", 1)
            filters.append(f"scale={w}:{h}")
        if self.fps > 0:
            filters.append(f"fps={self.fps}")
        if self.vertical_crop:
            filters.append("crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920")
        if not filters:
            return []
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


def _find_executable(name: str) -> str:
    """在 PATH 中查找可执行文件。"""
    path = shutil.which(name)
    return path or ""


_config_instance: LscConfig | None = None


def load_config() -> LscConfig:
    """加载 LSC 配置。

    返回单例实例，避免多房间场景下反复创建 LscConfig。
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = LscConfig()
    return _config_instance


def reload_config() -> LscConfig:
    """重新加载 LSC 配置。

    当 FFmpeg 路径或其他关键配置变化时调用，强制重新创建单例。
    """
    global _config_instance
    _config_instance = LscConfig()
    return _config_instance


def reset_config() -> None:
    """重置配置单例。

    下次调用 load_config() 时会重新创建实例。
    """
    global _config_instance
    _config_instance = None


__all__ = ["LscConfig", "load_config", "reload_config", "reset_config", "Profile", "ExportProfile"]
