"""
LSC Clip Exporter
=================

ClipExporter 是直播切片系统（LSC）的导出核心，负责将识别出的高光片段
从原始视频中精确截取并编码为独立的 MP4 文件。

整体架构
--------
- 单片段导出入口：:meth:`ClipExporter.export_clip`，构建 FFmpeg 命令并执行，
  使用 ``-ss`` 快速定位 + ``-t`` 时长截取实现精确切片。
- 批量导出入口：:meth:`ClipExporter.export_all`，以生成器模式逐个产出
  :class:`ExportResult`，便于上游流式处理而无需缓存全量结果。
- 原子写入保证：导出结果先写入 ``uuid`` 命名的临时文件，FFmpeg 成功退出
  后再通过 ``os.replace`` 更名为最终路径，避免用户看到半成品文件。
- 缩略图异步生成：利用共享线程池在后台抽取关键帧，不阻塞主导出流程。

关键特性
--------
- 双编码模式：``copy``（流拷贝，零重编码）与 ``reencode``（libx264/HW 重编码）。
- Copy 模式自动回退：存在视频滤镜（如 9:16 裁剪）或 ``start_sec > 0``
  （非零起点无法保证关键帧对齐）时，自动降级到硬件编码（优先 NVENC）/
  ``libx264`` 以保证切口精度。
- 实时进度回调：通过 ``-progress pipe:1`` 读取 FFmpeg 进度，支持进程追踪
  与取消（``on_process``），并内置 300 秒看门狗防止卡死。
- 路径安全防护：文件名消毒（移除 Windows 非法字符）+ ``os.path.realpath``
  双重校验，防止路径遍历攻击。
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
from lsc.config import ExportProfile, LscConfig, preferred_hw_video_codec
from lsc.utils.gpu_ffmpeg import (
    build_cpu_vf,
    build_cuda_vf,
    input_hwaccel_args,
    prefer_gpu_filters,
)
from lsc.utils.process_launcher import prepare_launch, set_stream_nonblocking

_log = get_logger(__name__)

_DEFAULT_CRF = 23
_DEFAULT_AUDIO_BITRATE = "128k"
_DEFAULT_VSCALE = "scale=1080:1920"

# 帧率比较容差：源帧率与目标差异小于此值视为相等，避免无意义的 fps 滤镜
_FPS_TOLERANCE = 0.5


def _parse_stream_fps(fps_str: str) -> float | None:
    """解析 FFprobe 帧率字符串（如 "30/1" 或 "30000/1001"）为浮点数。"""
    if not fps_str:
        return None
    try:
        if "/" in fps_str:
            num, den = fps_str.split("/", 1)
            num_f, den_f = float(num), float(den)
            return num_f / den_f if den_f else None
        return float(fps_str)
    except (ValueError, ZeroDivisionError):
        return None


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
    """基于 FFmpeg 的片段导出器，支持缩略图与竖屏裁剪。

    生命周期
    --------
    ``__init__(config)`` → :meth:`export_clip` 或 :meth:`export_all`
    → 产出 :class:`ExportResult`。

    导出模式
    --------
    - ``copy``：直接拷贝视频/音频流，速度最快，但切片精度受限于关键帧位置；
      适合完整 GOP 对齐的场景。
    - ``reencode``：使用 ``libx264``（或硬件编码器）重新编码，确保起止时间
      精确，但耗时更长。

    Copy 模式在以下情况自动回退到 ``libx264``：
    - 配置了视频滤镜（如 9:16 裁剪、缩放）；
    - 起始时间 ``start_sec > 0``（非零起点无法保证关键帧对齐）。
    """

    # Shared pool for thumbnail generation so exports don't block each other.
    _thumbnail_executor: ThreadPoolExecutor | None = None
    _thumbnail_lock = threading.Lock()

    def _probe_source_video(self, video_path: str) -> tuple[tuple[int, int] | None, float | None]:
        """探测源视频的分辨率和平均帧率。"""
        from lsc.utils.gpu_ffmpeg import probe_video_stream

        if not video_path or not os.path.isfile(video_path):
            return None, None
        info = probe_video_stream(video_path, self.ffprobe)
        if not info:
            return None, None
        resolution = None
        if "width" in info and "height" in info:
            resolution = (int(info["width"]), int(info["height"]))
        fps = float(info["fps"]) if info.get("fps") else None
        return resolution, fps

    def _probe_codec_name(self, video_path: str) -> str | None:
        from lsc.utils.gpu_ffmpeg import probe_video_stream

        if not video_path or not os.path.isfile(video_path):
            return None
        return probe_video_stream(video_path, self.ffprobe).get("codec_name") or None

    def _export_cpu_fallback(
        self,
        *,
        video_path: str,
        start_sec: float,
        duration: float,
        effective_profile: ExportProfile,
        tmp_output_path: str,
        env: dict,
        creation_flags: int,
        cwd: str | None,
        progress_callback,
        on_process,
    ) -> bool:
        """GPU 滤镜失败后：CUVID 硬解 + CPU crop/scale + NVENC/软编。"""
        cpu_vf = build_cpu_vf(
            vertical_crop=effective_profile.vertical_crop,
            resolution=effective_profile.resolution,
            fps=effective_profile.fps,
        )
        codec_name = self._probe_codec_name(video_path)
        hw = input_hwaccel_args(
            codec_name=codec_name, prefer_cuvid=True, output_format_cuda=False,
        )
        cmd = [self.ffmpeg, "-y", "-loglevel", "warning", *hw]
        cmd += ["-ss", f"{start_sec:.3f}", "-i", video_path, "-t", f"{duration:.3f}"]
        cmd += effective_profile.ffmpeg_video_args()
        cmd += effective_profile.ffmpeg_audio_args()
        cmd += cpu_vf
        cmd += ["-movflags", "+faststart", tmp_output_path]
        _log.info("export CPU fallback filters=%s", cpu_vf[1] if cpu_vf else "none")
        try:
            run_kwargs: dict = {
                "capture_output": True, "text": True, "timeout": 300,
                "encoding": "utf-8", "errors": "replace",
                "env": env, "cwd": cwd,
            }
            if creation_flags:
                run_kwargs["creationflags"] = creation_flags
            result = subprocess.run(cmd, **run_kwargs)
            return result.returncode == 0 and os.path.isfile(tmp_output_path)
        except Exception as exc:
            _log.warning("CPU fallback export failed: %s", exc)
            return False

    def _optimize_profile_filters(self, profile: ExportProfile, video_path: str) -> ExportProfile:
        """根据源视频参数优化 profile，移除不必要的 scale/fps 滤镜。

        当 profile 的目标分辨率/帧率与源视频相同时，移除 scale/fps 滤镜，
        避免无意义的解码→滤镜→编码处理，从而降低画质损失。

        vertical_crop 滤镜永远不会被移除（它改变了画面构图）。
        """
        if not profile.resolution and not profile.fps:
            return profile  # 无滤镜需要优化

        src_res, src_fps = self._probe_source_video(video_path)
        if src_res is None and src_fps is None:
            return profile  # 探测失败，保守返回原始 profile

        new_resolution = profile.resolution
        new_fps = profile.fps

        # 检查分辨率是否匹配
        if profile.resolution and src_res:
            # profile.resolution 格式为 "1920x1080"
            try:
                target_w, target_h = profile.resolution.split("x", 1)
                if int(target_w) == src_res[0] and int(target_h) == src_res[1]:
                    _log.debug(
                        "源视频分辨率 %dx%d 与 profile 一致，跳过 scale 滤镜",
                        src_res[0], src_res[1],
                    )
                    new_resolution = ""
            except (ValueError, IndexError):
                pass

        # 检查帧率是否匹配（允许容差）
        if profile.fps and profile.fps > 0 and src_fps is not None and abs(profile.fps - src_fps) < _FPS_TOLERANCE:
            _log.debug(
                "源视频帧率 %.2f 与 profile fps=%.1f 一致，跳过 fps 滤镜",
                src_fps, profile.fps,
            )
            new_fps = 0.0

        # 如果没有变化，返回原 profile
        if new_resolution == profile.resolution and new_fps == profile.fps:
            return profile

        # 返回优化后的 profile（vertical_crop 保持不变）
        return ExportProfile(
            crf=profile.crf, codec=profile.codec, preset=profile.preset,
            audio_bitrate=profile.audio_bitrate,
            vertical_crop=profile.vertical_crop,
            rate_mode=profile.rate_mode, video_bitrate=profile.video_bitrate,
            resolution=new_resolution, fps=new_fps,
            generate_thumbnail=getattr(profile, 'generate_thumbnail', False),
        )

    @classmethod
    def _get_thumbnail_executor(cls) -> ThreadPoolExecutor:
        """获取（或惰性创建）缩略图生成专用线程池。

        使用双重检查锁定确保多线程环境下只创建一个 ThreadPoolExecutor，
        避免并发导出时重复创建线程池导致资源浪费。
        """
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
        """从视频中导出单个片段。

        FFmpeg 切片命令结构
        -------------------
        使用 ``-ss <start_sec> -i <video_path> -t <duration>`` 实现快速定位
        与时长截取。视频/音频编码参数由 :class:`ExportProfile` 提供；
        若配置了视频滤镜则追加 ``-vf`` 参数。

        编码模式与回退逻辑
        ------------------
        - ``copy`` 模式（``profile.is_copy``）直接拷贝视频/音频流，速度最快；
          但若存在视频滤镜（如 9:16 裁剪）或 ``start_sec > 0``，
          则自动回退到 ``libx264`` + ``medium`` preset 以保证切口精度。
        - ``reencode`` 模式始终使用 ``libx264``（或配置的硬件编码器）重新编码。

        原子写入
        --------
        输出先写入 ``<safe_title>.<uuid8>_tmp.mp4``，FFmpeg 成功退出后
        通过 ``os.replace`` 更名为最终路径；失败时自动清理临时文件，
        避免生成半成品文件。

        进度回调
        --------
        当提供 ``progress_callback`` 时，使用 ``-progress pipe:1`` 让 FFmpeg
        将进度写入 stdout；主线程逐行解析 ``out_time_ms`` 并节流回调
        （百分比变化 ≥1% 或间隔 ≥200ms），同时后台线程收集 stderr 尾部
        用于错误诊断。

        参数
        ----
        video_path : str
            源视频文件路径。
        start_sec, end_sec : float
            切片起止时间（秒）。
        output_dir : str
            输出目录，不存在则自动创建。
        title : str
            片段标题，用于生成文件名（自动消毒 Windows 非法字符及路径遍历字符）。
        clip_index : int
            片段序号，用于日志与默认文件名。
        vertical_crop : bool
            是否应用 9:16 竖屏裁剪滤镜。
        codec : str
            视频编码器名称，仅当 ``profile`` 为 ``None`` 时生效。
        profile : ExportProfile | None
            完整编码配置；提供时覆盖 ``codec`` 与 ``vertical_crop`` 参数。
        progress_callback : callable | None
            进度回调 ``callback(percent: float, elapsed: float, total: float)``。
            提供时启用 ``-progress pipe:1`` 与 ``Popen`` 模式。
        on_process : callable | None
            进程启动回调 ``callback(proc: subprocess.Popen)``，
            用于追踪并可选取消 FFmpeg 进程。
        """
        if not os.path.isfile(video_path):
            return ExportResult(False, "", clip_index, title or f"clip_{clip_index}",
                                error=f"录制文件不存在 / video not found: {video_path}")

        os.makedirs(output_dir, exist_ok=True)

        duration = end_sec - start_sec
        if duration < 1:
            return ExportResult(False, "", clip_index, title or f"clip_{clip_index}",
                                error=f"片段过短 / clip too short: {duration:.1f}s（至少需要 1 秒）")

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
                                error="无效的片段标题（包含非法路径字符）")

        # Avoid overwriting existing files by appending an incremental suffix
        base_output_path = output_path
        suffix = 1
        while os.path.exists(output_path):
            output_path = f"{base_output_path[:-4]}_{suffix}.mp4"
            suffix += 1

        # 确定使用的编码 profile：优先使用外部传入的完整配置；
        # 若未提供则回退到 config 中的默认 profile，并将 legacy 的
        # codec / vertical_crop 参数合并进去以保持向后兼容。
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

        # 画质优化：根据源视频参数移除不必要的 scale/fps 滤镜，
        # 避免每次导出都做一次无意义的解码→滤镜→编码（减少 Generation Loss）
        profile = self._optimize_profile_filters(profile, video_path)

        # Copy 模式回退逻辑（Copy mode fallback）
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        # 1) 视频滤镜（如 9:16 裁剪、缩放）无法在 copy 模式下应用，
        #    必须重编码，因此只要存在 filter_args 即触发回退。
        # 2) Copy 模式从非 0 秒开始切片时，FFmpeg 只能 seek 到最近的关键帧，
        #    导致实际切口晚于 start_sec，精度不足；
        #    因此只要 start_sec > 0 也强制重编码。
        # 回退优先 NVENC（无 GPU 再 libx264），保留原 profile 的其他参数。
        filter_args = profile.ffmpeg_filter_args()
        effective_profile = profile
        needs_reencode = bool(profile.is_copy and filter_args)
        if profile.is_copy and start_sec > 0:
            needs_reencode = True
            _log.info(
                "copy mode with non-zero start (%.3fs) falls back to HW/soft reencode for precise cut",
                start_sec,
            )
        if needs_reencode:
            fallback_codec = preferred_hw_video_codec()
            # 带滤镜的导出用更快的 NVENC preset，避免 p6 拖长时间抬高整机负载
            export_preset = "faster" if fallback_codec.endswith("_nvenc") else "medium"
            effective_profile = ExportProfile(
                crf=profile.crf, codec=fallback_codec, preset=export_preset,
                audio_bitrate=profile.audio_bitrate, vertical_crop=profile.vertical_crop,
                rate_mode=profile.rate_mode, video_bitrate=profile.video_bitrate,
                resolution=profile.resolution, fps=profile.fps,
            )
            filter_args = effective_profile.ffmpeg_filter_args()
            _log.info("export reencode fallback codec=%s", fallback_codec)

        # 有滤镜时优先 CUVID + scale_cuda；失败由下方执行结果触发 CPU 回退
        codec_name = self._probe_codec_name(video_path)
        use_gpu_filters = (
            prefer_gpu_filters(self.ffmpeg)
            and bool(filter_args)
            and effective_profile.is_hardware
        )
        if use_gpu_filters:
            hwaccel = input_hwaccel_args(codec_name=codec_name, prefer_cuvid=True)
            filter_args = build_cuda_vf(
                vertical_crop=effective_profile.vertical_crop,
                resolution=effective_profile.resolution,
                fps=effective_profile.fps,
            )
            # 加速 NVENC preset（settings medium→p6 太慢）
            if effective_profile.codec.endswith("_nvenc") and effective_profile.preset in (
                "medium", "slow", "slower", "veryslow",
            ):
                effective_profile = ExportProfile(
                    crf=effective_profile.crf, codec=effective_profile.codec, preset="faster",
                    audio_bitrate=effective_profile.audio_bitrate,
                    vertical_crop=effective_profile.vertical_crop,
                    rate_mode=effective_profile.rate_mode,
                    video_bitrate=effective_profile.video_bitrate,
                    resolution=effective_profile.resolution, fps=effective_profile.fps,
                    generate_thumbnail=getattr(effective_profile, "generate_thumbnail", False),
                )
        elif effective_profile.codec != "copy":
            hwaccel = input_hwaccel_args(
                codec_name=codec_name,
                prefer_cuvid=True,
                output_format_cuda=False,  # 无 GPU 滤镜时让帧落系统内存
            )
        else:
            hwaccel = []

        def _assemble_cmd(hw: list[str], vf: list[str], prof: ExportProfile) -> list[str]:
            out = [self.ffmpeg, "-y", "-loglevel", "warning", *hw]
            out += ["-ss", f"{start_sec:.3f}", "-i", video_path, "-t", f"{duration:.3f}"]
            out += prof.ffmpeg_video_args()
            # 音频尽量 copy，避免 AAC 软编占 CPU（失败时外层可再试）
            if prof.is_copy:
                out += prof.ffmpeg_audio_args()
            else:
                out += ["-c:a", "copy"]
            out += vf
            out += ["-movflags", "+faststart"]
            return out

        cmd = _assemble_cmd(hwaccel, filter_args, effective_profile)

        _log.info(
            "export ffmpeg: codec=%s hwaccel=%s filters=%s start=%.2f dur=%.2f",
            effective_profile.codec,
            " ".join(hwaccel) if hwaccel else "none",
            filter_args[1] if filter_args else "none",
            start_sec,
            duration,
        )

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
                # 使用 Popen 模式：实时读取 stdout 中的进度行，
                # 同时后台线程收集 stderr 尾部用于错误诊断，
                # 并启动 watchdog 线程在 300 秒无响应时强制终止进程。
                # 调用方可通过 on_process 获取 Popen 对象以追踪或取消任务。
                proc = subprocess.Popen(cmd, **popen_kwargs)
                set_stream_nonblocking(proc.stderr)
                # 通知调用方进程已启动，便于追踪并取消该 FFmpeg 进程
                if on_process is not None:
                    try:
                        on_process(proc)
                    except Exception as exc:
                        _log.debug("操作异常（已忽略）: %s", exc)
                state: dict[str, int] = {}
                last_reported_percent = -1.0
                last_reported_time = 0.0
                stderr_tail: deque[str] = deque(maxlen=20)
                export_timed_out = False

                def _stderr_reader() -> None:
                    """后台线程：持续读取 FFmpeg stderr 并保留尾部用于错误诊断。"""
                    try:
                        for line in proc.stderr:
                            stderr_tail.append(line.rstrip())
                    except Exception as exc:
                        _log.debug("操作异常（已忽略）: %s", exc)

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
                    if proc.stdout is None:
                        return ExportResult(
                            False, output_path, clip_index, safe_title,
                            error="FFmpeg 进程管道初始化失败（内部错误）"
                        )
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
                                except Exception as exc:
                                    _log.debug("进度回调异常: %s", exc)
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
                                        error="导出超时（FFmpeg 运行超过 300 秒）")
                if proc.returncode != 0:
                    error_tail = "\n".join(stderr_tail)
                    _cleanup_tmp(tmp_output_path)
                    if use_gpu_filters:
                        _log.warning("GPU 导出失败，回退 CPU 滤镜: %s", error_tail[-200:])
                        if self._export_cpu_fallback(
                            video_path=video_path,
                            start_sec=start_sec,
                            duration=duration,
                            effective_profile=effective_profile,
                            tmp_output_path=tmp_output_path,
                            env=env,
                            creation_flags=creation_flags,
                            cwd=cwd,
                            progress_callback=progress_callback,
                            on_process=on_process,
                        ):
                            pass  # tmp 已写好，继续 rename
                        else:
                            return ExportResult(
                                False, output_path, clip_index, safe_title,
                                error=error_tail[-500:] or "FFmpeg 导出失败（GPU/CPU 均失败）",
                            )
                    else:
                        return ExportResult(False, output_path, clip_index, safe_title,
                                            error=error_tail[-500:] or "FFmpeg 导出失败（无详细错误信息）")
            else:
                # 无进度回调时使用 subprocess.run 简化执行，统一 300 秒超时；
                # 错误信息截取 stderr 尾部 500 字符返回。
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=300,
                    encoding="utf-8", errors="replace",
                    env=env, cwd=cwd,
                    **({"creationflags": creation_flags} if creation_flags else {}),
                )
                if result.returncode != 0:
                    _cleanup_tmp(tmp_output_path)
                    if use_gpu_filters:
                        _log.warning("GPU 导出失败，回退 CPU 滤镜: %s", (result.stderr or "")[-200:])
                        if not self._export_cpu_fallback(
                            video_path=video_path,
                            start_sec=start_sec,
                            duration=duration,
                            effective_profile=effective_profile,
                            tmp_output_path=tmp_output_path,
                            env=env,
                            creation_flags=creation_flags,
                            cwd=cwd,
                            progress_callback=None,
                            on_process=None,
                        ):
                            return ExportResult(
                                False, output_path, clip_index, safe_title,
                                error=(result.stderr or "")[-500:] or "FFmpeg 导出失败（GPU/CPU 均失败）",
                            )
                    else:
                        return ExportResult(False, output_path, clip_index, safe_title,
                                            error=result.stderr[-500:] or "FFmpeg 导出失败（无详细错误信息）")
        except subprocess.TimeoutExpired:
            _cleanup_tmp(tmp_output_path)
            return ExportResult(False, output_path, clip_index, safe_title,
                                error="导出超时 timed out（FFmpeg 运行超过 300 秒）")
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

        thumbnail_path = ""
        if profile and profile.generate_thumbnail:
            thumb_future = None
            try:
                midpoint = (start_sec + end_sec) / 2
                thumb_future = self._get_thumbnail_executor().submit(
                    self._generate_thumbnail, video_path, midpoint, output_dir, safe_title
                )
            except Exception as exc:
                _log.warning("Failed to submit thumbnail job for %s: %s", output_path, exc)

            actual_duration = self._get_duration(output_path)

            if thumb_future is not None:
                try:
                    thumbnail_path = thumb_future.result(timeout=10.0)
                except Exception as exc:
                    _log.warning("Thumbnail generation failed/timeout for %s: %s", output_path, exc)
        else:
            actual_duration = self._get_duration(output_path)

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
        """批量导出所有高光片段。

        以生成器逐个产出 :class:`ExportResult`，便于上游流式处理
        （如实时更新 UI 或逐条写入数据库），无需缓存全量结果。

        每个片段的标题由轮次（round_number）、得分（score）与描述拼接而成。
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
        """在指定时间点生成缩略图。

        使用 FFmpeg ``-ss <time> -vframes 1`` 抽取单帧，
        通过 ``-q:v 3`` 控制 JPEG 质量（范围 2-31，越小质量越高）。
        仅由共享线程池在后台调用，不阻塞主导出流程。
        """
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
