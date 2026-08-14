from __future__ import annotations

import threading

import pytest

from lsc.platforms.capabilities import all_platform_capabilities
from lsc.platforms.credentials import (
    CredentialStatus,
    LegacyCredentialProvider,
)
from lsc.platforms.models import (
    PlatformError,
    ResolveRequest,
    ResolveResult,
    StreamCandidate,
)


def test_all_builtin_platforms_have_capabilities():
    capabilities = all_platform_capabilities()
    for platform in (
        "bilibili",
        "huya",
        "kuaishou",
        "douyu",
        "xiaohongshu",
        "weibo",
        "douyin",
        "direct",
        "generic",
    ):
        assert capabilities[platform].platform_id == platform
        assert capabilities[platform].preferred_protocols


def test_legacy_credential_provider_redacts_context(monkeypatch):
    monkeypatch.setattr(
        LegacyCredentialProvider,
        "_load_headers",
        classmethod(lambda cls, platform: ({"Cookie": "SECRET"}, "test")),
    )
    provider = LegacyCredentialProvider()
    context = provider.get_context("bilibili", purpose="PROBE")
    assert context.status == CredentialStatus.AVAILABLE
    assert context.headers["Cookie"] == "SECRET"
    assert context.redacted()["headers"]["Cookie"] == "<redacted>"


def test_legacy_credential_provider_invalidation():
    provider = LegacyCredentialProvider()
    provider.invalidate("bilibili", reason="403")
    assert provider.get_status("bilibili") == CredentialStatus.INVALID
    assert provider.refresh("bilibili").status in {
        CredentialStatus.AVAILABLE,
        CredentialStatus.NOT_CONFIGURED,
    }


def test_anonymous_compatibility_platforms_do_not_report_missing_cookie():
    provider = LegacyCredentialProvider()
    for platform in (
        "bilibili",
        "huya",
        "kuaishou",
        "douyu",
        "xiaohongshu",
        "weibo",
    ):
        assert provider.get_status(platform) == CredentialStatus.AVAILABLE


def test_legacy_credential_provider_loads_huya_cookies(monkeypatch):
    monkeypatch.setitem(
        LegacyCredentialProvider._COOKIE_PLATFORMS,
        "huya",
        lambda: {"udb_uid": "1", "udb_guid": "g1"},
    )
    headers, source = LegacyCredentialProvider._load_headers("huya")
    assert source == "cookie_helper"
    assert "udb_uid=1" in headers["Cookie"]
    assert "udb_guid=g1" in headers["Cookie"]


def test_douyin_requires_interactive_cookie_when_missing(monkeypatch):
    monkeypatch.setattr(
        LegacyCredentialProvider,
        "_load_headers",
        classmethod(lambda cls, platform: ({}, "cookie_helper")),
    )
    assert LegacyCredentialProvider().get_status("douyin") == CredentialStatus.NOT_CONFIGURED


def test_credential_provider_invalidation_is_thread_safe(monkeypatch):
    monkeypatch.setattr(
        LegacyCredentialProvider,
        "_load_headers",
        classmethod(lambda cls, platform: ({}, "cookie_helper")),
    )
    provider = LegacyCredentialProvider()
    errors: list[Exception] = []

    def worker(index: int) -> None:
        try:
            if index % 2:
                provider.invalidate("douyin", account_ref=f"account-{index}")
            else:
                provider.refresh("douyin", account_ref=f"account-{index}")
            provider.get_status("douyin", account_ref=f"account-{index}")
        except Exception as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
    assert not errors


def test_credential_context_normalizes_and_validates_purpose():
    from lsc.platforms.credentials import CredentialContext

    context = CredentialContext(platform="direct", purpose="probe")
    assert context.purpose == "PROBE"
    with pytest.raises(ValueError, match="RESOLVE, PROBE or CONNECT"):
        CredentialContext(platform="direct", purpose="EXPORT")


def test_has_usable_credentials_exposes_boolean_only(monkeypatch):
    from lsc.platforms.credentials import CredentialContext, has_usable_credentials

    class Provider:
        def __init__(self, headers):
            self.headers = headers

        def get_context(self, platform, account_ref, purpose):
            assert platform == "bilibili"
            assert account_ref == "default"
            assert purpose == "CONNECT"
            return CredentialContext(platform=platform, headers=self.headers)

    monkeypatch.setattr(
        "lsc.platforms.credentials.get_default_credential_provider",
        lambda: Provider({"Cookie": "SECRET"}),
    )
    assert has_usable_credentials("bilibili") is True

    monkeypatch.setattr(
        "lsc.platforms.credentials.get_default_credential_provider",
        lambda: Provider({}),
    )
    assert has_usable_credentials("bilibili") is False


def test_resolve_models_are_safe_and_explicit():
    candidate = StreamCandidate("c1", "https://cdn/live?token=SECRET")
    result = ResolveResult(
        platform="direct",
        room_url="https://room.example/1",
        candidates=(candidate,),
        error=None,
    )
    request = ResolveRequest(source_url="https://room.example/1")
    assert request.force_refresh is False
    assert result.ok
    assert result.candidates[0].safe_url.endswith("token=%3Credacted%3E")
    assert PlatformError(code="NO_MEDIA").retryable is False
