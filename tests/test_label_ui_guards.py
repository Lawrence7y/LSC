"""Label UI auth / CSRF / body-size guard tests."""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_repo = Path(__file__).resolve().parents[1]
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from scripts.valorant_vision import serve_label_ui  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture()
def label_ui_server(tmp_path: Path):
    root = tmp_path / "annotate"
    root.mkdir()
    (root / "queue.json").write_text(
        json.dumps([
            {
                "id": "f1",
                "video_id": "v1",
                "rel_path": "v1/f1.jpg",
                "timestamp_sec": 0.0,
                "video_path": "",
                "split": "train",
                "source_type": "pov",
                "session_id": "",
            }
        ]),
        encoding="utf-8",
    )
    (root / "labels.json").write_text("{}", encoding="utf-8")
    frame_dir = root / "v1"
    frame_dir.mkdir()
    (frame_dir / "f1.jpg").write_bytes(b"\xff\xd8\xff\xd9")

    port = _free_port()
    token = "test-token-123"
    serve_label_ui._AUTH_TOKEN = token

    def _run() -> None:
        serve_label_ui.main([
            "--root", str(root),
            "--port", str(port),
            "--token", token,
            "--max-threads", "4",
        ])

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    serve_label_ui._SERVER_HOLDER.clear()
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        raise RuntimeError("label UI server did not start")
    try:
        yield f"http://127.0.0.1:{port}", token, root
    finally:
        for server in serve_label_ui._SERVER_HOLDER:
            server.shutdown()
        serve_label_ui._SERVER_HOLDER.clear()
        thread.join(timeout=5)


def _get(url: str, token: str | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url)
    if token is not None:
        req.add_header("X-Auth-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _post(
    url: str,
    token: str,
    body: bytes,
    origin: str | None = None,
    content_length: int | None = None,
) -> tuple[int, bytes]:
    headers = {"X-Auth-Token": token}
    if origin is not None:
        headers["Origin"] = origin
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_label_ui_requires_token(label_ui_server):
    base, _token, _root = label_ui_server
    code, _ = _get(f"{base}/")
    assert code == 401
    code, body = _get(f"{base}/", "test-token-123")
    assert code == 200
    assert b"test-token-123" in body
    code, _ = _get(f"{base}/api/queue", "test-token-123")
    assert code == 200


def test_label_ui_frame_query_auth(label_ui_server):
    base, token, root = label_ui_server
    code, body = _get(f"{base}/frame/v1/f1.jpg?auth={token}")
    assert code == 200
    assert body == b"\xff\xd8\xff\xd9"
    code, _ = _get(f"{base}/frame/v1/f1.jpg")
    assert code == 401
    code, _ = _get(f"{base}/frame/../queue.json")
    assert code == 401


def test_label_ui_rejects_cross_origin_post(label_ui_server):
    base, token, _root = label_ui_server
    code, _ = _post(
        f"{base}/api/labels",
        token,
        b"{}",
        origin="https://evil.example.com",
    )
    assert code == 403
    code, _ = _post(f"{base}/api/labels", token, b"{}", origin=None)
    assert code == 200


def test_label_ui_rejects_oversized_body(label_ui_server):
    base, token, _root = label_ui_server
    code, _ = _post(
        f"{base}/api/labels",
        token,
        b"",
        origin=None,
        content_length=serve_label_ui._MAX_BODY_BYTES + 1,
    )
    assert code == 413
