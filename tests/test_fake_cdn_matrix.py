"""Local CDN fault matrix used by resolver/recovery contract tests."""
from __future__ import annotations

import shutil
import threading
import time
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from lsc.platforms.failure import FailureKind, classify_failure
from lsc.platforms.models import ProbeRequest, StreamCandidate
from lsc.platforms.probe import ProbeService


class _CdnHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib protocol hook
        mode = self.path.rsplit("/", 1)[-1]
        if mode == "slow":
            time.sleep(0.15)
            status = 200
        elif mode == "break":
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Content-Length", "4096")
            self.end_headers()
            self.wfile.write(b"\x47" * 188)
            self.wfile.flush()
            self.connection.shutdown(1)
            return
        else:
            status = int(mode)
        self.send_response(status)
        self.send_header("Content-Type", "video/mp2t")
        if status == 429:
            self.send_header("Retry-After", "3")
        self.end_headers()
        if status == 200:
            self.wfile.write(b"\x47" * 188)

    def log_message(self, *_args):
        return


@pytest.fixture()
def fake_cdn():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CdnHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=1)


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        ("200", None),
        ("302", None),
        ("403", FailureKind.CDN_FORBIDDEN),
        ("404", FailureKind.OFFLINE),
        ("410", FailureKind.SIGNATURE_EXPIRED),
        ("429", FailureKind.RATE_LIMITED),
        ("500", FailureKind.CONNECTION_RESET),
        ("slow", FailureKind.CONNECT_TIMEOUT),
    ],
)
def test_fake_cdn_fault_matrix(fake_cdn, route, expected):
    try:
        with urlopen(f"{fake_cdn}/{route}", timeout=0.05) as response:
            status = response.status
    except TimeoutError:
        status = None
    except HTTPError as exc:
        status = exc.code

    if route == "slow":
        assert classify_failure("connection timed out") is expected
    elif expected is None:
        assert status in {200, 302}
    else:
        assert classify_failure(None, http_status=status) is expected


def test_fake_cdn_retry_after_and_midstream_break(fake_cdn):
    with pytest.raises(HTTPError) as error:
        urlopen(f"{fake_cdn}/429", timeout=1)
    assert error.value.headers.get("Retry-After") == "3"

    with urlopen(f"{fake_cdn}/break", timeout=1) as response:
        assert response.status == 200
        assert response.read(1024).startswith(b"\x47")
        with pytest.raises((ConnectionResetError, IncompleteRead, OSError)):
            response.read()


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe is required")
@pytest.mark.parametrize(
    ("route", "expected"),
    [
        ("403", FailureKind.CDN_FORBIDDEN.value),
        ("404", FailureKind.OFFLINE.value),
        ("410", FailureKind.SIGNATURE_EXPIRED.value),
        ("429", FailureKind.RATE_LIMITED.value),
        ("500", FailureKind.CONNECTION_RESET.value),
    ],
)
def test_probe_service_classifies_http_faults(fake_cdn, route, expected):
    """ProbeService must preserve HTTP failures as typed diagnostics."""
    candidate = StreamCandidate(
        f"fault|{route}",
        f"{fake_cdn}/{route}",
        protocol="flv",
    )
    result = ProbeService(shutil.which("ffprobe") or "ffprobe").probe(
        ProbeRequest(candidate=candidate, timeout_sec=1.0)
    )
    assert not result.ok
    assert result.failure_kind == expected


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe is required")
def test_probe_service_classifies_slow_first_byte(fake_cdn):
    candidate = StreamCandidate("fault|slow", f"{fake_cdn}/slow", protocol="flv")
    result = ProbeService(shutil.which("ffprobe") or "ffprobe").probe(
        ProbeRequest(candidate=candidate, timeout_sec=0.1)
    )
    assert not result.ok
    assert result.failure_kind == FailureKind.CONNECT_TIMEOUT.value
