"""Platform adapter public exports."""
from .base import (
    ERROR_OFFLINE,
    ERROR_PARSE_FAILED,
    ERROR_RESTRICTED,
    ERROR_UNSUPPORTED_URL,
    StreamInfo,
    headers_to_ffmpeg_input_args,
)
from .registry import detect_platform, parse_stream, select_quality

__all__ = [
    "ERROR_OFFLINE",
    "ERROR_PARSE_FAILED",
    "ERROR_RESTRICTED",
    "ERROR_UNSUPPORTED_URL",
    "StreamInfo",
    "detect_platform",
    "headers_to_ffmpeg_input_args",
    "parse_stream",
    "select_quality",
]
