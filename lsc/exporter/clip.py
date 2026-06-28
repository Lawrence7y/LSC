"""
LSC Clip Exporter
=================
Exports highlight clips from video using FFmpeg.

Features:
  - Precise time-based cutting with re-encoding at cut points
  - Thumbnail generation at highlight midpoint
  - Vertical crop (9:16) for short video platforms
  - Batch export with progress tracking
  - Configurable encoding profile (software/hardware encoders)
"""

import json
import os
import re
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Thread
from uuid import uuid4

from lsc import get_logger
from lsc.config import ExportProfile, LscConfig
from lsc.utils.process_launcher import prepare_launch

_log = get_logger(__name__)

_DEFAULT_CRF = 23
_DEFAULT_AUDIO_BITRATE = "128k"
_DEFAULT_VSCALE = "scale=1080:1920"


def parse_ffmpeg_progress_line(line: str, state: dict[str, int]) -> None:
    """Parse a single FFmpeg progress line and update state dict.

    FFmpeg outputs lines like 'out_time_ms=15000000' when using -progress pipe:1.
    """
    if "=" not in line:
        return
    key, value = line.strip().split("=", 1)
    if key == "out_time_ms" and value.isdigit():
        state["out_time_ms"] = int(value)


@dataclass
class ExportResult:
    """Result of a clip export operation."""
    success: bool
    output_path: str
    clip_index: int
    title: str
    duration: float = 0.0
    file_size_mb: float = 0.0
    thumbnail_path: str = ""
    error: str = ""


def _cleanup_tmp(path: str) -> None:
    """安全删除临时文件，忽略不存在的情况。"""
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


class ClipExporter:
    """FFmpeg-based clip exporter with thumbnail and vertical crop support."""

    # Shared pool for thumbnail generation so exports don't block each other.
    _thumbnail_executor: ThreadPoolExecutor | None = None
    _thumbnail_lock = threading.Lock()

    @classmethod
    def _get_thumbnail_executor(cls) -> ThreadPoolExecutor:
        # 双重检查锁定，避免并发创建多个线程池
        if cls._thumbnail_executor is None:
            with cls._thumbnail_lock:
                if cls._thumbnail_executor is None:
                    cls._thumbnail_executor = ThreadPoolExecutor(
                        max_workers=4, thread_name_prefix="lsc-thumb"
                    )
        return cls._thumbnail_executor

    def __init__(self, config: LscConfig):
        self.config = config
        self.export_cfg = config.profile.export
        self.ffmpeg = config.ffmpeg_path
        self.ffprobe = config.ffprobe_path

    def export_clip(self, video_path: str, start_sec: float, end_sec: float,
                    output_dir: str, *,
                    title: str = "",
                    clip_index: int = 0,
                    vertical_crop: bool = False,
                    codec: str = "",
                    profile: ExportProfile | None = None,
                    progress_callback=None,
                    on_process=None) -> ExportResult:
        """Export a single clip from the video.

        Parameters
        ----------
        profile : ExportProfile | None
            完整的编码配置。若提供则覆盖 codec/vertical_crop 参数；
            若为 None 则使用 config 中的默认 profile。
        progress_callback : callable | None
            进度回调函数 ``callback(percent: float, elapsed: float, total: float)``。
            当提供时使用 ``-progress pipe:1`` 实时读取 FFmpeg 进度。
        on_process : callable | None
            FFmpeg 进程启动回调 ``callback(proc: subprocess.Popen)``。
            当使用 Popen 模式（提供 progress_callback）时在进程创建后触发，
            供调用方追踪并取消该进程。
        """
        if not os.path.isfile(video_path):
            return ExportResult(False, "", clip_index, title or f"clip_{clip_index}",
                                error=f"Video not found: {video_path}")

        os.makedirs(output_dir, exist_ok=True)

        duration = end_sec - start_sec
        if duration < 1:
            return ExportResult(False, "", clip_index, title or f"clip_{clip_index}",
                                error=f"Clip too short: {duration:.1f}s")

        # Build output filename with path traversal protection
        raw_title = title or f"highlight_{clip_index}"
        # Strip Windows-illegal characters
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', raw_title)
        # Prevent path traversal: reject '..' components and leading slashes
        safe_title = safe_title.replace('..', '__').strip('. ')
        if not safe_title:
            safe_title = f"highlight_{clip_index}"
        output_path = os.path.join(output_dir, f"{safe_title}.mp4")
        # Final safety: ensure output stays within output_dir
        real_out = os.path.realpath(output_path)
        real_dir = os.path.realpath(output_dir)
        if not real_out.startswith(real_dir + os.sep):
            _log.error("Path traversal detected: title=%r resolved outside output_dir", title)
            return ExportResult(False, "", clip_index, safe_title,
                                error="Invalid clip title (path traversal)")

        # Avoid overwriting existing files by appending an incremental suffix
        base_output_path = output_path
        suffix = 1
        while os.path.exists(output_path):
            output_path = f"{base_output_path[:-4]}_{suffix}.mp4"
            suffix += 1

        # 确定使用的编码 profile
        if profile is None:
            # 合并旧的 codec/vertical_crop 参数到默认 profile
            profile = self.export_cfg
            if codec:
                profile = ExportProfile(
                    crf=profile.crf, codec=codec, preset=profile.preset,
                    audio_bitrate=profile.audio_bitrate,
                    vertical_crop=vertical_crop or profile.vertical_crop,
                    rate_mode=profile.rate_mode, video_bitrate=profile.video_bitrate,
                    resolution=profile.resolution, fps=profile.fps,
                )
            elif vertical_crop:
                profile = ExportProfile(
                    crf=profile.crf, codec=profile.codec, preset=profile.preset,
                    audio_bitrate=profile.audio_bitrate,
                    vertical_crop=True,
                    rate_mode=profile.rate_mode, video_bitrate=profile.video_bitrate,
                    resolution=profile.resolution, fps=profile.fps,
                )

        # 视频滤镜需要重编码；copy 模式下若有滤镜则回退到 libx264。
        # 另外，copy 模式下从非 0 秒开始切片会导致切口落在关键帧上而不精确，
        # 因此只要 start_sec > 0 也自动回退到软件编码以保证切口准确。
        filter_args = profile.ffmpeg_filter_args()
        effective_profile = profile
        needs_reencode = bool(profile.is_copy and filter_args)
        if profile.is_copy and start_sec > 0:
            needs_reencode = True
            _log.info(
                "copy mode with non-zero start (%.3fs) falls back to libx264 for precise cut",
                start_sec,
            )
        if needs_reencode:
            effective_profile = ExportProfile(
                crf=profile.crf, codec="libx264", preset="medium",
                audio_bitrate=profile.audio_bitrate, vertical_crop=profile.vertical_crop,
                rate_mode=profile.rate_mode, video_bitrate=profile.video_bitrate,
                resolution=profile.resolution, fps=profile.fps,
            )
            filter_args = effective_profile.ffmpeg_filter_args()

        # FFmpeg command
        cmd = [self.ffmpeg, "-y", "-loglevel", "warning"]

        # Seek to start (fast seek + accurate)
        cmd += ["-ss", f"{start_sec:.3f}", "-i", video_path]
        cmd += ["-t", f"{duration:.3f}"]

        # 视频编码参数
        cmd += effective_profile.ffmpeg_video_args()
        # 音频编码参数
        cmd += effective_profile.ffmpeg_audio_args()
        # 视频滤镜
        cmd += filter_args

        cmd += ["-movflags", "+faststart"]

        # 进度回调：使用 -progress pipe:1 让 FFmpeg 输出进度到 stdout
        has_callback = progress_callback is not None
        if has_callback:
            cmd += ["-progress", "pipe:1", "-nostats"]

        cmd += [output_path]

        # 原子写入：先写临时文件，成功后 rename，失败时清理
        # tmp 文件名包含唯一标识，防止并发导出相同标题片段时碰撞
        # 保持 .mp4 扩展名以便 FFmpeg 识别格式，uuid 作为文件名的一部分
        base, ext = os.path.splitext(output_path)
        tmp_output_path = f"{base}.{uuid4().hex[:8]}_tmp{ext}"
        cmd[-1] = tmp_output_path  # 替换最终输出路径为临时文件

        try:
            # 准备进程启动环境（避免 Windows 控制台弹窗 + avcodec DLL 冲突）
            env, creation_flags, cwd = prepare_launch(self.ffmpeg)
            popen_kwargs = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "env": env,
                "cwd": cwd,
            }
            if creation_flags:
                popen_kwargs["creationflags"] = creation_flags
            if has_callback:
                # 使用 Popen 逐行读取进度
                proc = subprocess.Popen(cmd, **popen_kwargs)
                # 通知调用方进程已启动，便于追踪并取消该 FFmpeg 进程
                if on_process is not None:
                    try:
                        on_process(proc)
                    except Exception:
                        pass
                state: dict[str, int] = {}
                last_reported_percent = -1.0
                last_reported_time = 0.0
                stderr_tail: deque[str] = deque(maxlen=20)
                export_timed_out = False

                def _stderr_reader() -> None:
                    try:
                        for line in proc.stderr:
                            stderr_tail.append(line.rstrip())
                    except Exception:
                        pass

                def _watchdog() -> None:
                    """Kill FFmpeg if it runs longer than 5 minutes."""
                    nonlocal export_timed_out
                    try:
                        proc.wait(timeout=300)
                    except subprocess.TimeoutExpired:
                        export_timed_out = True
                        try:
                            proc.kill()
                            proc.wait(timeout=5)
                        except Exception as exc:
                            # Kill 失败，记录进程信息便于手动清理
                            import logging
                            logging.getLogger(__name__).warning(
                                "Failed to kill FFmpeg export process (PID=%s): %s",
                                proc.pid, exc
                            )

                stderr_thread = Thread(target=_stderr_reader, daemon=True)
                stderr_thread.start()
                watchdog_thread = Thread(target=_watchdog, daemon=True)
                watchdog_thread.start()

                try:
                    assert proc.stdout is not None
                    for line in proc.stdout:
                        line = line.strip()
                        if not line:
                            continue
                        parse_ffmpeg_progress_line(line, state)
                        # Throttle progress callbacks: FFmpeg emits out_time_ms
                        # every frame, but the UI only needs ~5 updates/sec or
                        # when the percentage changes by at least 1.
                        if "out_time_ms" in state:
                            elapsed_sec = state["out_time_ms"] / 1_000_000.0
                            percent = min(100.0, (elapsed_sec / duration) * 100.0) if duration > 0 else 0.0
                            now = time.time()
                            if percent - last_reported_percent >= 1.0 or now - last_reported_time >= 0.2:
                                last_reported_percent = percent
                                last_reported_time = now
                                try:
                                    progress_callback(percent, elapsed_sec, duration)
                                except Exception:
                                    pass
                    # stdout 已关闭，等待进程退出（不会阻塞太久）
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    export_timed_out = True

                stderr_thread.join(timeout=2)
                watchdog_thread.join(timeout=2)

                if export_timed_out:
                    _cleanup_tmp(tmp_output_path)
                    return ExportResult(False, output_path, clip_index, safe_title,
                                        error="Export timed out")
                if proc.returncode != 0:
                    _cleanup_tmp(tmp_output_path)
                    error_tail = "\n".join(stderr_tail)
                    return ExportResult(False, output_path, clip_index, safe_title,
                                        error=error_tail[-500:] or "Export failed")
            else:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=300,
                    encoding="utf-8", errors="replace",
                    env=env, cwd=cwd,
                    **({"creationflags": creation_flags} if creation_flags else {}),
                )
                if result.returncode != 0:
                    _cleanup_tmp(tmp_output_path)
                    return ExportResult(False, output_path, clip_index, safe_title,
                                        error=result.stderr[-500:])
        except subprocess.TimeoutExpired:
            _cleanup_tmp(tmp_output_path)
            return ExportResult(False, output_path, clip_index, safe_title,
                                error="Export timed out")
        except Exception as e:
            _cleanup_tmp(tmp_output_path)
            return ExportResult(False, output_path, clip_index, safe_title,
                                error=str(e))

        # 原子写入：临时文件 rename 到最终路径
        try:
            os.replace(tmp_output_path, output_path)
        except OSError as exc:
            _cleanup_tmp(tmp_output_path)
            return ExportResult(False, output_path, clip_index, safe_title,
                                error=f"Failed to finalize output: {exc}")

        # Verify output
        if not os.path.isfile(output_path):
            return ExportResult(False, output_path, clip_index, safe_title,
                                error="Output file not created")

        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)

        # Generate thumbnail in a background thread so the caller can continue
        # (e.g. notify on_done and update UI) without waiting for the snapshot.
        thumbnail_path = ""
        thumb_future = None
        try:
            midpoint = (start_sec + end_sec) / 2
            thumb_future = self._get_thumbnail_executor().submit(
                self._generate_thumbnail, video_path, midpoint, output_dir, safe_title
            )
        except Exception as exc:
            _log.warning("Failed to submit thumbnail job for %s: %s", output_path, exc)

        # Get actual duration
        actual_duration = self._get_duration(output_path)

        # Wait a short while for the thumbnail; if it isn't ready yet we return
        # without it rather than blocking the export pipeline.
        if thumb_future is not None:
            try:
                thumbnail_path = thumb_future.result(timeout=10.0)
            except Exception as exc:
                _log.warning("Thumbnail generation failed/timeout for %s: %s", output_path, exc)

        return ExportResult(
            success=True,
            output_path=output_path,
            clip_index=clip_index,
            title=safe_title,
            duration=actual_duration or duration,
            file_size_mb=round(file_size_mb, 2),
            thumbnail_path=thumbnail_path,
        )

    def export_all(self, video_path: str, highlights: list, output_dir: str, *,
                   vertical_crop: bool = False):
        """
        Export all highlights as individual clips.

        Yields each :class:`ExportResult` as soon as it is produced so
        callers can process or display results incrementally without
        holding the full list in memory.
        """
        os.makedirs(output_dir, exist_ok=True)

        total = len(highlights)
        for i, hl in enumerate(highlights):
            start = hl.get("start_sec", hl.get("start", 0))
            end = hl.get("end_sec", hl.get("end", 0))
            score = hl.get("score", 0)
            desc = hl.get("description", f"highlight_{i+1}")
            round_num = hl.get("round_number", 0)

            # Build title
            title_parts = []
            if round_num:
                title_parts.append(f"R{round_num}")
            title_parts.append(f"score_{score:.2f}")
            title = f"{'_'.join(title_parts)}_{desc}" if title_parts else desc

            _log.info("[%d/%d] Exporting %.1fs-%.1fs (score=%.2f)...",
                      i + 1, total, start, end, score)

            result = self.export_clip(
                video_path, start, end, output_dir,
                title=title, clip_index=i+1,
                vertical_crop=vertical_crop,
            )

            if result.success:
                _log.info("    OK: %s (%.1fMB)", result.output_path, result.file_size_mb)
            else:
                _log.error("    FAIL: %s", result.error)

            yield result

    @staticmethod
    def save_export_manifest(video_path: str, output_dir: str,
                             results: list[ExportResult]) -> str:
        """Persist a manifest JSON describing the exported clips."""
        manifest = {
            "source": video_path,
            "total_clips": len(results),
            "successful": sum(1 for r in results if r.success),
            "failed": sum(1 for r in results if not r.success),
            "clips": [
                {
                    "index": r.clip_index,
                    "title": r.title,
                    "output": r.output_path,
                    "duration": r.duration,
                    "size_mb": r.file_size_mb,
                    "thumbnail": r.thumbnail_path,
                    "success": r.success,
                }
                for r in results
            ]
        }

        manifest_path = os.path.join(output_dir, "export_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        return manifest_path

    def _generate_thumbnail(self, video_path: str, time_sec: float,
                            output_dir: str, name: str) -> str:
        """Generate a thumbnail at the specified time."""
        thumb_path = os.path.join(output_dir, f"{name}_thumb.jpg")

        cmd = [
            self.ffmpeg, "-y", "-loglevel", "quiet",
            "-ss", f"{time_sec:.3f}", "-i", video_path,
            "-vframes", "1",
            "-q:v", "3",
            thumb_path
        ]

        env, creation_flags, cwd = prepare_launch(self.ffmpeg)
        run_kwargs = {"capture_output": True, "timeout": 30, "env": env, "cwd": cwd}
        if creation_flags:
            run_kwargs["creationflags"] = creation_flags
        result = subprocess.run(cmd, **run_kwargs)
        if result.returncode == 0 and os.path.isfile(thumb_path):
            return thumb_path
        return ""

    def _get_duration(self, filepath: str) -> float:
        from lsc.utils.helpers import probe_duration
        return probe_duration(filepath, self.ffprobe)
