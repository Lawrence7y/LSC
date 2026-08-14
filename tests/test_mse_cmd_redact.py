from __future__ import annotations

from lsc.core.services.mse_streamer import _redact_ffmpeg_cmd
from lsc.platforms.redaction import redact_text


def test_redact_ffmpeg_cmd_hides_headers_value() -> None:
    cmd = [
        "ffmpeg",
        "-headers",
        "Referer: https://example.com\r\nCookie: sessid=secret123\r\n",
        "-i",
        "https://stream.example/live.m3u8",
    ]
    redacted = _redact_ffmpeg_cmd(cmd)
    assert redacted == [
        "ffmpeg",
        "-headers",
        "<redacted>",
        "-i",
        "https://stream.example/live.m3u8",
    ]


def test_redact_ffmpeg_cmd_hides_inline_cookie() -> None:
    cmd = ["ffmpeg", "-user_agent", "LSC", "Cookie: token=abc"]
    redacted = _redact_ffmpeg_cmd(cmd)
    assert redacted == ["ffmpeg", "-user_agent", "LSC", "<redacted>"]


def test_redact_ffmpeg_cmd_hides_authorization() -> None:
    cmd = ["ffmpeg", "Authorization: Bearer secret-token", "-i", "rtmp://x"]
    redacted = _redact_ffmpeg_cmd(cmd)
    assert redacted[0] == "ffmpeg"
    assert redacted[1] == "<redacted>"
    assert redacted[2] == "-i"


def test_redact_ffmpeg_cmd_preserves_safe_args() -> None:
    cmd = ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-f", "mp4", "pipe:1"]
    assert _redact_ffmpeg_cmd(cmd) == cmd


def test_redact_text_hides_complete_escaped_cookie_header() -> None:
    detail = (
        "Command [... '-headers', "
        "'Referer: https://live.example/\\r\\n"
        "Cookie: SESSDATA=secret; bili_jct=another-secret; foo=bar\\r\\n']"
    )
    redacted = redact_text(detail)
    assert "secret" not in redacted
    assert "another-secret" not in redacted
    assert "foo=bar" not in redacted
    assert "Cookie: <redacted>" in redacted
