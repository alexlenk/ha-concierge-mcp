"""Guest-secret generation and verification for the Concierge MCP integration.

This authentication path is entirely independent of ``hass.auth``. It never
validates a Home Assistant access token, session cookie, or admin bypass —
see the design document, section 3.2, for why a standard HA credential
cannot be used here.
"""
from __future__ import annotations

import hmac
import secrets

from homeassistant.config_entries import ConfigEntry

from .const import AUTH_SCHEME, CONF_SECRET, HEADER_AUTHORIZATION, SECRET_BYTES


def generate_secret() -> str:
    """Generate a new guest secret."""
    return secrets.token_urlsafe(SECRET_BYTES)


def get_secret(entry: ConfigEntry) -> str | None:
    """Return the guest secret stored on a config entry."""
    return entry.data.get(CONF_SECRET)


def extract_bearer_token(header_value: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not header_value:
        return None
    scheme, _, token = header_value.partition(" ")
    if scheme != AUTH_SCHEME or not token:
        return None
    return token


def verify_secret(entry: ConfigEntry, header_value: str | None) -> bool:
    """Verify a request's Authorization header against the stored secret.

    Uses a constant-time comparison to avoid a timing side-channel. This is
    the only authentication check performed for this integration's
    endpoint — there is no fallback path.
    """
    stored = get_secret(entry)
    if not stored:
        return False

    provided = extract_bearer_token(header_value)
    if not provided:
        return False

    return hmac.compare_digest(provided, stored)


__all__ = [
    "HEADER_AUTHORIZATION",
    "extract_bearer_token",
    "generate_secret",
    "get_secret",
    "verify_secret",
]
