"""录制文件修复工具（P2-1: 录制文件修复工具）。

提供录制文件验证和修复功能，处理 moov atom 不完整导致的文件不可播放问题。
"""

from __future__ import annotations

import logging
import os
import subprocess

from lsc.recorder.capture import validate_recording

_log = logging.getLogger(__name__)


def repair_recording(input_path: str, output_path: str | None = None) -> str | None:
    """修复录制文件（重新封装 moov atom）。

    使用 FFmpeg 重新封装，不重新编码，将 moov atom 移到文件开头。

    Args:
        input_path: 输入文件路径
        output_path: 输出文件路径，为 None 时在输入路径后加 .repaired.mp4

    Returns:
        修复后的文件路径，失败时返回 None
    """
    if not os.path.isfile(input_path):
        _log.warning("修复失败：文件不存在 %s", input_path)
        return None

    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}.repaired{ext}"

    from lsc.config import load_config
    cfg = load_config()
    ffmpeg_path = cfg.ffmpeg_path or "ffmpeg"

    cmd = [
        ffmpeg_path, "-y",
        "-loglevel", "warning",
        "-i", input_path,
        "-c", "copy",  # 不重新编码
        "-movflags", "+faststart",  # 将 moov 移到开头
        output_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode == 0 and os.path.isfile(output_path):
            _log.info("修复成功: %s -> %s", input_path, output_path)
            return output_path
        else:
            _log.warning("修复失败: returncode=%d, stderr=%s",
                         result.returncode, result.stderr.decode("utf-8", errors="replace")[:500])
            # 清理失败的输出文件
            if os.path.isfile(output_path):
                os.unlink(output_path)
            return None
    except subprocess.TimeoutExpired:
        _log.warning("修复超时: %s", input_path)
        return None
    except Exception as exc:
        _log.error("修复异常: %s, %s", input_path, exc)
        return None


def validate_and_repair(path: str) -> tuple[bool, str]:
    """验证文件，损坏时自动修复。

    Args:
        path: 文件路径

    Returns:
        (是否有效, 文件路径) — 修复成功时返回修复后的路径
    """
    is_valid, error = validate_recording(path)
    if is_valid:
        return True, path

    _log.info("文件损坏，尝试修复: %s, error=%s", path, error)
    repaired = repair_recording(path)
    if repaired:
        return True, repaired

    return False, path
