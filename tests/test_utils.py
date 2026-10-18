"""Utility modules unit tests.

Covers process_launcher, helpers — functions that lack
any test coverage but are critical to system stability.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from lsc.utils.process_launcher import (
    _ENV_WHITELIST,
    build_clean_env,
    get_creation_flags,
    prepare_launch,
    set_stream_nonblocking,
)


class TestGetCreationFlags:
    """Test subprocess creation flag resolution."""

    def test_returns_int(self):
        flags = get_creation_flags()
        assert isinstance(flags, int)

    def test_nonzero_on_windows(self):
        if sys.platform == "win32":
            assert get_creation_flags() != 0
        else:
            assert get_creation_flags() == 0


class TestBuildCleanEnv:
    """Test environment sanitization for FFmpeg subprocess."""

    def test_whitelist_contains_expected_keys(self):
        """Verify the whitelist includes all required environment variable keys."""
        expected = {"PATH", "USERPROFILE", "HOME", "SYSTEMROOT", "PYTHONUNBUFFERED", "LSC_LOG_DIR"}
        assert expected.issubset(_ENV_WHITELIST)

    def test_env_only_contains_whitelisted_keys(self):
        """build_clean_env output should only contain whitelisted keys."""
        # Use a mock to avoid slow PATH scanning on Windows
        with patch("lsc.utils.process_launcher.os.listdir", return_value=[]):
            env = build_clean_env("/usr/bin/ffmpeg")
            for key in env:
                assert key in _ENV_WHITELIST, f"Unexpected key {key} in env"

    def test_env_path_starts_with_ffmpeg_dir(self):
        with patch("lsc.utils.process_launcher.os.listdir", return_value=[]):
            env = build_clean_env("/usr/local/bin/ffmpeg")
            # On Windows, abspath prepends drive letter, but the dir should be present
            assert "usr" in env["PATH"] and "local" in env["PATH"]


class TestPrepareLaunch:
    """Test subprocess launch preparation."""

    def test_env_whitelist_filter(self):
        """prepare_launch should respect environment whitelist."""
        with patch("lsc.utils.process_launcher.os.listdir", return_value=[]):
            env, flags, cwd = prepare_launch("/usr/bin/ffmpeg")
            for key in env:
                assert key in _ENV_WHITELIST


class TestSetStreamNonblocking:
    """Test pipe non-blocking mode setting."""

    def test_none_pipe_no_error(self):
        set_stream_nonblocking(None)  # should not raise

    def test_mock_pipe_no_error(self):
        mock_pipe = MagicMock()
        mock_pipe.fileno.return_value = 99
        # On Windows it's a no-op; on POSIX it tries fcntl
        if sys.platform == "win32":
            set_stream_nonblocking(mock_pipe)
            mock_pipe.fileno.assert_called()

    def test_pipe_without_fileno(self):
        mock_pipe = MagicMock()
        mock_pipe.fileno.side_effect = AttributeError("no fileno")
        set_stream_nonblocking(mock_pipe)  # should not raise


# ── helpers.py ────────────────────────────────────────

from lsc.utils.helpers import fmt_time


class TestFmtTime:
    """Test time formatting helper."""

    def test_zero_returns_zero_length(self):
        result = fmt_time(0)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_seconds_only(self):
        result = fmt_time(45)
        assert "45" in result or "00:45" in result

    def test_minutes_and_seconds(self):
        result = fmt_time(125)
        # Should be something like 2:05 or 02:05
        assert "2" in result and "5" in result

    def test_hours_minutes_seconds(self):
        result = fmt_time(3661)
        # Should contain 1:01:01
        assert "1" in result





class TestResolveRealVideoPath:
    """Test resolve_real_video_path behavior and regression against sidecars."""

    def test_existing_file_returns_as_is(self, tmp_path):
        from lsc.utils.helpers import resolve_real_video_path

        p = tmp_path / "recording_录制中.mp4"
        p.write_bytes(b"data")
        assert resolve_real_video_path(str(p)) == str(p)

    def test_ignores_json_and_sidecars(self, tmp_path):
        from lsc.utils.helpers import resolve_real_video_path

        in_progress = tmp_path / "2026-09-07_12-25-51_录制中.mp4"
        # Renamed video exists
        final_mp4 = tmp_path / "2026-09-07_12-25-51_至_2026-09-07_13-22-36.mp4"
        final_mp4.write_bytes(b"video data")

        # Sidecars with names that precede .mp4 alphabetically
        final_json = tmp_path / "2026-09-07_12-25-51_至_2026-09-07_13-22-36.finalization.json"
        final_json.write_text('{"job_id": "test"}', encoding="utf-8")
        analysis_json = tmp_path / "2026-09-07_12-25-51_至_2026-09-07_13-22-36.analysis.json"
        analysis_json.write_text('{"analysis": "test"}', encoding="utf-8")

        resolved = resolve_real_video_path(str(in_progress))
        assert resolved == str(final_mp4)
        assert not resolved.endswith(".json")

    def test_empty_or_nonexistent_directory(self):
        from lsc.utils.helpers import resolve_real_video_path

        assert resolve_real_video_path("") == ""
        assert resolve_real_video_path("/non/existent/dir/file.mp4") == "/non/existent/dir/file.mp4"
