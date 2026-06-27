"""
Error message humanization for user-facing error display.

Maps raw FFmpeg/network/OS errors to readable Chinese messages.
Used by the WebSocket handler to return friendly messages to the frontend.
"""

import re


_PATTERNS: list[tuple[re.Pattern, str]] = [
    # HTTP / Network
    (re.compile(r"403|Forbidden", re.I),
     "平台拒绝了连接（403）。可能主播未开播，或需要登录 Cookie。"),
    (re.compile(r"404|Not Found", re.I),
     "直播流地址不存在（404）。主播可能已下播。"),
    (re.compile(r"Connection refused|ECONNREFUSED", re.I),
     "无法连接到直播服务器。请检查网络或稍后重试。"),
    (re.compile(r"Connection timed out|ETIMEDOUT", re.I),
     "连接直播服务器超时。网络不稳定或服务器无响应。"),
    (re.compile(r"Name or service not known|getaddrinfo", re.I),
     "域名解析失败。请检查网络连接。"),

    # Stream / Recording
    (re.compile(r"Stream not found|No stream|Input/output error.*stream", re.I),
     "未找到直播流。主播可能已下播或流地址已过期。"),
    (re.compile(r"No space left|ENOSPC|disk full", re.I),
     "磁盘空间不足，无法继续录制。请清理输出目录。"),
    (re.compile(r"Permission denied|EACCES|WinError 5|拒绝访问", re.I),
     "文件写入权限不足。请检查输出目录权限。"),
    (re.compile(r"Server returned 5\d\d|Internal Server Error", re.I),
     "直播平台服务器异常，请稍后重试。"),

    # FFmpeg
    (re.compile(r"Invalid data found when processing input", re.I),
     "无法解析直播流数据。流格式可能不受支持。"),
    (re.compile(r"Decoder.*not found|cannot find codec", re.I),
     "缺少视频解码器。请确保 FFmpeg 安装完整。"),
    (re.compile(r"Encoder.*not found|cannot find encoder", re.I),
     "缺少视频编码器。请检查编码器设置。"),
    (re.compile(r"error.*ffmpeg|ffmpeg.*error", re.I),
     "录制引擎出错。请检查编码器设置或重启应用。"),

    # Platform-specific
    (re.compile(r"Cookie|登录|login required", re.I),
     "需要登录凭证。请检查 Cookie 配置。"),
    (re.compile(r"直播间未开播|房间未开播|not live", re.I),
     "该直播间当前未开播。"),
    (re.compile(r"解析流地址失败|parse.*fail|extract.*fail", re.I),
     "无法解析直播流地址。平台可能已更新协议。"),

    # Quality / Format
    (re.compile(r"Unsupported codec|unsupported format", re.I),
     "不支持的视频格式。请尝试切换编码器。"),
]


def humanize_error(raw: str) -> str:
    """Convert raw technical error to user-friendly Chinese message.

    Returns the original message with a prefix if no pattern matches.
    """
    if not raw or not isinstance(raw, str):
        return "发生未知错误"

    raw_stripped = raw.strip()

    for pattern, msg in _PATTERNS:
        if pattern.search(raw_stripped):
            return msg

    # No match: return original with prefix
    # Truncate very long messages
    if len(raw_stripped) > 200:
        raw_stripped = raw_stripped[:200] + "..."

    return f"发生错误：{raw_stripped}"


def friendly_connect_error(raw: str) -> str:
    """Specialized version for connection-stage errors (more context)."""
    return humanize_error(raw)
