"""CDN 流地址过期检测：只认具名参数，禁止把抖音其它 query 当成即将过期。"""
from __future__ import annotations

import time
from urllib.parse import urlencode

from lsc.core.orchestrator import (
    _is_stream_url_expiring,
    _should_proactively_refresh_stream,
)

def _now() -> float:
    return time.time()


def _douyin_flv(**params: object) -> str:
    return (
        "http://pull-flv-q1.douyincdn.com/thirdgame/"
        "stream-696467670485959486_or4.flv?"
        + urlencode({str(k): str(v) for k, v in params.items()})
    )


def test_douyin_url_with_near_now_junk_params_is_not_expiring() -> None:
    """回归：扫描全部 query 会把 volcTime / 短 hex 误判成即将过期，导致十几秒重连一次。"""
    url = _douyin_flv(
        expire=int(_now() + 24 * 3600),
        sign="abcdef0123456789abcdef0123456789",
        volcTime=int(_now()),
        l_og=format(int(_now()), "x"),
    )
    assert _is_stream_url_expiring(url) is False


def test_named_expire_within_threshold_is_expiring() -> None:
    url = _douyin_flv(expire=int(_now() + 30))
    assert _is_stream_url_expiring(url) is True


def test_already_past_expire_does_not_force_proactive_refresh() -> None:
    """CDN 常在 expire 过后仍供流；正在写入的录制不得因此被拆掉。"""
    url = _douyin_flv(expire=int(_now() - 120))
    assert _is_stream_url_expiring(url) is False


def test_expire_equal_to_now_is_issue_time_not_ttl() -> None:
    url = _douyin_flv(expire=int(_now()))
    assert _is_stream_url_expiring(url) is False


def test_huya_wstime_hex_within_threshold_is_expiring() -> None:
    ts = int(_now() + 20)
    url = f"https://tx.flv.huya.com/src/live.flv?wsSecret=abc&wsTime={ts:x}"
    assert _is_stream_url_expiring(url) is True


def test_bilibili_expires_hours_away_is_not_expiring() -> None:
    url = (
        "https://cn-live.bilivideo.com/live-bvc/x.flv"
        f"?expires={int(_now() + 3600)}"
    )
    assert _is_stream_url_expiring(url) is False


def test_empty_url_is_not_expiring() -> None:
    assert _is_stream_url_expiring("") is False
    assert _is_stream_url_expiring("http://cdn.example/live.flv") is False


def test_freshly_parsed_short_ttl_url_skips_proactive_refresh() -> None:
    url = _douyin_flv(expire=int(_now() + 30))
    assert _is_stream_url_expiring(url) is True
    assert _should_proactively_refresh_stream(url, parsed_at=_now()) is False


def test_aged_url_in_last_minute_does_proactive_refresh() -> None:
    url = _douyin_flv(expire=int(_now() + 30))
    assert _should_proactively_refresh_stream(url, parsed_at=_now() - 3600) is True
