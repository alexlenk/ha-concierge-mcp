"""Tests for the optional Cloudflare Access JWT auth path.

The critical test here (mirroring test_auth.py's "reject a real HA
access token" regression test) is that this path stays completely inert
unless both config values are explicitly set — an operator who has never
touched this feature must not be newly exposed by its mere existence.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.concierge_mcp import cloudflare_access
from custom_components.concierge_mcp.const import (
    CONF_CF_ACCESS_AUD,
    CONF_CF_ACCESS_TEAM_DOMAIN,
    DOMAIN,
)

TEAM_DOMAIN = "myteam"
AUD = "test-aud-tag"


@pytest.fixture
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def _entry(hass, *, team_domain: str | None = None, aud: str | None = None) -> MockConfigEntry:
    options = {"entities": []}
    if team_domain is not None:
        options[CONF_CF_ACCESS_TEAM_DOMAIN] = team_domain
    if aud is not None:
        options[CONF_CF_ACCESS_AUD] = aud
    entry = MockConfigEntry(domain=DOMAIN, data={"secret": "s"}, options=options)
    entry.add_to_hass(hass)
    return entry


def _sign(private_pem: bytes, *, aud: str = AUD, exp_delta: int = 300) -> str:
    return jwt.encode(
        {"aud": aud, "sub": "operator@example.com", "exp": int(time.time()) + exp_delta},
        private_pem,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


def _patched_signing_key(public_pem: bytes):
    """Patch PyJWKClient.get_signing_key_from_jwt to skip the real network call."""
    return patch(
        "jwt.PyJWKClient.get_signing_key_from_jwt",
        return_value=SimpleNamespace(key=public_pem),
    )


async def test_get_config_returns_none_when_unset(hass) -> None:
    entry = _entry(hass)
    assert cloudflare_access.get_config(entry) == (None, None)


async def test_verify_jwt_rejects_when_not_configured_even_with_a_valid_looking_header(
    hass, keypair
) -> None:
    """The critical regression test: a well-formed, plausible JWT must not
    authenticate anything if the operator never configured this feature."""
    private_pem, public_pem = keypair
    entry = _entry(hass)  # nothing configured
    token = _sign(private_pem)

    with _patched_signing_key(public_pem):
        assert await cloudflare_access.verify_jwt(hass, entry, token) is False


async def test_verify_jwt_rejects_missing_header_when_configured(hass) -> None:
    entry = _entry(hass, team_domain=TEAM_DOMAIN, aud=AUD)
    assert await cloudflare_access.verify_jwt(hass, entry, None) is False


async def test_verify_jwt_accepts_valid_token(hass, keypair) -> None:
    private_pem, public_pem = keypair
    entry = _entry(hass, team_domain=TEAM_DOMAIN, aud=AUD)
    token = _sign(private_pem)

    with _patched_signing_key(public_pem):
        assert await cloudflare_access.verify_jwt(hass, entry, token) is True


async def test_verify_jwt_rejects_wrong_audience(hass, keypair) -> None:
    private_pem, public_pem = keypair
    entry = _entry(hass, team_domain=TEAM_DOMAIN, aud=AUD)
    token = _sign(private_pem, aud="some-other-app")

    with _patched_signing_key(public_pem):
        assert await cloudflare_access.verify_jwt(hass, entry, token) is False


async def test_verify_jwt_rejects_expired_token(hass, keypair) -> None:
    private_pem, public_pem = keypair
    entry = _entry(hass, team_domain=TEAM_DOMAIN, aud=AUD)
    token = _sign(private_pem, exp_delta=-60)

    with _patched_signing_key(public_pem):
        assert await cloudflare_access.verify_jwt(hass, entry, token) is False


async def test_verify_jwt_rejects_garbage_token_without_crashing(hass) -> None:
    entry = _entry(hass, team_domain=TEAM_DOMAIN, aud=AUD)
    assert await cloudflare_access.verify_jwt(hass, entry, "not-a-jwt-at-all") is False


async def test_verify_jwt_rejects_wrong_signing_key(hass, keypair) -> None:
    """A token signed by a different key than the one JWKS returns must fail."""
    private_pem, _public_pem = keypair
    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_public_pem = other_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    entry = _entry(hass, team_domain=TEAM_DOMAIN, aud=AUD)
    token = _sign(private_pem)

    with _patched_signing_key(other_public_pem):
        assert await cloudflare_access.verify_jwt(hass, entry, token) is False
