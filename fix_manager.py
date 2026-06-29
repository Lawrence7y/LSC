"""Replace start_audio_align method in manager.py"""
import pathlib

path = pathlib.Path("lsc/gui/multi_room/manager.py")
content = path.read_text(encoding="utf-8")

marker_start = "    def start_audio_align(self, room_ids: list[str]) -> bool:"
marker_end = "        return True"

idx_start = content.index(marker_start)
idx_end = content.index(marker_end, idx_start) + len(marker_end)

old_block = content[idx_start:idx_end]

new_block = '''    def start_audio_align(self, room_ids: list[str]) -> bool:
        """Launch background audio cross-correlation alignment for selected rooms.

        Only rooms that are actively recording can participate in audio
        alignment, because the algorithm relies on local recording files
        (which are guaranteed to be in sync with the preview). Non-
        recording rooms fall back to buffer-based alignment instead.

        Args:
            room_ids: List of room identifiers to align. Must contain at
                least two valid recording rooms.

        Returns:
            True if the alignment worker was started successfully (or if
                the call was short-circuited due to insufficient recording
                rooms). Failures are reported through the
                ``align_finished`` signal instead.
        """
        from lsc.config import load_config as _load_config
        _cfg = _load_config()
        ffmpeg_path = _cfg.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"

        rooms_data: list[dict[str, Any]] = []
        skipped_names: list[str] = []
        for rid in room_ids:
            room = self.get_room(rid)
            if room is None or not room.stream_info:
                continue
            if room.is_recording and room.record_output_path and room.recording_start_mono:
                elapsed = _time.monotonic() - room.recording_start_mono
                seek = max(0.0, elapsed - AUDIO_DURATION - _SEEK_BUFFER)
                rooms_data.append({
                    "room_id": rid,
                    "source": room.record_output_path,
                    "seek": seek,
                    "is_recording": True,
                    "streamer_name": room.streamer_name,
                })
            else:
                name = room.streamer_name or rid[:8]
                skipped_names.append(name)
                continue

        if len(rooms_data) < 2:
            skipped = "、".join(skipped_names) if skipped_names else "所选房间"
            self._on_align_finished({
                "success": False,
                "error": f"未录制的直播间不能参与音频对齐（{skipped} 未在录制），请先开始录制",
            })
            return True

        worker = _AudioAlignWorker(rooms_data, ffmpeg_path)
        worker.align_done.connect(self._on_align_finished)
        worker.finished.connect(lambda: worker.deleteLater())
        self._audio_align_worker = worker
        worker.start()
        return True'''

if old_block not in content:
    print("ERROR: old block not found")
    print("Looking for:", repr(old_block[:100]))
else:
    content = content.replace(old_block, new_block, 1)
    path.write_text(content, encoding="utf-8")
    print("Replacement done")
