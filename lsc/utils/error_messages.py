"""
Error message humanization for user-facing error display.

Maps raw FFmpeg/network/OS errors to readable Chinese messages.
Used by the WebSocket handler to return friendly messages to the frontend.
"""

import logging
import re

_log = logging.getLogger(__name__)

# 需要保留原始错误信息的模式（路径/磁盘相关，原始信息对定位问题关键）。
# 命中后追加 `（原始错误：{raw}）`，让用户看到具体路径与 strerror。
_PRESERVE_RAW_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"Permission denied|EACCES|WinError 5|拒绝访问", re.I),
     "文件写入权限不足。请检查输出目录权限"),
    (re.compile(r"No space left|ENOSPC|disk full|磁盘空间不足|磁盘已满", re.I),
     "磁盘空间不足，无法继续录制。请清理输出目录"),
]

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # HTTP / Network
    (re.compile(r"403|Forbidden", re.I),
     "平台拒绝了连接（403）。可能主播未开播，或需要登录 Cookie。"),
    (re.compile(r"404|Not Found", re.I),
     "直播流地址不存在（404）。主播可能已下播。"),
    (re.compile(r"Connection refused|ECONNREFUSED|连接被拒绝|拒绝连接", re.I),
     "无法连接到直播服务器。请检查网络或稍后重试。"),
    (re.compile(r"Connection timed out|ETIMEDOUT|连接超时|操作超时", re.I),
     "连接直播服务器超时。网络不稳定或服务器无响应。"),
    (re.compile(r"Name or service not known|getaddrinfo|不知道这样的主机|主机名无法解析", re.I),
     "域名解析失败。请检查网络连接。"),

    # Stream / Recording
    (re.compile(r"Stream not found|No stream|Input/output error.*stream", re.I),
     "未找到直播流。主播可能已下播或流地址已过期。"),
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

    # File / Path (中文 Windows)
    (re.compile(r"No such file|FileNotFoundError|找不到指定的文件|系统找不到指定的路径", re.I),
     "文件或路径不存在。请检查配置。"),
]

# 可恢复错误模式：网络抖动、流暂时中断、流地址过期等，值得自动重连。
_RECOVERABLE_PATTERNS: list[re.Pattern] = [
    re.compile(r"Server returned 5\d\d", re.I),
    re.compile(r"Connection (timed out|refused|reset)", re.I),
    re.compile(r"Stream ends prematurely", re.I),
    re.compile(r"Error number -138", re.I),
    re.compile(r"Invalid data found", re.I),
    re.compile(r"未增长|stalled|not growing", re.I),
    re.compile(r"流.*过期|链接已过期|鉴权失败", re.I),
]

# 不可恢复错误模式：权限/磁盘/配置类，重连也无效。
_NON_RECOVERABLE_PATTERNS: list[re.Pattern] = [
    re.compile(r"Permission denied|EACCES|WinError 5|拒绝访问", re.I),
    re.compile(r"No space left|ENOSPC|disk full|磁盘空间不足|磁盘已满", re.I),
    re.compile(r"Encoder.*not found|cannot find encoder", re.I),
    re.compile(r"403|Forbidden", re.I),
    re.compile(r"404|Not Found", re.I),
]


def humanize_error(raw: str) -> str:
    """Convert raw technical error to user-friendly Chinese message.

    For permission/disk errors, the original raw message (which contains
    the offending path and strerror) is appended so the user can locate
    the problem instead of seeing a generic "permission denied" message.

    Returns the original message with a prefix if no pattern matches.
    """
    if not raw or not isinstance(raw, str):
        _log.debug("humanize_error: empty or non-string input: %r", raw)
        return "发生未知错误"

    raw_stripped = raw.strip()
    if not raw_stripped:
        _log.debug("humanize_error: whitespace-only input")
        return "发生未知错误"

    # 优先匹配需要保留原始信息的模式（权限/磁盘类），追加原始错误便于定位
    for pattern, msg in _PRESERVE_RAW_PATTERNS:
        if pattern.search(raw_stripped):
            snippet = raw_stripped[:200] + ("..." if len(raw_stripped) > 200 else "")
            _log.debug("humanize_error matched PRESERVE_RAW: %s", pattern.pattern)
            return f"{msg}（原始错误：{snippet}）"

    for pattern, msg in _PATTERNS:
        if pattern.search(raw_stripped):
            _log.debug("humanize_error matched: %s -> %s", pattern.pattern, msg)
            return msg

    # No match: return original with prefix
    _log.debug("humanize_error no pattern matched, returning original prefix")
    # Truncate very long messages
    if len(raw_stripped) > 200:
        raw_stripped = raw_stripped[:200] + "..."

    return f"发生错误：{raw_stripped}"


def is_recoverable_error(raw: str) -> bool:
    """判断录制错误是否值得自动重连。

    网络抖动、流暂时中断等可恢复错误返回 True；
    权限拒绝、磁盘满、鉴权失败、主播下播等不可恢复错误返回 False。
    对未知错误默认返回 False，避免无限重连浪费资源。
    """
    if not raw or not isinstance(raw, str):
        _log.debug("is_recoverable_error: empty or non-string input: %r", raw)
        return False

    # 先判不可恢复（权限/磁盘/鉴权类优先）
    for pattern in _NON_RECOVERABLE_PATTERNS:
        if pattern.search(raw):
            _log.debug("is_recoverable_error: matched NON_RECOVERABLE: %s", pattern.pattern)
            return False
    # 再判可恢复
    recoverable = any(pattern.search(raw) for pattern in _RECOVERABLE_PATTERNS)
    if recoverable:
        _log.debug("is_recoverable_error: matched RECOVERABLE pattern")
    else:
        _log.debug("is_recoverable_error: no pattern matched, defaulting to non-recoverable")
    return recoverable


def friendly_connect_error(raw: str) -> str:
    """Specialized version for connection-stage errors (more context)."""
    result = humanize_error(raw)
    _log.debug("friendly_connect_error: mapped to: %s", result[:80])
    return result
