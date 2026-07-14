"""Cookie 解密与 HTTP 头安全过滤测试。"""
from __future__ import annotations

import json

from lsc.platforms.cookie_helper import (
    _decrypt_chrome_value,
    _is_http_header_safe,
    _sanitize_cookie_map,
    cookies_to_header,
    load_cookies_from_file,
)


def test_is_http_header_safe_rejects_replacement_char():
    assert _is_http_header_safe("ok-value") is True
    assert _is_http_header_safe("") is False
    assert _is_http_header_safe("bad\ufffdvalue") is False
    assert _is_http_header_safe("中文") is False


def test_decrypt_chrome_value_does_not_return_replacement_garbage():
    # 模拟 Chrome v20 密文：解密失败时不得产出含 \ufffd 的伪明文
    encrypted = b"v20" + bytes(range(32, 96))
    assert _decrypt_chrome_value(encrypted) == ""


def test_decrypt_chrome_value_accepts_plain_ascii():
    assert _decrypt_chrome_value(b"plain_cookie_value") == "plain_cookie_value"


def test_sanitize_cookie_map_drops_invalid_entries():
    cleaned = _sanitize_cookie_map(
        {
            "ok": "abc123",
            "bad": "x\ufffdy",
            "cn": "中文",
            1: "numkey",  # type: ignore[dict-item]
        }
    )
    assert cleaned == {"ok": "abc123"}


def test_cookies_to_header_skips_invalid_values():
    header = cookies_to_header({"a": "1", "b": "x\ufffd"})
    assert header == "a=1"
    header.encode("latin-1")


def test_parse_cookie_input_supports_json_object_array_and_header():
    from lsc.platforms.cookie_helper import parse_cookie_input

    assert parse_cookie_input('{"ttwid":"abc","sessionid":"s1"}') == {
        "ttwid": "abc",
        "sessionid": "s1",
    }
    assert parse_cookie_input(
        '[{"name":"ttwid","value":"abc"},{"name":"sessionid","value":"s1"}]'
    ) == {"ttwid": "abc", "sessionid": "s1"}
    assert parse_cookie_input("ttwid=abc; sessionid=s1") == {
        "ttwid": "abc",
        "sessionid": "s1",
    }


def test_load_cookies_from_file_sanitizes_json(tmp_path):
    path = tmp_path / "douyin.json"
    path.write_text(
        json.dumps({"ttwid": "good", "broken": "bad\ufffd"}, ensure_ascii=False),
        encoding="utf-8",
    )
    cookies = load_cookies_from_file(str(path))
    assert cookies == {"ttwid": "good"}


def test_fetch_page_cookie_header_ignores_replacement_chars(monkeypatch):
    """脏 Cookie 不得导致 urllib latin-1 编码异常。"""
    import scripts.douyin_record as douyin_record

    captured: dict[str, object] = {}

    class _FakeResp:
        def read(self) -> bytes:
            return b"<html></html>"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        captured["headers"] = dict(request.headers)
        return _FakeResp()

    monkeypatch.setattr(douyin_record, "urlopen", fake_urlopen, raising=False)
    # urlopen 是在函数内 import 的，需 patch urllib.request
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    html, err = douyin_record.fetch_page(
        "https://live.douyin.com/1",
        cookies={"ok": "1", "bad": "x\ufffd"},
    )
    assert err is None
    assert html is not None
    cookie_header = captured["headers"].get("Cookie") or captured["headers"].get("cookie")
    assert cookie_header == "ok=1"
    cookie_header.encode("latin-1")
