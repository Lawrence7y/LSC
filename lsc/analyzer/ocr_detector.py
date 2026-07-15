"""OCR 击杀提示框检测器。
使用 rapidocr 识别文字变化，检测击杀事件与回合标记。
支持 Valorant 回合边界检测（Round X、Phase 文字）。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Callable
from typing import Any

from lsc.analyzer.ocr_accel import (
    ffmpeg_hwaccel_args,
    read_settings_ocr_accel,
    run_ffmpeg_with_hwaccel_fallback,
)
from lsc.utils.process_launcher import run_hidden

_log = logging.getLogger(__name__)
_KILL_FEED_CROP_RATIO = (0.75, 0.04, 0.23, 0.20)
_SAMPLE_INTERVAL = 0.5

_FRAME_DIFF_THRESHOLD = 5.0

# 回合标记检测的 HUD 裁剪区域 (x, y, w, h) - 检测分数/回合数变化区域
# 针对 Valorant: 回合数显示在屏幕正上方中央, 比分在两侧
_ROUND_MARKER_CROP_RATIO = (0.35, 0.01, 0.30, 0.06)

# 回合标记正则: 匹配 Round/Ronda, Phase, 比分格式 (3:2, 3-2)
_ROUND_MARKER_PATTERNS = [
    re.compile(r"round\s*\d+", re.I),
    re.compile(r"ronda\s*\d+", re.I),
    re.compile(r"phase", re.I),
    re.compile(r"\d+\s*[-:：]\s*\d+"),  # 比分格式 "3:2" "12-11"
]

# 买枪期关键词: 匹配 OCR 文字判断是否处于 buy_phase
_BUY_PHASE_PATTERNS = [
    re.compile(r"\bbuy\b", re.I),
    re.compile(r"\bequip\b", re.I),
    re.compile(r"\bprepar", re.I),     # preparation / preparing
    re.compile(r"购买", re.I),
    re.compile(r"装备", re.I),
]

# Kill Feed 语义关键词 - 击杀提示框文字必须包含其中之一
# 用于过滤 HUD 其他元素(计时器、ping、玩家名滚动)造成的文字变化误检
_KILL_FEED_PATTERNS = [
    re.compile(r"eliminated", re.I),
    re.compile(r"knocked", re.I),
    re.compile(r"killed", re.I),
    re.compile(r"headshot", re.I),
    re.compile(r"爆头"),
    re.compile(r"击杀"),
    re.compile(r"淘汰"),
    re.compile(r"倒地"),
]

# OCR 置信度下限: rapidocr 返回 (box, text, confidence), 低于此值视为不可靠
_OCR_CONFIDENCE_THRESHOLD = 0.6

_GAME_CONFIGS: dict[str, dict[str, Any]] = {
    "valorant": {
        "crop_ratio": (0.75, 0.04, 0.23, 0.20),
        "name": "Valorant",
        "round_marker_crop": (0.35, 0.01, 0.30, 0.06),
    },
    "cs2": {
        "crop_ratio": (0.75, 0.04, 0.23, 0.20),
        "name": "Counter-Strike 2",
        "round_marker_crop": (0.30, 0.01, 0.40, 0.06),
    },
    "apex": {
        "crop_ratio": (0.65, 0.03, 0.35, 0.30),
        "name": "Apex Legends",
        "round_marker_crop": None,  # Apex 没有明确回合系统
    },
}
_DEFAULT_GAME = "valorant"

# RapidOCR 单例：避免每次调用重新加载 ONNX 模型（每次构造需 ~1-2s 加载检测+识别模型）
_ocr_instance: Any = None
_ocr_lock = threading.Lock()  # #8: prevent concurrent multi-room OCR init


def invalidate_ocr() -> None:
    """销毁 OCR 单例，供设置变更后重建。"""
    global _ocr_instance
    _ocr_instance = None


def _get_ocr() -> Any:
    """获取 RapidOCR 单例（懒加载，线程安全）。"""
    global _ocr_instance
    with _ocr_lock:
        if _ocr_instance is None:
            from lsc.analyzer.ocr_accel import create_ocr, read_settings_ocr_accel
            _ocr_instance = create_ocr(read_settings_ocr_accel())
    return _ocr_instance


def detect_kill_events(
    video_path: str,
    ffmpeg_path: str = "ffmpeg",
    duration: float = 0.0,
    progress_callback: Callable[[str, float, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    game: str = _DEFAULT_GAME,
    time_range: tuple[float, float] | None = None,
) -> list[dict[str, Any]]:
    """检测击杀提示框变化，返回击杀事件与回合标记列表。

    支持多游戏配置，通过 ``game`` 参数选择 Kill Feed 裁剪区域。
    除击杀事件外，还检测 Valorant/CS2 回合边界标记（Round X / Phase）。

    Args:
        time_range: 可选 ``(start_sec, end_sec)``，仅分析该时间段（增量分析）。

    Args:
        video_path: 视频文件路径
        ffmpeg_path: FFmpeg 路径
        duration: 视频时长（秒）
        progress_callback: 进度回调
        cancel_check: 取消检查回调
        game: 游戏名称，支持 "valorant"（默认）、"cs2"、"apex"

    返回:
        事件列表，每个事件包含:
        - timestamp: 时间戳 (秒)
        - text: 识别文字
        - score: 置信度 (0.3-1.0)
        - type: "kill" (击杀) | "round_marker" (回合标记)
        - text: 识别文字
        - score: 置信度 (0.3-1.0)
    """
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401 - 检查模块可用性
    except ImportError:
        _log.warning("rapidocr-onnxruntime 未安装，跳过 OCR 检测")
        return []

    width, height = _get_video_resolution(video_path, ffmpeg_path)
    if width == 0 or height == 0:
        return []

    game_config = _GAME_CONFIGS.get(game, _GAME_CONFIGS[_DEFAULT_GAME])
    crop_ratio = game_config["crop_ratio"]
    x = int(width * crop_ratio[0])
    y = int(height * crop_ratio[1])
    w = int(width * crop_ratio[2])
    h = int(height * crop_ratio[3])

    ocr = _get_ocr()
    tmp_dir = tempfile.mkdtemp(prefix="lsc_ocr_")

    try:
        output_pattern = os.path.join(tmp_dir, "frame_%05d.jpg")
        cmd = [
            ffmpeg_path, "-y", "-loglevel", "error",
        ]
        if time_range is not None:
            cmd += ["-ss", f"{time_range[0]:.3f}", "-t", f"{time_range[1] - time_range[0]:.3f}"]
        cmd += [
            "-i", video_path,
            "-vf", f"fps=1/{_SAMPLE_INTERVAL},crop={w}:{h}:{x}:{y},showinfo",
            "-q:v", "3",
            output_pattern,
        ]
        hw = ffmpeg_hwaccel_args(read_settings_ocr_accel())
        result = run_ffmpeg_with_hwaccel_fallback(cmd, hwaccel_args=hw, timeout=300)
        frame_ts_pattern = re.compile(r"pts_time:(\d+\.?\d*)")
        precise_timestamps = [float(m.group(1)) for m in frame_ts_pattern.finditer(result.stderr)]
        deduped_ts: list[float] = []
        for ts in precise_timestamps:
            if not deduped_ts or ts > deduped_ts[-1] + 0.001:
                deduped_ts.append(ts)
        precise_timestamps = deduped_ts
        seg_offset = time_range[0] if time_range else 0.0
        if seg_offset > 0:
            precise_timestamps = [ts + seg_offset for ts in precise_timestamps]

        events: list[dict[str, Any]] = []
        prev_text = ""
        frame_files = sorted(f for f in os.listdir(tmp_dir) if f.endswith(".jpg"))
        total = len(frame_files)

        try:
            import cv2
            _has_cv2 = True
        except ImportError:
            _has_cv2 = False
        import numpy as np
        from PIL import Image

        prev_frame_gray = None
        ocr_count = 0

        for i, fname in enumerate(frame_files):
            if cancel_check and cancel_check():
                break
            fpath = os.path.join(tmp_dir, fname)

            # 帧读取容错：增长文件可能产生损坏/不完整的 JPG
            try:
                if _has_cv2:
                    frame_gray = cv2.imread(fpath, cv2.IMREAD_GRAYSCALE)
                else:
                    frame_gray = np.array(Image.open(fpath).convert("L"), dtype=np.float32)
            except Exception:
                _log.debug("帧读取失败，跳过: %s", fname)
                prev_frame_gray = None
                continue

            if frame_gray is None:
                prev_frame_gray = None
                continue

            if prev_frame_gray is not None:
                if _has_cv2:
                    diff = cv2.absdiff(frame_gray, prev_frame_gray)
                    mean_diff = float(np.mean(diff))
                else:
                    diff = np.abs(frame_gray - prev_frame_gray)
                    mean_diff = float(np.mean(diff))
                if mean_diff < _FRAME_DIFF_THRESHOLD:
                    prev_frame_gray = frame_gray
                    if progress_callback and duration > 0:
                        pct = min(90.0, (i + 1) / max(total, 1) * 90.0)
                        progress_callback("ocr", pct, f"OCR 预筛中... {i+1}/{total}")
                    continue

            prev_frame_gray = frame_gray
            ocr_count += 1

            # OCR 容错：单帧 OCR 失败不崩溃整个检测
            try:
                result_ocr, _ = ocr(fpath)
            except Exception:
                _log.debug("单帧 OCR 失败，跳过: %s", fname)
                continue

            # 提取高置信度文字 (rapidocr 返回 [(box, text, confidence), ...])
            # 过滤低置信度文字, 减少 HUD 抖动造成的误检
            if result_ocr:
                confident_lines = [
                    line for line in result_ocr
                    if len(line) >= 3 and line[2] >= _OCR_CONFIDENCE_THRESHOLD
                ]
                current_text = " ".join(line[1] for line in confident_lines)
            else:
                current_text = ""

            if current_text and current_text != prev_text:
                # 语义评分: 含击杀关键词给高分, 无关键词但有文字变化给低分
                has_kill_keyword = any(p.search(current_text) for p in _KILL_FEED_PATTERNS)
                if has_kill_keyword:
                    kill_count = sum(
                        max(0, len(p.split(current_text)) - 1)
                        for p in _KILL_FEED_PATTERNS
                    )
                    kill_count = max(1, kill_count)
                    score = min(1.0, kill_count * 0.2)
                else:
                    text_len = len(current_text)
                    has_separator = ">" in current_text or "  " in current_text
                    word_count = len(current_text.split())
                    if text_len < 3 or text_len > 60 or not has_separator or word_count < 2:
                        prev_text = current_text
                        continue
                    score = 0.3

                if i < len(precise_timestamps):
                    timestamp = precise_timestamps[i]
                else:
                    timestamp = i * _SAMPLE_INTERVAL
                events.append({
                    "timestamp": round(timestamp, 3),
                    "text": current_text[:100],
                    "score": max(0.3, score),
                    "type": "kill",
                })
                prev_text = current_text

            if progress_callback and duration > 0:
                pct = min(90.0, (i + 1) / max(total, 1) * 90.0)
                progress_callback("ocr", pct, f"OCR 检测中... {i+1}/{total}")

        # 第二遍: 检测回合标记 (从视频中提取回合边界)
        round_marker_crop = game_config.get("round_marker_crop")
        if round_marker_crop is not None:
            events = _detect_round_markers(
                events, video_path, ffmpeg_path, width, height,
                round_marker_crop, _SAMPLE_INTERVAL, game_config["name"],
            )

        if progress_callback:
            kill_count = sum(1 for e in events if e.get("type") == "kill")
            marker_count = sum(1 for e in events if e.get("type") == "round_marker")
            progress_callback("ocr", 100.0, f"OCR 检测完成：{kill_count} 击杀 + {marker_count} 回合标记")

        _log.info(
            "OCR 检测: %d 击杀 + %d 回合标记 (path=%s, 预筛 %d/%d 帧 OCR, %.0f%% 过滤)",
            sum(1 for e in events if e.get("type") == "kill"),
            sum(1 for e in events if e.get("type") == "round_marker"),
            os.path.basename(video_path),
            ocr_count, total,
            (1 - ocr_count / max(total, 1)) * 100,
        )
        return events
    except Exception as exc:
        _log.warning("OCR 检测失败: %s", exc)
        return []
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)





def _detect_round_markers(
    events: list[dict[str, Any]],
    video_path: str,
    ffmpeg_path: str,
    width: int,
    height: int,
    marker_crop_ratio: tuple[float, float, float, float],
    sample_interval: float,
    game_name: str,
) -> list[dict[str, Any]]:
    """检测回合边界标记（Round X / Phase 文字变化）。

    通过在 HUD 中央区域采样 OCR，识别回合数变化。
    回合变化点 = 新的回合开始边界。
    """
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401 - 检查模块可用性
    except ImportError:
        return events

    mx = int(width * marker_crop_ratio[0])
    my = int(height * marker_crop_ratio[1])
    mw = int(width * marker_crop_ratio[2])
    mh = int(height * marker_crop_ratio[3])

    tmp_dir = tempfile.mkdtemp(prefix="lsc_round_")
    try:
        output_pattern = os.path.join(tmp_dir, "hud_%05d.jpg")
        cmd = [
            ffmpeg_path, "-y", "-loglevel", "error",
            "-i", video_path,
            "-vf", f"fps=1/{sample_interval},crop={mw}:{mh}:{mx}:{my},showinfo",
            "-q:v", "3",
            output_pattern,
        ]
        hw = ffmpeg_hwaccel_args(read_settings_ocr_accel())
        result = run_ffmpeg_with_hwaccel_fallback(cmd, hwaccel_args=hw, timeout=300)

        frame_ts_pattern = re.compile(r"pts_time:(\d+\.?\d*)")
        precise_timestamps = [float(m.group(1)) for m in frame_ts_pattern.finditer(result.stderr)]
        deduped_ts: list[float] = []
        for ts in precise_timestamps:
            if not deduped_ts or ts > deduped_ts[-1] + 0.001:
                deduped_ts.append(ts)
        precise_timestamps = deduped_ts

        ocr = _get_ocr()
        frame_files = sorted(f for f in os.listdir(tmp_dir) if f.endswith(".jpg"))
        prev_hud_text = ""
        round_markers: list[dict[str, Any]] = []

        for i, fname in enumerate(frame_files):
            fpath = os.path.join(tmp_dir, fname)
            result_ocr, _ = ocr(fpath)
            # 只保留置信度足够的 OCR 结果
            confident_lines = [
                line for line in result_ocr
                if len(line) >= 3 and line[2] >= _OCR_CONFIDENCE_THRESHOLD
            ] if result_ocr else []
            current_text = " ".join(line[1] for line in confident_lines) if confident_lines else ""

            # 检测是否包含回合标记
            has_marker = any(p.search(current_text) for p in _ROUND_MARKER_PATTERNS)

            if has_marker and current_text != prev_hud_text:
                if i < len(precise_timestamps):
                    timestamp = precise_timestamps[i]
                else:
                    timestamp = i * sample_interval
                marker_event: dict[str, Any] = {
                    "timestamp": round(timestamp, 3),
                    "text": f"{game_name} round marker: {current_text[:50]}",
                    "score": 0.5,
                    "type": "round_marker",
                }
                # 检查当前 HUD 帧文字是否包含 buy_phase 关键词
                is_buy_phase = any(p.search(current_text) for p in _BUY_PHASE_PATTERNS)
                if is_buy_phase:
                    marker_event["phase"] = "buy"
                round_markers.append(marker_event)
                _log.debug("回合标记 @ %.3fs: %s (phase=%s)",
                           timestamp, current_text[:40],
                           marker_event.get("phase", "-"))

            prev_hud_text = current_text

        events.extend(round_markers)
        _log.info("回合标记检测: 发现 %d 个回合边界", len(round_markers))
        return events
    except Exception as exc:
        _log.warning("回合标记检测失败: %s", exc)
        return events
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)





def _get_video_resolution(video_path: str, ffmpeg_path: str) -> tuple[int, int]:
    """获取视频分辨率。"""
    try:
        cmd = [ffmpeg_path, "-i", video_path, "-hide_banner"]
        result = run_hidden(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=10,
        )
        for line in result.stderr.split("\n"):
            if "Video:" in line:
                m = re.search(r"(\d{3,5})x(\d{3,5})", line)
                if m:
                    return int(m.group(1)), int(m.group(2))
    except Exception as exc:
        _log.debug("操作异常（已忽略）: %s", exc)
    return 1920, 1080
