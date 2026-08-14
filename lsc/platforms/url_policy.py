"""Network URL safety policy for direct and generic adapters."""
from __future__ import annotations

import ipaddress
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.azure.internal",
    "instance-data.ec2.internal",
    "metadata",
}


def validate_public_url(url: str) -> tuple[bool, str]:
    """Reject local, loopback, link-local and cloud metadata targets."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False, "仅支持 http/https 公网地址"
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in _BLOCKED_HOSTNAMES:
        return False, "禁止访问本机或云元数据地址"
    # Cover common local/DNS aliases before any network operation. Literal IP
    # checks below handle numeric forms; these suffixes prevent a resolver
    # from bypassing the policy with names such as foo.localhost.
    if hostname.endswith((".localhost", ".local", ".internal")):
        return False, "blocked local or cloud metadata hostname"
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True, ""
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or str(address) == "169.254.169.254"
    ):
        return False, "禁止访问本地、内网、链路本地或云元数据地址"
    return True, ""


class _NoRedirectHandler(HTTPRedirectHandler):
    """Expose redirect targets without allowing urllib to open them."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_redirect_chain(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout_sec: float = 5.0,
    proxy_url: str = "",
    max_redirects: int = 5,
) -> tuple[bool, str]:
    """Validate redirect targets before handing a direct stream to FFmpeg.

    A media probe still owns HTTP status and codec validation.  This helper
    only follows redirect metadata with a one-byte GET and rejects unsafe
    schemes/loopback/private/metadata targets.  Non-redirect HTTP failures
    are returned as safe so the real probe can report the platform-specific
    failure kind instead of duplicating transport policy here.
    """
    current = str(url or "").strip()
    opener_kwargs = [_NoRedirectHandler()]
    if proxy_url:
        opener_kwargs.append(
            ProxyHandler({"http": proxy_url, "https": proxy_url})
        )
    opener = build_opener(*opener_kwargs)
    for _hop in range(max(0, int(max_redirects)) + 1):
        safe, reason = validate_public_url(current)
        if not safe:
            return False, reason
        request = Request(
            current,
            headers={
                **dict(headers or {}),
                "Range": "bytes=0-0",
                "Accept": "*/*",
            },
            method="GET",
        )
        try:
            with opener.open(request, timeout=max(0.1, float(timeout_sec))) as response:
                status = int(getattr(response, "status", 200) or 200)
                location = response.headers.get("Location", "")
        except HTTPError as exc:
            status = int(exc.code)
            location = str(exc.headers.get("Location", "") or "")
            if not 300 <= status < 400:
                return True, ""
        except (OSError, ValueError):
            # The real ffprobe/FFmpeg invocation will classify transport
            # failures; do not turn a transient preflight timeout into an
            # authorization or platform parse failure.
            return True, ""

        if not 300 <= status < 400 or not location:
            return True, ""
        current = urljoin(current, location)
    return False, "redirect chain exceeded safety limit"


__all__ = ["validate_public_url", "validate_redirect_chain"]
