"""
LSC Clip Exporter
=================
Exports highlight clips from video using FFmpeg.

Features:
  - Precise time-based cutting with re-encoding at cut points
  - Thumbnail generation at highlight midpoint
  - Vertical crop (9:16) for short video platforms
  - Batch export with progress tracking
"""

import json
import os
import re
import subprocess
from dataclasses import dataclass

from lsc import get_logger
from lsc.config import LscConfig

_log = get_logger(__name__)

_DEFAULT_CRF = 23


def parse_ffmpeg_progress_line(line: str, state: dict[str, int]) -> None:
    """Parse a single FFmpeg progress line and update state dict.

    FFmpeg outputs lines like 'out_time_ms=15000000' when using -progress pipe:1.
    """
    if "=" not in line:
        return
    key, value = line.strip().split("=", 1)
    if key == "out_time_ms" and value.isdigit():
        state["out_time_ms"] = int(value)
_DEFAULT_AUDIO_BITRATE = "128k"
_DEFAULT_VSCALE = "scale=1080:1920"


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


class ClipExporter:
    """FFmpeg-based clip exporter with thumbnail and vertical crop support."""

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
                    codec: str = "") -> ExportResult:
        """Export a single clip from the video."""
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

        use_codec = codec or self.export_cfg.codec
        do_crop = vertical_crop or self.export_cfg.vertical_crop

        # FFmpeg command
        cmd = [self.ffmpeg, "-y", "-loglevel", "warning"]

        # Seek to start (fast seek + accurate)
        cmd += ["-ss", f"{start_sec:.3f}", "-i", video_path]
        cmd += ["-t", f"{duration:.3f}"]

        if use_codec == "copy":
            # Stream copy (fast, but cuts may not be frame-accurate)
            cmd += ["-c", "copy"]
        else:
            # Re-encode (accurate cuts, slower)
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(_DEFAULT_CRF)]
            cmd += ["-c:a", "aac", "-b:a", _DEFAULT_AUDIO_BITRATE]

        if do_crop:
            # Crop to 9:16 vertical format
            # Input is 1920x1080, output should be 607x1080 (9:16)
            cmd += ["-vf", f"crop=ih*9/16:ih:(iw-ih*9/16)/2:0,{_DEFAULT_VSCALE}"]
            if use_codec == "copy":
                # Can't stream copy with video filter
                cmd = [self.ffmpeg, "-y", "-loglevel", "warning",
                       "-ss", f"{start_sec:.3f}", "-i", video_path,
                       "-t", f"{duration:.3f}",
                       "-vf", f"crop=ih*9/16:ih:(iw-ih*9/16)/2:0,{_DEFAULT_VSCALE}",
                       "-c:v", "libx264", "-preset", "medium", "-crf", str(_DEFAULT_CRF),
                       "-c:a", "aac", "-b:a", _DEFAULT_AUDIO_BITRATE]

        cmd += ["-movflags", "+faststart", output_path]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300,
                encoding="utf-8", errors="replace"
            )
            if result.returncode != 0:
                return ExportResult(False, output_path, clip_index, safe_title,
                                    error=result.stderr[-500:])
        except subprocess.TimeoutExpired:
            return ExportResult(False, output_path, clip_index, safe_title,
                                error="Export timed out")
        except Exception as e:
            return ExportResult(False, output_path, clip_index, safe_title,
                                error=str(e))

        # Verify output
        if not os.path.isfile(output_path):
            return ExportResult(False, output_path, clip_index, safe_title,
                                error="Output file not created")

        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)

        # Generate thumbnail
        thumbnail_path = ""
        try:
            thumbnail_path = self._generate_thumbnail(
                video_path, (start_sec + end_sec) / 2, output_dir, safe_title
            )
        except Exception as exc:
            # Thumbnail failure is non-critical, but log for diagnostics
            _log.warning(
                "thumbnail generation failed: clip=%s err=%s",
                safe_title, exc,
            )

        # Get actual duration
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
                   vertical_crop: bool = False) -> list[ExportResult]:
        """
        Export all highlights as individual clips.

        Parameters
        ----------
        video_path : str
            Source video file.
        highlights : list[dict]
            List of highlight dicts with start_sec, end_sec, score, description keys.
        output_dir : str
            Output directory for clips.
        vertical_crop : bool
            Whether to crop to 9:16 vertical format.
        """
        os.makedirs(output_dir, exist_ok=True)
        results = []

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

            results.append(result)

        # Save export manifest
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

        return results

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

        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode == 0 and os.path.isfile(thumb_path):
            return thumb_path
        return ""

    def _get_duration(self, filepath: str) -> float:
        from lsc.utils.helpers import probe_duration
        return probe_duration(self.ffprobe, filepath)
