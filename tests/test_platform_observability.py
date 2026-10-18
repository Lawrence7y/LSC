"""P2-2 平台失效可观测性（2026-09-18 债务收口）。

守的三件事，此前都是**静默**的：

1. `CredentialProvider.invalidate()` 全仓零调用点、`reason` 被 `del` 掉 —— 凭据
   状态永远只有"配了没"，没有"还好不好"。现在由 `mark_failed_candidate` 在观测到
   AUTH_REQUIRED / AUTH_EXPIRED 时接线，并把原因/时刻留在 provider 里。
2. 虎牙 SDK 链接改写失败会静默 `return raw`，而 raw 正是"只出一段 GOP 就 EOF"的
   那个坏形状。现在按原因各告警一次并计数。
3. 两类降级都要能从 `get_system_stats` 的 `platform_degradation` 读到。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
_backend_dir = os.path.join(ROOT, "python-backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
_tests_dir = os.path.dirname(__file__)
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)


class _Provider:
    """记录 invalidate 调用的假凭据 provider。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def invalidate(self, platform, account_ref="default", reason=""):
        self.calls.append((platform, account_ref, reason))
        return None


def test_invalidate_helper_marks_and_counts(monkeypatch):
    """凭据失效的唯一入口：标记 + 记原因 + 计数，一次做完。"""
    from lsc.platforms import diagnostics
    from lsc.platforms.credentials import invalidate_credentials_on_auth_failure
    from lsc.platforms.failure import FailureKind

    diagnostics.reset()
    provider = _Provider()

    marked = invalidate_credentials_on_auth_failure(
        provider, "douyin", "acct-7", FailureKind.AUTH_REQUIRED, reason="登录态失效"
    )

    assert marked is True
    assert provider.calls == [("douyin", "acct-7", "登录态失效")]
    assert diagnostics.degradation_snapshot()["douyin"]["credential_auth_failure"] == 1


def test_invalidate_helper_counts_three_but_warns_once(monkeypatch, caplog):

    from lsc.platforms import diagnostics
    from lsc.platforms.credentials import invalidate_credentials_on_auth_failure
    from lsc.platforms.failure import FailureKind

    diagnostics.reset()
    provider = _Provider()
    with caplog.at_level(logging.WARNING, logger="lsc.platforms.credentials"):
        for _ in range(3):
            invalidate_credentials_on_auth_failure(
                provider, "douyin", "default", FailureKind.AUTH_EXPIRED, reason="x"
            )
    snap = diagnostics.degradation_snapshot()
    assert snap["douyin"]["credential_auth_failure"] == 3
    assert len([r for r in caplog.records if "platform_degradation" in r.getMessage()]) == 1
    assert len(provider.calls) == 3


def test_non_auth_kind_is_ignored():
    """CDN 403 / 签名过期描述的是那条 URL，不是账号 —— 不能牵连凭据状态。"""
    from lsc.platforms import diagnostics
    from lsc.platforms.credentials import invalidate_credentials_on_auth_failure
    from lsc.platforms.failure import FailureKind

    diagnostics.reset()
    provider = _Provider()
    for kind in (FailureKind.CDN_FORBIDDEN, FailureKind.SIGNATURE_EXPIRED, FailureKind.OFFLINE):
        assert invalidate_credentials_on_auth_failure(
            provider, "douyin", "default", kind, reason="x"
        ) is False
    assert provider.calls == []
    assert diagnostics.degradation_snapshot() == {}


def test_marking_is_platform_agnostic_but_display_is_not():
    """标记本身不看"这个平台要不要凭据"，要不要**提示用户**才看"配过没配过"。

    判据来自既有契约：`test_platform_probe_and_resolver` 三条用例锁定
    resolve 期 direct 平台被判 AUTH_EXPIRED 也必须标记。展示层的收敛在
    `CookieStatusLine`（configured && INVALID 才显示"实测已失效"）。
    """
    from lsc.platforms import diagnostics
    from lsc.platforms.credentials import invalidate_credentials_on_auth_failure
    from lsc.platforms.failure import FailureKind

    diagnostics.reset()
    provider = _Provider()
    assert invalidate_credentials_on_auth_failure(
        provider, "direct", "default", FailureKind.AUTH_EXPIRED, reason="x"
    ) is True
    assert provider.calls == [("direct", "default", "x")]


def test_invalidate_helper_survives_broken_provider():
    """provider 抛异常不得把整条解析路径带崩。"""

    class _Bad:
        def invalidate(self, *_a, **_kw):
            raise RuntimeError("provider down")

    from lsc.platforms import diagnostics
    from lsc.platforms.credentials import invalidate_credentials_on_auth_failure
    from lsc.platforms.failure import FailureKind

    diagnostics.reset()
    assert invalidate_credentials_on_auth_failure(
        _Bad(), "douyin", "default", FailureKind.AUTH_REQUIRED, reason="x"
    ) is False
    # 标记没做成，但降级仍然计数
    assert diagnostics.degradation_snapshot()["douyin"]["credential_auth_failure"] == 1


def test_mark_failed_candidate_wires_auth_kind(monkeypatch):
    """端到端：ingest 期真实入口也必须把 AUTH_* 落到同一个凭据标记入口。

    这条正是本次收口新增的那一半 —— resolve 期早有、ingest 期此前漏接。
    """
    from lsc.platforms import diagnostics, recovery_policy

    diagnostics.reset()
    provider = _Provider()
    monkeypatch.setattr(
        recovery_policy, "get_default_credential_provider", lambda: provider
    )
    info = SimpleNamespace(
        platform="douyin",
        stream_url="https://example.com/live.m3u8",
        raw={"account_ref": "acct-9"},
    )

    recovery_policy.mark_failed_candidate(info, "需要登录凭证，请配置该平台登录凭据")

    assert provider.calls and provider.calls[0][0] == "douyin"
    assert provider.calls[0][1] == "acct-9"
    assert "登录" in provider.calls[0][2]
    assert diagnostics.degradation_snapshot()["douyin"]["credential_auth_failure"] == 1


def test_huya_sdk_fallback_is_counted_and_returns_raw():
    from lsc.platforms import diagnostics, huya

    diagnostics.reset()
    raw = "wsSecret=abc&wsTime=123&ctype=huya_live"
    assert huya.build_huya_live_query(raw, "room-1") == raw
    snap = diagnostics.degradation_snapshot()
    assert snap["huya"]["sdk_query_fallback"] == 1

    huya.build_huya_live_query(raw, "room-2")
    assert diagnostics.degradation_snapshot()["huya"]["sdk_query_fallback"] == 2


def test_huya_empty_anti_code_stays_silent():
    """空 anti_code 不是降级（本来就没有可改写的东西），不得计数。"""
    from lsc.platforms import diagnostics, huya

    diagnostics.reset()
    assert huya.build_huya_live_query("", "room-1") == ""
    assert "huya" not in diagnostics.degradation_snapshot()


def test_system_stats_extra_carries_platform_degradation():
    """`_system_stats_extra` 是周期广播与按需查询共用的唯一构造点。"""
    from handlers import room_handler

    from lsc.platforms import diagnostics

    diagnostics.reset()
    diagnostics.note_degradation("bilibili", "parser_stale", reason="KeyError")
    extra = room_handler._system_stats_extra()
    assert extra["platform_degradation"]["bilibili"]["parser_stale"] == 1
    assert "ffmpeg_budget" in extra

    source = (ROOT / "python-backend" / "handlers" / "room_handler.py").read_text(
        encoding="utf-8"
    )
    # 两条出口都必须走同一个构造点，否则顶栏周期刷新会漏字段
    assert source.count("_system_stats_extra(bridge)") == 2


def test_credential_status_route_exposes_liveness(monkeypatch):
    from handlers import credential_handlers

    class _Prov:
        def get_status(self, platform, account_ref="default"):
            return SimpleNamespace(value="INVALID")

        def invalidation_info(self, platform, account_ref="default"):
            return {"invalidated_at": 1234.5, "invalidated_reason": "AUTH_EXPIRED"}

    monkeypatch.setattr(credential_handlers, "get_default_credential_provider", lambda: _Prov())
    out = credential_handlers._credential_liveness(
        "huya", SimpleNamespace(debug=lambda *a: None)
    )
    assert out == {
        "credential_status": "INVALID",
        "credential_invalidated_at": 1234.5,
        "credential_invalidated_reason": "AUTH_EXPIRED",
    }


def test_credential_liveness_failure_degrades_to_empty(monkeypatch):
    """provider 抛异常时不得把整条状态查询带崩 —— 返回空 dict 即可。"""
    from handlers import credential_handlers

    def _boom():
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(credential_handlers, "get_default_credential_provider", _boom)
    assert credential_handlers._credential_liveness(
        "douyin", SimpleNamespace(debug=lambda *a: None)
    ) == {}


def test_invalidate_keeps_reason_and_timestamp():
    """`invalidate()` 曾把 reason 直接 `del` 掉；现在必须可查。"""
    from lsc.platforms.credentials import CredentialStatus, LegacyCredentialProvider

    provider = LegacyCredentialProvider()
    provider._load_headers = lambda _p: ({"Cookie": "a=b"}, "cookie_helper")  # type: ignore[method-assign]
    assert provider.get_status("huya") is CredentialStatus.AVAILABLE

    provider.invalidate("huya", "default", reason="AUTH_EXPIRED")
    assert provider.get_status("huya") is CredentialStatus.INVALID
    info = provider.invalidation_info("huya")
    assert info["invalidated_reason"] == "AUTH_EXPIRED"
    assert info["invalidated_at"] > 0

    provider.refresh("huya")
    assert provider.get_status("huya") is CredentialStatus.AVAILABLE
    assert provider.invalidation_info("huya") == {}


# ── P2-2c 平台自检路由 ─────────────────────────────────────────────


def _register_selfcheck():
    from handlers import selfcheck_handlers
    from test_clip_refine_state import _FakeServer

    server = _FakeServer()

    def _validate(raw):
        # 与 room_handler._room_url_shape 同签名的桩：只接受 https 前缀
        url = str(raw or "").strip()
        if not url:
            return url, "请输入直播间链接"
        if not url.startswith("https://"):
            return url, "仅支持 http:// 或 https:// 直播间链接"
        return url, None

    selfcheck_handlers.register_selfcheck_handlers(
        server, log=SimpleNamespace(info=lambda *a: None,
                                    warning=lambda *a: None,
                                    error=lambda *a: None),
        validate_url=_validate,
    )
    return server.handlers["run_platform_self_check"]


def test_selfcheck_rejects_bad_url_without_running_acceptance():
    import asyncio

    handler = _register_selfcheck()
    calls: list = []

    def _boom(*_a):
        calls.append(1)
        return {}

    import handlers.selfcheck_handlers as sh

    original = sh._run_self_check
    sh._run_self_check = _boom
    try:
        out = asyncio.run(handler({"source_url": "ftp://example.com/room"}))
        assert out["success"] is False
        out = asyncio.run(handler({}))
        assert out["success"] is False
        assert calls == []  # 判据没过就绝不能走到 acceptance
    finally:
        sh._run_self_check = original


def test_selfcheck_returns_report_and_releases_inflight():
    import asyncio

    import handlers.selfcheck_handlers as sh

    sh._release()
    original = sh._run_self_check
    sh._run_self_check = lambda url, quality: {"passed": True, "platform": "huya",
                                               "failures": [], "quality_seen": quality}
    try:
        handler = _register_selfcheck()
        out = asyncio.run(handler({"source_url": "https://www.huya.com/123", "quality": "原画"}))
        assert out["success"] is True
        assert out["report"]["passed"] is True
        # 结束必须释放，否则一次异常就把后续自检永久锁死
        assert sh._try_acquire() is True
        sh._release()
    finally:
        sh._run_self_check = original


def test_selfcheck_concurrent_request_is_refused():
    """连点不得堆线程 —— 第二个请求必须直接被拒。"""
    import asyncio

    import handlers.selfcheck_handlers as sh

    sh._release()
    assert sh._try_acquire() is True  # 手动占住
    try:
        handler = _register_selfcheck()
        out = asyncio.run(handler({"source_url": "https://www.huya.com/123"}))
        assert out["success"] is False
        assert "自检" in out["error"]
    finally:
        sh._release()


def test_selfcheck_releases_inflight_on_exception():
    """executor 里抛异常也必须释放锁，否则一次故障永久锁死自检。"""
    import asyncio

    import handlers.selfcheck_handlers as sh

    sh._release()
    original = sh._run_self_check

    def _raise(*_a):
        raise RuntimeError("boom")

    sh._run_self_check = _raise
    try:
        handler = _register_selfcheck()
        out = asyncio.run(handler({"source_url": "https://www.huya.com/123"}))
        assert out["success"] is False
        assert sh._try_acquire() is True
        sh._release()
    finally:
        sh._run_self_check = original


def test_selfcheck_route_is_registered_with_same_url_validator():
    """注册点必须传 room_handler 的那一条 URL 判据，不能自带一套。"""
    source = (ROOT / "python-backend" / "handlers" / "room_handler.py").read_text(
        encoding="utf-8"
    )
    assert "register_selfcheck_handlers(" in source
    assert "validate_url=_room_url_shape" in source
