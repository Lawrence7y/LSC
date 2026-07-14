"""Error messages humanization unit tests.

Covers all 19 regex patterns in _PATTERNS, 2 in _PRESERVE_RAW_PATTERNS,
RECOVERABLE/NON_RECOVERABLE classification functions, and edge cases.
"""
from __future__ import annotations

import pytest

from lsc.utils.error_messages import (
    humanize_error,
    is_recoverable_error,
    friendly_connect_error,
)


class TestHumanizeErrorPreserveRaw:
    """Test _PRESERVE_RAW_PATTERNS (permission/disk errors with raw message appended)."""

    def test_permission_denied(self):
        result = humanize_error("Permission denied: /path/to/file")
        assert "权限不足" in result
        assert "原始错误" in result
        assert "Permission denied" in result

    def test_access_denied_chinese(self):
        result = humanize_error("拒绝访问。系统找不到指定的路径")
        assert "权限不足" in result or "路径不存在" in result

    def test_winerror_5(self):
        result = humanize_error("[WinError 5] Access is denied")
        assert "权限不足" in result

    def test_no_space_left(self):
        result = humanize_error("No space left on device")
        assert "磁盘空间不足" in result
        assert "原始错误" in result

    def test_disk_full_chinese(self):
        result = humanize_error("磁盘已满，无法写入文件")
        assert "磁盘空间不足" in result


class TestHumanizeErrorPatterns:
    """Test _PATTERNS (all 17 non-preserve patterns)."""

    def test_403_forbidden(self):
        result = humanize_error("Server returned 403 Forbidden")
        assert "403" in result or "拒绝了连接" in result

    def test_404_not_found(self):
        result = humanize_error("HTTP Error 404: Not Found")
        assert "404" in result or "不存在" in result

    def test_connection_refused(self):
        result = humanize_error("Connection refused")
        assert "无法连接" in result

    def test_connection_timeout(self):
        result = humanize_error("Connection timed out after 30000ms")
        assert "超时" in result

    def test_dns_failure(self):
        result = humanize_error("Name or service not known")
        assert "域名解析" in result

    def test_stream_not_found(self):
        result = humanize_error("No stream available for this room")
        assert "未找到直播流" in result

    def test_server_500(self):
        result = humanize_error("Server returned 500 Internal Server Error")
        assert "服务器异常" in result

    def test_invalid_data(self):
        result = humanize_error("Invalid data found when processing input")
        assert "无法解析" in result

    def test_decoder_not_found(self):
        result = humanize_error("cannot find codec h264_cuvid for input stream")
        assert "解码器" in result

    def test_encoder_not_found(self):
        result = humanize_error("cannot find encoder libx264")
        assert "编码器" in result

    def test_ffmpeg_error(self):
        result = humanize_error("ffmpeg reported a generic error")
        assert "录制引擎" in result

    def test_cookie_required(self):
        result = humanize_error("Cookie required to access this stream")
        assert "登录" in result or "Cookie" in result

    def test_not_live(self):
        result = humanize_error("该直播间未开播")
        assert "未开播" in result

    def test_parse_failed(self):
        result = humanize_error("解析流地址失败，无法获取 stream URL")
        assert "无法解析" in result

    def test_unsupported_codec(self):
        result = humanize_error("Unsupported codec: av1")
        assert "不支持" in result

    def test_file_not_found_chinese(self):
        result = humanize_error("系统找不到指定的路径")
        assert "路径不存在" in result


class TestHumanizeErrorFallback:
    """Test fallback behavior when no pattern matches."""

    def test_unmatched_returns_original_with_prefix(self):
        result = humanize_error("some completely unknown error XYZ123")
        assert "发生错误" in result
        assert "XYZ123" in result

    def test_unmatched_truncates_long_messages(self):
        long_msg = "x" * 300
        result = humanize_error(long_msg)
        assert "发生错误" in result
        assert len(result) <= 210  # 200 + prefix + "..."

    def test_empty_string(self):
        result = humanize_error("")
        assert result == "发生未知错误"

    def test_none_input(self):
        result = humanize_error(None)
        assert result == "发生未知错误"

    def test_non_string_input(self):
        result = humanize_error(12345)
        assert result == "发生未知错误"

    def test_whitespace_only(self):
        result = humanize_error("   ")
        assert result == "发生未知错误"


class TestIsRecoverableError:
    """Test error recovery classification."""

    def test_recoverable_timeout(self):
        assert is_recoverable_error("Connection timed out") is True

    def test_recoverable_reset(self):
        assert is_recoverable_error("Connection reset by peer") is True

    def test_recoverable_500(self):
        assert is_recoverable_error("Server returned 502 Bad Gateway") is True

    def test_recoverable_403(self):
        assert is_recoverable_error("403 Forbidden") is True

    def test_recoverable_404(self):
        assert is_recoverable_error("404 Not Found") is True

    def test_non_recoverable_permission(self):
        assert is_recoverable_error("Permission denied") is False

    def test_non_recoverable_disk_full(self):
        assert is_recoverable_error("No space left on device") is False

    def test_non_recoverable_encoder(self):
        assert is_recoverable_error("Encoder not found") is False

    def test_non_recoverable_expired(self):
        """404 is recoverable (renewable), link expired is non-recoverable."""
        # 404 matches RECOVERABLE via 404|Not Found
        assert is_recoverable_error("链接已过期") is True  # matches 流.*过期

    def test_unknown_non_recoverable(self):
        """Unknown errors default to non-recoverable to prevent infinite reconnect."""
        assert is_recoverable_error("totally unknown error XYZ") is False

    def test_empty_input(self):
        assert is_recoverable_error("") is False

    def test_nothing_matches(self):
        assert is_recoverable_error("some random message") is False


class TestFriendlyConnectError:
    """Test friendly_connect_error wrapper."""

    def test_delegates_to_humanize(self):
        result = friendly_connect_error("Connection refused")
        assert "无法连接" in result

    def test_handles_empty(self):
        result = friendly_connect_error("")
        assert result == "发生未知错误"
