"""把底层错误翻译成用户可读的中文提示。"""
from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # HTTP 错误
    (re.compile(r"403|Forbidden", re.I),
     "access_denied",
     "平台拒绝了连接（403）。可能主播未开播，或需要登录 Cookie。"),
    (re.compile(r"404|Not Found", re.I),
     "not_found",
     "未找到直播流（404）。主播可能已下播或链接已失效。"),
    (re.compile(r"429|Too Many Requests", re.I),
     "rate_limited",
     "请求过于频繁（429）。请稍后再试。"),
    (re.compile(r"5\d{2}|Server Error", re.I),
     "server_error",
     "服务器错误。请稍后再试。"),

    # 网络错误
    (re.compile(r"Connection refused|ECONNREFUSED", re.I),
     "conn_refused",
     "无法连接到直播服务器。请检查网络或稍后重试。"),
    (re.compile(r"Connection reset|ECONNRESET", re.I),
     "conn_reset",
     "连接被重置。网络可能不稳定，请稍后重试。"),
    (re.compile(r"Connection timed out|ETIMEDOUT", re.I),
     "conn_timeout",
     "连接超时。请检查网络或稍后重试。"),
    (re.compile(r"timeout|timed out", re.I),
     "timeout",
     "操作超时。请检查网络或稍后重试。"),
    (re.compile(r"name resolution|dns|getaddrinfo|ENOTFOUND", re.I),
     "dns_error",
     "无法解析服务器地址。请检查网络连接。"),
    (re.compile(r"Network is unreachable|ENETUNREACH", re.I),
     "network_unreachable",
     "网络不可达。请检查网络连接。"),

    # 磁盘/文件错误
    (re.compile(r"No space left|ENOSPC", re.I),
     "disk_full",
     "磁盘空间不足，无法继续录制。请清理输出目录。"),
    (re.compile(r"permission denied|EACCES", re.I),
     "permission_denied",
     "权限不足。请检查输出目录的写入权限。"),
    (re.compile(r"Read-only file system|EROFS", re.I),
     "readonly_fs",
     "文件系统只读。请检查输出目录。"),
    (re.compile(r"File name too long|ENAMETOOLONG", re.I),
     "filename_too_long",
     "文件名过长。请缩短房间名称或输出路径。"),

    # FFmpeg 错误
    (re.compile(r"ffmpeg.*error|ffmpeg.*failed", re.I),
     "ffmpeg_error",
     "录制引擎出错。请检查编码器设置或重启应用。"),
    (re.compile(r"Invalid data found|corrupt", re.I),
     "invalid_data",
     "直播流数据异常。可能需要切换清晰度或重试。"),
    (re.compile(r"Stream not found|No stream", re.I),
     "no_stream",
     "未找到直播流。主播可能已下播。"),

    # 平台特定错误
    (re.compile(r"Cookie|cookie.*invalid|cookie.*expired", re.I),
     "cookie_invalid",
     "Cookie 无效或已过期。请更新浏览器 Cookie。"),
    (re.compile(r"login|登录|认证", re.I),
     "login_required",
     "需要登录才能访问。请检查 Cookie 或登录状态。"),
    (re.compile(r"region|地区|geo.?block", re.I),
     "region_blocked",
     "该直播在当前地区不可用。可能需要使用代理。"),
]


def humanize_error(raw: str) -> str:
    """返回用户友好的错误描述。未匹配则返回原文（加前缀）。"""
    if not raw:
        return ""
    for pattern, _code, msg in _PATTERNS:
        if pattern.search(raw):
            return msg
    return f"发生错误：{raw}"


def get_error_code(raw: str) -> str:
    """返回错误代码。未匹配则返回 'unknown'。"""
    if not raw:
        return ""
    for pattern, code, _msg in _PATTERNS:
        if pattern.search(raw):
            return code
    return "unknown"


def is_recoverable_error(raw: str) -> bool:
    """判断错误是否可恢复（适合自动重连）。"""
    code = get_error_code(raw)
    # 这些错误通常是暂时性的，可以尝试重连
    recoverable_codes = {
        "timeout", "conn_timeout", "conn_reset", "conn_refused",
        "network_unreachable", "no_stream", "invalid_data",
        "rate_limited", "server_error",
    }
    return code in recoverable_codes


def get_retry_suggestion(raw: str) -> str:
    """返回重试建议。"""
    code = get_error_code(raw)
    suggestions = {
        "timeout": "建议：检查网络连接，或切换到更稳定的网络。",
        "conn_timeout": "建议：服务器响应慢，可稍后重试。",
        "conn_reset": "建议：网络不稳定，可尝试重连。",
        "conn_refused": "建议：服务器可能维护中，请稍后重试。",
        "dns_error": "建议：检查网络连接或 DNS 设置。",
        "disk_full": "建议：清理磁盘空间或更改输出目录。",
        "permission_denied": "建议：以管理员身份运行或更改输出目录。",
        "cookie_invalid": "建议：更新浏览器 Cookie。",
        "login_required": "建议：检查登录状态或更新 Cookie。",
        "region_blocked": "建议：使用代理或 VPN。",
        "rate_limited": "建议：等待几分钟后重试。",
        "server_error": "建议：服务器异常，请稍后重试。",
    }
    return suggestions.get(code, "")
