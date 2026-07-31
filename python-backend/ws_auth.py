"""WebSocket Origin + token helpers for LSC backend."""
from __future__ import annotations

import hmac
import os
from urllib.parse import parse_qs, urlparse

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_origin_allowed(origin: str) -> bool:
    if not origin:
        return False
    # Electron 以 loadFile() 加载界面时，Chromium 可能发送 ``null``
    # 或 ``file://``；两者均为本地打包界面的合法来源。
    if origin in {"null", "file://"}:
        return True
    try:
        parsed = urlparse(origin)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    return host in _LOCAL_HOSTS


def is_ws_token_required() -> bool:
    return os.environ.get("LSC_WS_TOKEN_REQUIRED", "1").strip() not in ("0", "false", "False", "no")


def expected_ws_token() -> str:
    return os.environ.get("LSC_WS_TOKEN", "") or ""


def extract_token_from_path(path: str | None) -> str | None:
    if not path:
        return None
    if "://" in path:
        query = urlparse(path).query
    elif "?" in path:
        query = path.split("?", 1)[1]
    else:
        query = urlparse(f"ws://x{path}").query
    vals = parse_qs(query).get("token") or []
    return vals[0] if vals else None


def validate_ws_token(provided: str | None) -> bool:
    if not is_ws_token_required():
        return True
    expected = expected_ws_token()
    if not expected:
        return False
    return hmac.compare_digest(provided or "", expected)
