"""Second, independent, opt-in auth path: Cloudflare Access JWT verification.

The guest secret in ``auth.py`` is the primary path, built for a headless
client (the guest chatbot backend) that can't complete an OAuth redirect.
This module adds an entirely separate path for a human operator testing
this endpoint interactively — e.g. via Claude.ai — through a Cloudflare
Access application in front of the same tunnel. It never weakens or
replaces the guest-secret check; ``http.py`` accepts a request if *either*
path succeeds.

Inert by default: both ``CONF_CF_ACCESS_TEAM_DOMAIN`` and
``CONF_CF_ACCESS_AUD`` must be explicitly configured, or every call here
returns False regardless of what headers a request carries. An operator
who has never touched this feature is not newly exposed by it.

Cloudflare Access injects a signed RS256 JWT into the
``Cf-Access-Jwt-Assertion`` header of every request it forwards once a
user completes its OAuth login. Verifying the signature against Access's
own published keys and checking the token's ``aud`` claim against this
specific Access Application's AUD tag is what scopes it to only this
endpoint — the same isolation property the guest secret exists for,
via a different, better-suited credential for an interactive human client.
"""
from __future__ import annotations

import logging

import jwt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from jwt import PyJWKClient

from .const import (
    CF_ACCESS_JWKS_LIFESPAN_SECONDS,
    CF_ACCESS_JWT_ALGORITHM,
    CONF_CF_ACCESS_AUD,
    CONF_CF_ACCESS_TEAM_DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# One PyJWKClient per team domain, reused across requests so its internal
# JWKS cache (keyed by CF_ACCESS_JWKS_LIFESPAN_SECONDS) is actually
# effective instead of re-fetching Cloudflare's keys on every call.
_jwks_clients: dict[str, PyJWKClient] = {}


def _normalize_team_domain(value: str | None) -> str | None:
    """Normalize a team-domain value to the bare team name PyJWKClient needs.

    Cloudflare displays this as a full hostname (``myteam.cloudflareaccess.com``)
    and the authorization-server metadata reports it as a URL
    (``https://myteam.cloudflareaccess.com``) — both are the obvious things
    to paste, and pasting either used to silently double the suffix
    (``myteam.cloudflareaccess.com.cloudflareaccess.com``), which doesn't
    resolve. Strip whitespace, an optional scheme, any trailing path, and a
    trailing ``.cloudflareaccess.com`` so all of those forms normalize to
    ``myteam``. A whitespace-only value normalizes to None so the path stays
    inert rather than building a bogus URL.
    """
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    value = value.removeprefix("https://").removeprefix("http://")
    value = value.split("/", 1)[0]
    value = value.removesuffix(".cloudflareaccess.com")
    return value or None


def get_config(entry: ConfigEntry) -> tuple[str | None, str | None]:
    """Return (team_domain, aud) configured for this entry, if any."""
    aud = entry.options.get(CONF_CF_ACCESS_AUD)
    return (
        _normalize_team_domain(entry.options.get(CONF_CF_ACCESS_TEAM_DOMAIN)),
        aud.strip() if aud else None,
    )


def _get_jwks_client(team_domain: str) -> PyJWKClient:
    client = _jwks_clients.get(team_domain)
    if client is None:
        jwks_url = f"https://{team_domain}.cloudflareaccess.com/cdn-cgi/access/certs"
        client = PyJWKClient(
            jwks_url, cache_keys=True, lifespan=CF_ACCESS_JWKS_LIFESPAN_SECONDS
        )
        _jwks_clients[team_domain] = client
    return client


async def verify_jwt(
    hass: HomeAssistant, entry: ConfigEntry, header_value: str | None
) -> bool:
    """Verify a Cf-Access-Jwt-Assertion header, if this path is configured.

    Returns False — never raises — for every failure mode: path not
    configured, missing header, expired token, wrong audience, bad
    signature, or a JWKS fetch failure. A malformed or hostile token must
    not crash the request handler; it just doesn't authenticate.
    """
    team_domain, aud = get_config(entry)
    if not team_domain or not aud or not header_value:
        return False

    client = _get_jwks_client(team_domain)

    try:
        signing_key = await hass.async_add_executor_job(
            client.get_signing_key_from_jwt, header_value
        )
        jwt.decode(
            header_value,
            signing_key.key,
            algorithms=[CF_ACCESS_JWT_ALGORITHM],
            audience=aud,
        )
    except jwt.PyJWTError as err:
        # This path is off by default and requires two explicit config
        # values, so a rejection here means an operator is actively trying
        # to sign in and failing — warning, not debug, so the most common
        # misconfiguration (an aud tag that doesn't match) is diagnosable
        # from the log instead of surfacing only as an opaque 401.
        _LOGGER.warning(
            "Cloudflare Access JWT rejected (team_domain=%s, expected aud=%s): %s",
            team_domain,
            aud,
            err,
        )
        return False
    except Exception:
        # get_signing_key_from_jwt reaches the network via urllib, which can
        # raise OSError/socket.timeout for DNS failures, TLS errors, or a
        # connection timeout — not all of those are PyJWTError subclasses.
        # This auth check must fail closed rather than surface as a 500.
        _LOGGER.exception(
            "Unexpected error verifying Cloudflare Access JWT (team_domain=%s)",
            team_domain,
        )
        return False

    return True
