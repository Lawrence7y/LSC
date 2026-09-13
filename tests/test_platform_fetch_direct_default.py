"""平台抓取默认直连：系统代理（env/注册表）不得再被隐式采用。

2026-09-13 真机事故：本机注册表系统代理指向 127.0.0.1:8780 但代理进程未运行 ⇒
`build_opener` 默认 ProxyHandler 与 `douyin_record._SSRF_SAFE_OPENER` 的
`ProxyHandler(getproxies())` 都隐式吃到死代理 ⇒ 虎牙/抖音全部解析报
[WinError 10061]（错误文案是"网络错误"，极易误判为房间/网络问题）。

统一口径（与显式 scoped proxy 契约一致）：
- 默认 opener **直连**，不读 env/注册表代理；
- 仅当调用方显式传入 `proxy_url`（network_context 作用域代理）才走代理。

红线：显式 scoped 代理路径必须保持可用（测试 3 是正向对照，不得被"顺手删掉"）。
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import urllib.request
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "python-backend"
_REPO_ROOT = Path(__file__).resolve().parents[1]

SENTINEL_PROXIES = {"http": "http://127.0.0.1:9", "https": "http://127.0.0.1:9"}


def _proxy_handlers_with_values(opener) -> list[dict]:
    out = []
    for handler in opener.handlers:
        if isinstance(handler, urllib.request.ProxyHandler) and handler.proxies:
            out.append(dict(handler.proxies))
    return out


def test_base_default_opener_ignores_system_proxy(monkeypatch):
    """lsc.platforms.base 的默认 opener 必须直连（改前隐式吃系统代理）。"""
    monkeypatch.setattr(urllib.request, "getproxies", lambda: dict(SENTINEL_PROXIES))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    import lsc.platforms.base as base

    try:
        importlib.reload(base)
        assert _proxy_handlers_with_values(base._SAFE_OPENER) == [], (
            "默认 opener 携带了系统代理，死代理会拖垮全平台解析"
        )
        # 正向对照：显式 scoped 代理仍必须生效
        scoped = base._opener_for_proxy("http://127.0.0.1:9")
        assert _proxy_handlers_with_values(scoped) == [dict(SENTINEL_PROXIES)]
    finally:
        importlib.reload(base)


def test_douyin_record_default_opener_ignores_system_proxy(monkeypatch):
    """scripts/douyin_record 的 SSRF 安全 opener 必须直连（改前显式吃系统代理）。"""
    monkeypatch.setattr(urllib.request, "getproxies", lambda: dict(SENTINEL_PROXIES))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    script_path = _REPO_ROOT / "scripts" / "douyin_record.py"
    spec = importlib.util.spec_from_file_location("douyin_record_test", str(script_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert _proxy_handlers_with_values(module._SSRF_SAFE_OPENER) == [], (
        "douyin 默认 opener 携带了系统代理，死代理会拖垮抖音解析"
    )
    # 显式 scoped 代理仍必须生效（getattr 探测：入口尚未存在时红在缺入口）
    build_scoped = getattr(module, "_build_scoped_opener", None)
    assert build_scoped is not None, "缺少 _build_scoped_opener（scoped 代理构建入口）"
    scoped = build_scoped("http://127.0.0.1:9")
    assert _proxy_handlers_with_values(scoped) == [dict(SENTINEL_PROXIES)]
