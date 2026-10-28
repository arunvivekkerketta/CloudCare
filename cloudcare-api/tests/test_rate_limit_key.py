"""Rate-limit identity must not be spoofable through X-Forwarded-For."""
import uuid

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.ratelimit import (
    enforce_forgot_password_email_limit,
    limiter,
)
from app.utils import client_ip, get_settings


def _request(peer: str, forwarded: str | None = None) -> Request:
    headers = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": (peer, 12345),
        }
    )


def test_direct_client_cannot_spoof_forwarded_header(monkeypatch):
    monkeypatch.setattr(
        get_settings(), "trusted_proxy_ips", "10.0.0.0/8"
    )
    request = _request("203.0.113.9", "198.51.100.1")
    assert client_ip(request) == "203.0.113.9"


def test_trusted_proxy_uses_rightmost_forwarded_address(monkeypatch):
    monkeypatch.setattr(
        get_settings(), "trusted_proxy_ips", "10.0.0.0/8"
    )
    request = _request("10.1.2.3", "198.51.100.1, 203.0.113.7")
    assert client_ip(request) == "203.0.113.7"


def test_changing_spoofed_leftmost_hop_does_not_change_the_key(monkeypatch):
    monkeypatch.setattr(
        get_settings(), "trusted_proxy_ips", "10.0.0.0/8"
    )
    first = client_ip(_request("10.1.2.3", "192.0.2.1, 203.0.113.7"))
    second = client_ip(_request("10.1.2.3", "192.0.2.99, 203.0.113.7"))
    assert first == second == "203.0.113.7"


def test_malformed_forwarded_value_falls_back_to_the_proxy_peer(monkeypatch):
    monkeypatch.setattr(
        get_settings(), "trusted_proxy_ips", "10.0.0.0/8"
    )
    request = _request("10.1.2.3", "198.51.100.1, not-an-ip")
    assert client_ip(request) == "10.1.2.3"


def test_forgot_password_has_a_per_email_cap():
    limiter.reset()
    email = f"limit-{uuid.uuid4().hex}@example.com"
    for _ in range(5):
        enforce_forgot_password_email_limit(email)

    with pytest.raises(HTTPException) as caught:
        enforce_forgot_password_email_limit(email)
    assert caught.value.status_code == 429
    assert "Too many reset requests" in caught.value.detail
