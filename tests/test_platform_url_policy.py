from __future__ import annotations

from lsc.platforms.credentials import CredentialContext, CredentialStatus
from lsc.platforms.direct import DirectAdapter
from lsc.platforms.generic import GenericPageAdapter
from lsc.platforms.url_policy import validate_public_url, validate_redirect_chain


def test_url_policy_rejects_private_and_metadata_targets():
    for url in (
        "http://127.0.0.1/live.flv",
        "http://10.0.0.1/live.flv",
        "http://169.254.169.254/latest/meta-data",
        "http://localhost/live.flv",
        "http://service.localhost/live.flv",
        "http://metadata.google.internal/live.flv",
    ):
        assert validate_public_url(url)[0] is False
        assert DirectAdapter().can_handle(url) is False


def test_url_policy_accepts_public_stream_url():
    assert validate_public_url("https://cdn.example/live.flv")[0] is True
    assert DirectAdapter().can_handle("https://cdn.example/live.flv") is True


def test_redirect_policy_rejects_private_target_before_network_open():
    safe, reason = validate_redirect_chain("http://127.0.0.1/live.flv")

    assert safe is False
    assert reason


def test_redirect_policy_checks_each_location_without_following_unsafe_target(monkeypatch):
    import lsc.platforms.url_policy as policy

    class Response:
        def __init__(self, status, location=""):
            self.status = status
            self.headers = {"Location": location} if location else {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Opener:
        def __init__(self):
            self.calls = 0

        def open(self, _request, timeout):
            self.calls += 1
            return Response(302, "http://127.0.0.1/metadata")

    opener = Opener()
    monkeypatch.setattr(policy, "build_opener", lambda *_handlers: opener)

    safe, reason = policy.validate_redirect_chain("https://public.example/live.flv")

    assert safe is False
    assert reason
    assert opener.calls == 1


def test_generic_does_not_return_private_media_url():
    html = '<video src="http://127.0.0.1/live.m3u8"></video>'
    assert GenericPageAdapter()._find_stream_url(html) == ""


def test_generic_context_uses_scoped_proxy_timeout_and_headers(monkeypatch):
    seen = {}

    def fake_fetch(url, **kwargs):
        seen.update(kwargs)
        return '<title>Live</title>' + ('x' * 100) + 'https://cdn.example/live.m3u8'

    monkeypatch.setattr("lsc.platforms.generic.fetch_url", fake_fetch)
    result = GenericPageAdapter().parse_with_context(
        "https://page.example/live",
        CredentialContext(
            platform="generic",
            purpose="RESOLVE",
            status=CredentialStatus.AVAILABLE,
            headers={"Referer": "https://page.example/"},
            network_context={"proxy_url": "http://proxy.example:8080", "timeout_sec": 4},
        ),
    )

    assert not result.error
    assert seen["proxy_url"] == "http://proxy.example:8080"
    assert seen["timeout"] == 4
    assert seen["headers"]["Referer"] == "https://page.example/"


def test_safe_redirect_handler_validates_url_not_header_blob(monkeypatch):
    from urllib.request import Request

    from lsc.platforms.base import _SafeRedirectHandler

    seen: list[str] = []

    monkeypatch.setattr(
        "lsc.platforms.base._validate_network_url",
        lambda url: seen.append(str(url)),
    )
    monkeypatch.setattr(
        "urllib.request.HTTPRedirectHandler.redirect_request",
        lambda self, req, fp, code, msg, headers, newurl: Request(newurl),
    )
    handler = _SafeRedirectHandler()
    headers = {"Location": "https://cdn.example/live.flv", "Server": "Tengine"}
    new_req = handler.redirect_request(
        Request("https://tx.example/from.flv"),
        None,
        302,
        "Found",
        headers,
        "https://cdn.example/live.flv",
    )

    assert seen == ["https://cdn.example/live.flv"]
    assert new_req.full_url == "https://cdn.example/live.flv"
