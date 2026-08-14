"""Credential provider abstraction backed by the existing cookie helper."""
from __future__ import annotations

import enum
import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from .cookie_helper import (
    cookies_to_header,
    get_bilibili_cookies,
    get_douyin_cookies,
    get_huya_cookies,
)
from .redaction import redact_headers

_log = logging.getLogger(__name__)

_CREDENTIAL_PURPOSES = frozenset({"RESOLVE", "PROBE", "CONNECT"})


class CredentialStatus(str, enum.Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    AVAILABLE = "AVAILABLE"
    EXPIRING = "EXPIRING"
    EXPIRED = "EXPIRED"
    INVALID = "INVALID"
    INTERACTION_REQUIRED = "INTERACTION_REQUIRED"


@dataclass(frozen=True, slots=True)
class CredentialContext:
    """Short-lived, purpose-scoped credentials for one media request."""

    platform: str
    account_ref: str = "default"
    purpose: str = "RESOLVE"
    status: CredentialStatus = CredentialStatus.NOT_CONFIGURED
    headers: Mapping[str, str] = field(default_factory=dict)
    fetched_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    source: str = ""
    # Non-secret transport scope used by platform candidate quarantine and
    # probe/connect parity.  It is deliberately excluded from repr/compare;
    # credentials remain the only material exposed through this context.
    network_context: Mapping[str, object] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    # Per-request control signals are carried alongside the scoped context so
    # adapters can stop before issuing another page/API request.  They are
    # intentionally excluded from repr/compare and never serialized.
    deadline_monotonic: float | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    cancellation: Any | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        purpose = str(self.purpose or "RESOLVE").strip().upper()
        if purpose not in _CREDENTIAL_PURPOSES:
            raise ValueError(
                "credential purpose must be one of RESOLVE, PROBE or CONNECT"
            )
        object.__setattr__(self, "purpose", purpose)

    @property
    def available(self) -> bool:
        return self.status in {
            CredentialStatus.AVAILABLE,
            CredentialStatus.EXPIRING,
        }

    @property
    def network_profile(self) -> str:
        value = self.network_context.get("profile") or self.network_context.get("proxy_url")
        return str(value or "")

    def redacted(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "account_ref": self.account_ref,
            "purpose": self.purpose,
            "status": self.status.value,
            "headers": redact_headers(self.headers),
            "fetched_at": self.fetched_at,
            "expires_at": self.expires_at,
            "source": self.source,
        }


class CredentialProvider(Protocol):
    """Provider protocol shared by resolvers and platform adapters."""

    def get_status(
        self,
        platform: str,
        account_ref: str = "default",
    ) -> CredentialStatus:
        ...

    def get_context(
        self,
        platform: str,
        account_ref: str = "default",
        purpose: str = "RESOLVE",
    ) -> CredentialContext:
        ...

    def refresh(
        self,
        platform: str,
        account_ref: str = "default",
    ) -> CredentialContext:
        ...

    def invalidate(
        self,
        platform: str,
        account_ref: str = "default",
        reason: str = "",
    ) -> None:
        ...

    def redact(self, value: object) -> str:
        ...


class LegacyCredentialProvider:
    """Compatibility provider using the repository's existing cookie sources.

    It deliberately does not introduce a second persistence mechanism. The
    existing environment/file/browser lookup remains the source of truth while
    callers receive a short-lived, purpose-scoped context.
    """

    _COOKIE_PLATFORMS = {
        "bilibili": get_bilibili_cookies,
        "douyin": get_douyin_cookies,
        "huya": get_huya_cookies,
    }

    def __init__(self) -> None:
        self._invalidated: set[tuple[str, str]] = set()
        self._state_lock = threading.RLock()

    @classmethod
    def _load_headers(cls, platform: str) -> tuple[dict[str, str], str]:
        loader = cls._COOKIE_PLATFORMS.get(platform.lower())
        if loader is None:
            return {}, "none"
        try:
            cookies = loader()
        except Exception as exc:
            _log.warning(
                "credential source unavailable platform=%s: %s",
                platform,
                redact_headers({"error": str(exc)})["error"],
            )
            cookies = {}
        if not cookies:
            return {}, "cookie_helper"
        return {"Cookie": cookies_to_header(cookies)}, "cookie_helper"

    def get_status(
        self,
        platform: str,
        account_ref: str = "default",
    ) -> CredentialStatus:
        key = (str(platform or "").lower(), str(account_ref or "default"))
        with self._state_lock:
            invalidated = key in self._invalidated
        if invalidated:
            return CredentialStatus.INVALID
        headers, _ = self._load_headers(key[0])
        if headers:
            return CredentialStatus.AVAILABLE
        try:
            from .capabilities import get_platform_capabilities

            capabilities = get_platform_capabilities(key[0])
            requires_credentials = bool(capabilities.credential_kinds) and not bool(
                capabilities.supports_anonymous
            )
        except Exception:
            requires_credentials = key[0] in self._COOKIE_PLATFORMS
        if requires_credentials:
            return CredentialStatus.NOT_CONFIGURED
        return CredentialStatus.AVAILABLE

    def get_context(
        self,
        platform: str,
        account_ref: str = "default",
        purpose: str = "RESOLVE",
    ) -> CredentialContext:
        normalized = str(platform or "").strip().lower()
        normalized_account = str(account_ref or "default")
        status = self.get_status(normalized, normalized_account)
        headers: dict[str, str] = {}
        source = "none"
        if status != CredentialStatus.INVALID:
            headers, source = self._load_headers(normalized)
            # Invalidation may race the cookie-file/browser read.  Re-check
            # after I/O so a context returned after an explicit revoke cannot
            # carry stale credentials into probe/connect.
            with self._state_lock:
                if (normalized, normalized_account) in self._invalidated:
                    status = CredentialStatus.INVALID
                    headers = {}
                    source = "invalidated"
        return CredentialContext(
            platform=normalized,
            account_ref=normalized_account,
            purpose=str(purpose or "RESOLVE").upper(),
            status=status,
            headers=headers,
            source=source,
        )

    def refresh(
        self,
        platform: str,
        account_ref: str = "default",
    ) -> CredentialContext:
        with self._state_lock:
            self._invalidated.discard(
                (str(platform or "").lower(), str(account_ref or "default"))
            )
        return self.get_context(platform, account_ref, "RESOLVE")

    def invalidate(
        self,
        platform: str,
        account_ref: str = "default",
        reason: str = "",
    ) -> None:
        del reason
        with self._state_lock:
            self._invalidated.add(
                (str(platform or "").lower(), str(account_ref or "default"))
            )

    @staticmethod
    def redact(value: object) -> str:
        from .redaction import redact_text

        return redact_text(value)


_default_provider: LegacyCredentialProvider | None = None
_default_provider_lock = threading.Lock()


def get_default_credential_provider() -> LegacyCredentialProvider:
    global _default_provider
    if _default_provider is None:
        with _default_provider_lock:
            if _default_provider is None:
                _default_provider = LegacyCredentialProvider()
    return _default_provider


def has_usable_credentials(
    platform: str,
    account_ref: str = "default",
) -> bool:
    """Return only whether a scoped credential context has request headers.

    Callers such as preview-quality policy must not import ``cookie_helper``
    or inspect a plaintext Cookie value.  Keeping this boolean projection in
    the credential layer preserves that boundary while retaining compatibility
    with the existing local cookie sources.
    """
    try:
        context = get_default_credential_provider().get_context(
            platform,
            account_ref,
            "CONNECT",
        )
        return bool(context.headers)
    except Exception as exc:
        _log.debug(
            "credential availability check failed platform=%s: %s",
            platform,
            redact_headers({"error": str(exc)})["error"],
        )
        return False


__all__ = [
    "CredentialContext",
    "CredentialProvider",
    "CredentialStatus",
    "LegacyCredentialProvider",
    "get_default_credential_provider",
    "has_usable_credentials",
]
