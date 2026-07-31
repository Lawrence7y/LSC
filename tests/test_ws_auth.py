import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python-backend"))

from ws_auth import (
    extract_token_from_path,
    is_origin_allowed,
    is_ws_token_required,
    validate_ws_token,
)


def test_origin_localhost_ok():
    assert is_origin_allowed("http://localhost:5173") is True
    assert is_origin_allowed("http://127.0.0.1:9876") is True
    assert is_origin_allowed("null") is True


def test_origin_prefix_bypass_rejected():
    assert is_origin_allowed("http://localhost.attacker.com") is False
    assert is_origin_allowed("http://127.0.0.1.evil.com") is False
    assert is_origin_allowed("https://example.com") is False
    assert is_origin_allowed("") is False


def test_extract_token_from_path():
    assert extract_token_from_path("/?token=abc") == "abc"
    assert extract_token_from_path("/?token=abc&x=1") == "abc"
    assert extract_token_from_path("/") is None


def test_validate_token(monkeypatch):
    monkeypatch.setenv("LSC_WS_TOKEN", "secret-token-value")
    monkeypatch.setenv("LSC_WS_TOKEN_REQUIRED", "1")
    assert validate_ws_token("secret-token-value") is True
    assert validate_ws_token("wrong") is False
    assert validate_ws_token("") is False


def test_token_not_required(monkeypatch):
    monkeypatch.setenv("LSC_WS_TOKEN_REQUIRED", "0")
    assert is_ws_token_required() is False
    assert validate_ws_token("") is True


def test_validate_token_required_but_unset(monkeypatch):
    monkeypatch.setenv("LSC_WS_TOKEN_REQUIRED", "1")
    monkeypatch.delenv("LSC_WS_TOKEN", raising=False)
    assert validate_ws_token("anything") is False
