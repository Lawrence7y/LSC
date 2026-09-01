"""Tests for FailureKind classification (PR-2)."""
import pytest

from lsc.platforms.failure import (
    FailureKind,
    classify_failure,
    extract_retry_after,
    failure_kind_to_message,
    is_recoverable_failure,
    normalize_failure_kind,
)


def test_http_status_short_circuit():
    assert classify_failure(None, http_status=401) == FailureKind.AUTH_REQUIRED
    assert classify_failure(None, http_status=403) == FailureKind.CDN_FORBIDDEN
    assert classify_failure(None, http_status=404) == FailureKind.OFFLINE
    assert classify_failure(None, http_status=410) == FailureKind.SIGNATURE_EXPIRED
    assert classify_failure(None, http_status=412) == FailureKind.RATE_LIMITED
    assert classify_failure(None, http_status=429) == FailureKind.RATE_LIMITED
    assert classify_failure(None, http_status=503) == FailureKind.CONNECTION_RESET


def test_timeout_error_name_and_thread_exhaustion_are_retryable():
    assert classify_failure("TimeoutError") == FailureKind.CONNECT_TIMEOUT
    assert classify_failure("can't start new thread") == FailureKind.CONNECTION_RESET
    assert classify_failure("HTTP Error 412: Precondition Failed") == FailureKind.RATE_LIMITED
    assert classify_failure("Expecting value: line 1 column 1 (char 0)") == FailureKind.CONNECTION_RESET


def test_signature_expired():
    assert classify_failure("直播流链接已过期") == FailureKind.SIGNATURE_EXPIRED


def test_wrapped_403_is_cdn_forbidden_not_signature_expired():
    text = (
        "录制启动失败：直播流连接异常（流地址可能已过期，请重新连接房间）| "
        "upstream: HTTP error 403 Forbidden | Server returned 403 Forbidden"
    )
    assert classify_failure(text) == FailureKind.CDN_FORBIDDEN


def test_ffmpeg_windows_error_138_is_connect_timeout():
    text = (
        "录制启动失败：直播流无数据返回（流地址可能已过期或主播已下播） | "
        "upstream: Connection to tcp://112-85-66-46.bytefcdnrd.com:443 "
        "failed: Error number -138 occurred"
    )
    assert classify_failure(text) == FailureKind.CONNECT_TIMEOUT


def test_maybe_expired_wrapper_without_upstream_is_not_signature_expired():
    assert classify_failure(
        "录制启动失败：直播流无数据返回（流地址可能已过期或主播已下播）"
    ) != FailureKind.SIGNATURE_EXPIRED


def test_cdn_forbidden_string():
    assert classify_failure("Server returned 403 Forbidden") == FailureKind.CDN_FORBIDDEN


def test_auth_required():
    assert classify_failure("需要登录 Cookie") == FailureKind.AUTH_REQUIRED


def test_auth_expired():
    assert classify_failure("登录态已过期") == FailureKind.AUTH_EXPIRED


def test_region_restriction_and_zero_exit_are_typed():
    assert classify_failure("region restricted") == FailureKind.REGION_RESTRICTED
    assert classify_failure("ffmpeg ended with code=0") == FailureKind.CONNECTION_RESET


def test_connect_timeout():
    assert classify_failure("Connection timed out") == FailureKind.CONNECT_TIMEOUT


def test_ffmpeg_end_of_file_is_connection_reset():
    assert classify_failure(
        "[https @ 000001fb43092040] Will reconnect at 1573622 in 0 second(s), "
        "error=End of file."
    ) == FailureKind.CONNECTION_RESET


def test_ffmpeg_demux_io_error_is_connection_reset():
    assert classify_failure(
        "[in#0/flv @ 000001fb4308f000] Error during demuxing: I/O error"
    ) == FailureKind.CONNECTION_RESET


def test_dns_failure():
    assert classify_failure("getaddrinfo: Name or service not known") == FailureKind.DNS_FAILURE
    assert classify_failure("getaddrinfo failed") == FailureKind.DNS_FAILURE


def test_unsupported_codec():
    assert classify_failure("Decoder av1 not found") == FailureKind.UNSUPPORTED_CODEC


def test_no_media_and_offline():
    assert classify_failure("Stream not found") == FailureKind.NO_MEDIA
    assert classify_failure("该直播间当前未开播") == FailureKind.OFFLINE


def test_disk_full_and_permission():
    assert classify_failure("No space left on device") == FailureKind.DISK_FULL
    assert classify_failure("Permission denied (WinError 5)") == FailureKind.PERMISSION_DENIED


def test_rate_limited_string():
    assert classify_failure("Too Many Requests 429") == FailureKind.RATE_LIMITED


def test_unknown_and_empty():
    assert classify_failure("") == FailureKind.UNKNOWN
    assert classify_failure(None) == FailureKind.UNKNOWN
    assert classify_failure("some random noise") == FailureKind.UNKNOWN


def test_normalize_failure_kind_accepts_enum_and_wire_forms():
    assert normalize_failure_kind(FailureKind.AUTH_REQUIRED) == FailureKind.AUTH_REQUIRED
    assert normalize_failure_kind("AUTH_REQUIRED") == FailureKind.AUTH_REQUIRED
    assert normalize_failure_kind("FailureKind.AUTH_REQUIRED") == FailureKind.AUTH_REQUIRED
    assert normalize_failure_kind("not-a-kind") == FailureKind.UNKNOWN
    assert is_recoverable_failure("FailureKind.CONNECT_TIMEOUT") is True


@pytest.mark.parametrize(
    "kind,recoverable",
    [
        (FailureKind.SIGNATURE_EXPIRED, True),
        (FailureKind.CDN_FORBIDDEN, True),
        (FailureKind.CONNECTION_RESET, True),
        (FailureKind.CONNECT_TIMEOUT, True),
        (FailureKind.RATE_LIMITED, True),
        (FailureKind.AUTH_REQUIRED, False),
        (FailureKind.AUTH_EXPIRED, False),
        (FailureKind.OFFLINE, False),
        (FailureKind.DISK_FULL, False),
        (FailureKind.PERMISSION_DENIED, False),
        (FailureKind.UNKNOWN, False),
    ],
)
def test_recoverability(kind, recoverable):
    assert is_recoverable_failure(kind) is recoverable


def test_every_kind_has_message():
    for kind in FailureKind:
        msg = failure_kind_to_message(kind)
        assert isinstance(msg, str) and msg


def test_extract_retry_after_supports_seconds_and_http_date():
    assert extract_retry_after("HTTP 429; Retry-After: 3.5") == 3.5
    assert extract_retry_after("Retry-After: Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
