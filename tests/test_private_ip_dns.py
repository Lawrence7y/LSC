"""SSRF private-IP checks must not treat DNS failure as an internal address."""
from __future__ import annotations

import socket

import pytest

from lsc.platforms.base import _is_private_ip


def test_dns_failure_is_not_private_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(host, *args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "getaddrinfo failed")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert _is_private_ip("live.douyin.com") is False


def test_literal_loopback_is_private() -> None:
    assert _is_private_ip("127.0.0.1") is True


def test_douyin_script_dns_failure_is_not_private(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.douyin_record as douyin_record

    def boom(host, *args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "getaddrinfo failed")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert douyin_record._is_private_ip("live.douyin.com") is False
