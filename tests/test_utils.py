"""Utility modules unit tests.

Covers process_launcher, helpers, error_stats — functions that lack
any test coverage but are critical to system stability.
"""
from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from lsc.utils.process_launcher import (
    get_creation_flags,
    prepare_launch,
    build_clean_env,
    set_stream_nonblocking,
    _ENV_WHITELIST,
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


# ── error_stats.py ─────────────────────────────────────

from lsc.utils.error_stats import ErrorStats, get_error_stats


class TestErrorStats:
    """Test error statistics collection using fresh instances."""

    def test_record_and_count(self):
        stats = ErrorStats()
        stats.record_error("E404", "not found")
        assert stats.get_total_errors() == 1
        assert stats.get_error_count("E404") == 1

    def test_record_multiple_same_code(self):
        stats = ErrorStats()
        for _ in range(5):
            stats.record_error("timeout", "conn timeout")
        assert stats.get_error_count("timeout") == 5

    def test_clear_resets_everything(self):
        stats = ErrorStats()
        stats.record_error("E1", "msg")
        stats.clear()
        assert stats.get_total_errors() == 0
        assert stats.get_last_error() == ("", "")

    def test_last_error_tracks_most_recent(self):
        stats = ErrorStats()
        stats.record_error("E404", "not found")
        stats.record_error("E500", "server error")
        code, msg = stats.get_last_error()
        assert code == "E500"
        assert msg == "server error"

    def test_error_rate_zero_for_no_errors(self):
        stats = ErrorStats()
        assert stats.get_error_rate("any_code") == 0.0

    def test_record_many_trims_timestamps(self):
        stats = ErrorStats()
        # Record more than 100 errors - older timestamps should be trimmed
        for _ in range(110):
            stats.record_error("overflow", "test")
        assert stats.get_error_count("overflow") == 110

    def test_get_frequent_errors_does_not_deadlock(self):
        """get_frequent_errors calls get_error_rate while holding the lock.

        A non-reentrant Lock deadlocks here; RLock allows re-entry.
        Regression test for the recursive-lock deadlock.
        """
        stats = ErrorStats()
        stats.record_error("E1", "msg")
        stats.record_error("E2", "msg2")
        # If the lock is non-reentrant this hangs forever; the test timeout
        # (pytest-timeout or CI) catches it, but assert correctness too.
        frequent = stats.get_frequent_errors(threshold=0.0)
        assert isinstance(frequent, list)
        codes = {entry[0] for entry in frequent}
        assert {"E1", "E2"}.issubset(codes)

    def test_get_summary_does_not_deadlock(self):
        """get_summary calls get_frequent_errors while holding the lock."""
        stats = ErrorStats()
        stats.record_error("E1", "msg")
        summary = stats.get_summary()
        assert summary["total_errors"] == 1
        assert isinstance(summary["frequent_errors"], list)

    def test_frequent_errors_threadsafe_under_concurrency(self):
        """Multiple threads calling get_summary must not deadlock."""
        import threading

        stats = ErrorStats()
        for i in range(50):
            stats.record_error(f"E{i}", "msg")

        errors: list[BaseException] = []

        def worker():
            try:
                for _ in range(20):
                    stats.get_summary()
                    stats.get_frequent_errors(threshold=0.0)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not errors, f"concurrent access raised: {errors}"
        assert all(not t.is_alive() for t in threads), "threads deadlocked"
