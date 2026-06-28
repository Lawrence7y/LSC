"""LSC CLI 分析命令 — 基于 FFmpeg 场景变化检测的高光片段识别。"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass

from lsc import get_logger

_log = get_logger(__name__)

# 场景变化检测阈值（0.0-1.0，越小越敏感）
_DEFAULT_SCENE_THRESHOLD = 0.3
# 最小片段时长（秒），过短的片段会被过滤
_MIN_HIGHLIGHT_DURATION = 5.0
# 片段前后扩展的缓冲时间（秒）
_HIGHLIGHT_BUFFER = 2.0


@dataclass
class HighlightSegment:
    """高光片段数据。"""
    start: float
    end: float
    score: float = 0.0
    label: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "duration": round(self.duration, 2),
            "score": round(self.score, 3),
            "label": self.label,
        }


def _get_video_duration(video_path: str, ffprobe: str) -> float:
    """使用 FFprobe 获取视频时长。"""
    try:
        cmd = [
            ffprobe, "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = (result.stdout or "").strip()
        if output:
            return float(output)
    except Exception as exc:
        _log.warning("获取视频时长失败: %s", exc)
    return 0.0


def _detect_scene_changes(video_path: str, ffmpeg: str,
                          threshold: float = _DEFAULT_SCENE_THRESHOLD) -> list[float]:
    """使用 FFmpeg 检测场景变化时间点。

    Returns:
        场景变化的时间点列表（秒）。
    """
    try:
        cmd = [
            ffmpeg, "-i", video_path,
            "-vf", f"select='gt(scene,{threshold})',showinfo",
            "-vsync", "vfr",
            "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        # 从 stderr 中解析 showinfo 输出的时间戳
        timestamps = []
        for line in result.stderr.split("\n"):
            if "showinfo" in line and "pts_time:" in line:
                try:
                    # 格式: [Parsed_showinfo_1 ...] n:   0 pts:    0 pts_time:0 ...
                    pts_start = line.index("pts_time:") + len("pts_time:")
                    pts_end = line.index(" ", pts_start)
                    timestamp = float(line[pts_start:pts_end])
                    timestamps.append(timestamp)
                except (ValueError, IndexError):
                    continue

        return sorted(timestamps)
    except Exception as exc:
        _log.warning("场景变化检测失败: %s", exc)
        return []


def _merge_nearby_timestamps(timestamps: list[float],
                             min_gap: float = _MIN_HIGHLIGHT_DURATION) -> list[tuple[float, float]]:
    """将相近的时间戳合并为连续的时间段。

    Args:
        timestamps: 场景变化时间点列表。
        min_gap: 最小间隔（秒），小于该间隔的时间点会被合并。

    Returns:
        合并后的时间段列表 (start, end)。
    """
    if not timestamps:
        return []

    segments = []
    start = timestamps[0]
    end = timestamps[0]

    for ts in timestamps[1:]:
        if ts - end < min_gap:
            end = ts
        else:
            segments.append((start, end))
            start = ts
            end = ts

    segments.append((start, end))
    return segments


def _create_highlights(segments: list[tuple[float, float]],
                       video_duration: float,
                       buffer: float = _HIGHLIGHT_BUFFER,
                       min_duration: float = _MIN_HIGHLIGHT_DURATION) -> list[HighlightSegment]:
    """从时间段创建高光片段，添加缓冲区并过滤过短的片段。"""
    highlights = []

    for start, end in segments:
        # 添加缓冲区
        hl_start = max(0.0, start - buffer)
        hl_end = min(video_duration, end + buffer)

        # 确保片段时长足够
        if hl_end - hl_start < min_duration:
            # 扩展到最小时长
            center = (hl_start + hl_end) / 2
            hl_start = max(0.0, center - min_duration / 2)
            hl_end = min(video_duration, center + min_duration / 2)

        if hl_end - hl_start >= min_duration:
            # 计算分数（基于场景变化密度）
            changes_in_range = sum(1 for s, e in segments
                                   if s < hl_end and e > hl_start)
            score = min(1.0, changes_in_range / 5.0)

            highlights.append(HighlightSegment(
                start=hl_start,
                end=hl_end,
                score=score,
                label=f"高光片段 {len(highlights) + 1}",
            ))

    return highlights


def cmd_analyze(args) -> dict:
    """分析视频高光片段。

    使用 FFmpeg 场景变化检测来识别视频中的高光时刻。
    支持的参数:
        - video: 视频文件路径
        - output: 输出 JSON 文件路径（可选）
        - profile: 分析配置（目前未使用）
    """
    video = getattr(args, "video", "")
    output = getattr(args, "output", "")
    profile = getattr(args, "profile", "generic")

    if not video or not os.path.isfile(video):
        result = {
            "video": video,
            "profile": profile,
            "highlights": [],
            "status": "error - 视频文件不存在",
            "error": f"视频文件不存在: {video}",
        }
        if output:
            os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
            with open(output, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        return result

    # 获取 FFmpeg 路径
    from lsc.config import load_config
    cfg = load_config()
    ffmpeg = cfg.ffmpeg_path
    ffprobe = cfg.ffprobe_path

    if not ffmpeg or not os.path.isfile(ffmpeg):
        result = {
            "video": video,
            "profile": profile,
            "highlights": [],
            "status": "error - FFmpeg 未安装或路径无效",
            "error": "FFmpeg 未安装或路径无效，请在设置中配置 FFmpeg 路径",
        }
        if output:
            os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
            with open(output, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        return result

    _log.info("开始分析视频: %s", video)

    # 获取视频时长
    video_duration = _get_video_duration(video, ffprobe)
    if video_duration <= 0:
        result = {
            "video": video,
            "profile": profile,
            "highlights": [],
            "status": "error - 无法获取视频时长",
            "error": "无法获取视频时长",
        }
        if output:
            os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
            with open(output, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        return result

    _log.info("视频时长: %.1f 秒", video_duration)

    # 检测场景变化
    _log.info("正在检测场景变化...")
    scene_threshold = _DEFAULT_SCENE_THRESHOLD
    timestamps = _detect_scene_changes(video, ffmpeg, scene_threshold)
    _log.info("检测到 %d 个场景变化点", len(timestamps))

    if not timestamps:
        result = {
            "video": video,
            "profile": profile,
            "video_duration": round(video_duration, 2),
            "highlights": [],
            "scene_changes": [],
            "status": "ok - 未检测到明显的场景变化",
        }
        if output:
            os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
            with open(output, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        return result

    # 合并相近的时间戳
    segments = _merge_nearby_timestamps(timestamps)
    _log.info("合并为 %d 个时间段", len(segments))

    # 创建高光片段
    highlights = _create_highlights(segments, video_duration)
    _log.info("生成 %d 个高光片段", len(highlights))

    result = {
        "video": video,
        "profile": profile,
        "video_duration": round(video_duration, 2),
        "scene_threshold": scene_threshold,
        "scene_changes": [round(ts, 2) for ts in timestamps],
        "highlights": [hl.to_dict() for hl in highlights],
        "status": f"ok - 检测到 {len(highlights)} 个高光片段",
    }

    if output:
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        _log.info("分析结果已保存到: %s", output)

    return result


__all__ = ["cmd_analyze", "HighlightSegment"]
