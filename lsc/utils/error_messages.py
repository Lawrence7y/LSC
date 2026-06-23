"""把底层错误翻译成用户可读的中文提示。"""
from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"403|Forbidden", re.I),
     "access_denied",
     "平台拒绝了连接（403）。可能主播未开播，或需要登录 Cookie。"),
    (re.compile(r"Connection refused|ECONNREFUSED", re.I),
     "conn_refused",
     "无法连接到直播服务器。请检查网络或稍后重试。"),
    (re.compile(r"No space left|ENOSPC", re.I),
     "disk_full",
     "磁盘空间不足，无法继续录制。请清理输出目录。"),
    (re.compile(r"Stream not found|No stream|404", re.I),
     "no_stream",
     "未找到直播流。主播可能已下播。"),
    (re.compile(r"timeout|timed out", re.I),
     "timeout",
     "连接超时。请检查网络或稍后重试。"),
    (re.compile(r"ffmpeg.*error|ffmpeg.*failed", re.I),
     "ffmpeg_error",
     "录制引擎出错。请检查编码器设置或重启应用。"),
    (re.compile(r"name resolution|dns|getaddrinfo", re.I),
     "dns_error",
     "无法解析服务器地址。请检查网络连接。"),
    (re.compile(r"permission denied|EACCES", re.I),
     "permission_denied",
     "权限不足。请检查输出目录的写入权限。"),
]


def humanize_error(raw: str) -> str:
    """返回用户友好的错误描述。未匹配则返回原文（加前缀）。"""
    if not raw:
        return ""
    for pattern, _code, msg in _PATTERNS:
        if pattern.search(raw):
            return msg
    return f"发生错误：{raw}"
