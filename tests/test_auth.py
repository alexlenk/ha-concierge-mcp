"""Tests for guest-secret generation and verification.

The last test in this file is the regression test the design document
calls out explicitly: a request bearing a real, valid-shaped Home
Assistant access token must be rejected, proving this code path never
falls back to ``hass.auth``.
"""
from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.concierge_mcp import auth
from custom_components.concierge_mcp.const import DOMAIN


def _entry(secret: str) -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, data={"secret": secret}, options={"entities": []})


async def test_generate_secret_is_not_empty_or_predictable() -> None:
    first = auth.generate_secret()
    second = auth.generate_secret()

    assert first
    assert len(first) >= 32
    assert first != second


async def test_verify_secret_accepts_correct_bearer_token() -> None:
    entry = _entry("correct-secret")
    assert auth.verify_secret(entry, "Bearer correct-secret") is True


async def test_verify_secret_rejects_missing_header() -> None:
    entry = _entry("correct-secret")
    assert auth.verify_secret(entry, None) is False


async def test_verify_secret_rejects_wrong_secret() -> None:
    entry = _entry("correct-secret")
    assert auth.verify_secret(entry, "Bearer wrong-secret") is False


async def test_verify_secret_rejects_malformed_header() -> None:
    entry = _entry("correct-secret")
    assert auth.verify_secret(entry, "correct-secret") is False
    assert auth.verify_secret(entry, "Basic correct-secret") is False


async def test_verify_secret_rejects_when_entry_has_no_secret() -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={"entities": []})
    assert auth.verify_secret(entry, "Bearer anything") is False


async def test_verify_secret_rejects_a_home_assistant_style_access_token() -> None:
    """A HA long-lived access token must never grant access to this endpoint.

    This never calls ``hass.auth`` at all — verify_secret only ever
    compares against the guest secret stored on the config entry. A
    token that happens to be a real, currently-valid HA credential for an
    admin user carries no special meaning here.
    """
    entry = _entry("guest-secret")
    fake_long_lived_access_token = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJpc3MiOiJhZG1pbiIsImlhdCI6MTcwMDAwMDAwMH0."
        "9f8b6b7f0a1c2d3e4f5061728394a5b6c7d8e9f0"
    )
    assert auth.verify_secret(entry, f"Bearer {fake_long_lived_access_token}") is False


async def test_verify_secret_rejects_non_ascii_bearer_token_without_crashing() -> None:
    """hmac.compare_digest raises TypeError on non-ASCII str arguments.

    Cloudflare Access forwards its own opaque bearer token in this same
    header alongside the JWT assertion, so arbitrary client-controlled
    values reach this check during normal operation — this must degrade to
    a clean rejection, not an unhandled 500.
    """
    entry = _entry("correct-secret")
    assert auth.verify_secret(entry, "Bearer opaque-tökén-☃") is False
