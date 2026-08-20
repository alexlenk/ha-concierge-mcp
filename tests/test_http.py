"""HTTP-layer tests for the Concierge MCP endpoint.

These are the tests that actually prove the design goal (see the design
document, section 10): the endpoint is reachable only with this
integration's own guest secret, never with a Home Assistant credential,
and two integrations set up in the same instance don't interfere.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.concierge_mcp.const import (
    API_URL,
    CONF_CF_ACCESS_AUD,
    CONF_CF_ACCESS_TEAM_DOMAIN,
    DOMAIN,
    HEADER_CF_ACCESS_JWT,
)

ENTITIES = [{"entity_id": "lock.front_door", "read": True, "control": False}]


async def _setup_entry(
    hass,
    *,
    secret: str = "correct-secret",
    entities: list | None = None,
    cf_access: dict | None = None,
) -> MockConfigEntry:
    options = {"entities": entities if entities is not None else ENTITIES}
    if cf_access:
        options.update(cf_access)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"secret": secret},
        options=options,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _rpc(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


async def test_correct_secret_succeeds(hass, hass_client_no_auth) -> None:
    await _setup_entry(hass)
    client = await hass_client_no_auth()

    resp = await client.post(
        API_URL,
        json=_rpc("initialize", {"protocolVersion": "2025-06-18"}),
        headers={"Authorization": "Bearer correct-secret"},
    )

    assert resp.status == 200
    body = await resp.json()
    assert body["result"]["serverInfo"]["name"]


async def test_missing_authorization_header_rejected(hass, hass_client_no_auth) -> None:
    await _setup_entry(hass)
    client = await hass_client_no_auth()

    resp = await client.post(API_URL, json=_rpc("tools/list"))

    assert resp.status == 401


async def test_wrong_secret_rejected(hass, hass_client_no_auth) -> None:
    await _setup_entry(hass)
    client = await hass_client_no_auth()

    resp = await client.post(
        API_URL, json=_rpc("tools/list"), headers={"Authorization": "Bearer not-the-secret"}
    )

    assert resp.status == 401


async def test_valid_home_assistant_access_token_is_rejected(hass, hass_client) -> None:
    """The regression test: a real, currently-valid HA token must not work here.

    ``hass_client`` auto-attaches a genuine Long-Lived-Access-Token-style
    bearer token for an admin test user. If this ever passed, it would
    mean the endpoint silently fell back to ``hass.auth`` — exactly what
    this integration must never do.
    """
    await _setup_entry(hass)
    client = await hass_client()

    resp = await client.post(API_URL, json=_rpc("tools/list"))

    assert resp.status == 401


async def test_tools_list_reflects_current_allowlist(hass, hass_client_no_auth) -> None:
    await _setup_entry(hass, entities=[{"entity_id": "lock.front_door", "read": True, "control": False}])
    client = await hass_client_no_auth()

    resp = await client.post(
        API_URL, json=_rpc("tools/list"), headers={"Authorization": "Bearer correct-secret"}
    )

    body = await resp.json()
    tool_names = {t["name"] for t in body["result"]["tools"]}
    assert tool_names == {"get_state", "list_entities"}


async def test_tools_call_rejects_entity_outside_allowlist(hass, hass_client_no_auth) -> None:
    hass.states.async_set("lock.back_door", "locked", {})
    await _setup_entry(hass, entities=[])
    client = await hass_client_no_auth()

    resp = await client.post(
        API_URL,
        json=_rpc("tools/call", {"name": "get_state", "arguments": {"entity_id": "lock.back_door"}}),
        headers={"Authorization": "Bearer correct-secret"},
    )

    assert resp.status == 200
    body = await resp.json()
    assert body["result"]["isError"] is True


async def test_malformed_json_body_returns_parse_error(hass, hass_client_no_auth) -> None:
    await _setup_entry(hass)
    client = await hass_client_no_auth()

    resp = await client.post(
        API_URL,
        data="not json",
        headers={"Authorization": "Bearer correct-secret", "Content-Type": "application/json"},
    )

    assert resp.status == 400
    body = await resp.json()
    assert body["error"]["code"] == -32700


async def test_non_jsonrpc_body_returns_invalid_request(hass, hass_client_no_auth) -> None:
    await _setup_entry(hass)
    client = await hass_client_no_auth()

    resp = await client.post(
        API_URL, json={"hello": "world"}, headers={"Authorization": "Bearer correct-secret"}
    )

    assert resp.status == 400
    body = await resp.json()
    assert body["error"]["code"] == -32600


async def test_unknown_method_returns_method_not_found(hass, hass_client_no_auth) -> None:
    await _setup_entry(hass)
    client = await hass_client_no_auth()

    resp = await client.post(
        API_URL, json=_rpc("not/a/real/method"), headers={"Authorization": "Bearer correct-secret"}
    )

    assert resp.status == 400
    body = await resp.json()
    assert body["error"]["code"] == -32601


async def test_initialized_notification_returns_202_with_no_body(hass, hass_client_no_auth) -> None:
    await _setup_entry(hass)
    client = await hass_client_no_auth()

    resp = await client.post(
        API_URL,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Authorization": "Bearer correct-secret"},
    )

    assert resp.status == 202


async def test_removed_entry_returns_503_instead_of_crashing(hass, hass_client_no_auth) -> None:
    """The view stays registered for the life of the HA process, even if
    the config entry is later removed entirely. It must not crash."""
    entry = await _setup_entry(hass)
    client = await hass_client_no_auth()

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    resp = await client.post(
        API_URL, json=_rpc("tools/list"), headers={"Authorization": "Bearer correct-secret"}
    )

    assert resp.status == 503


async def test_cross_integration_isolation_with_official_mcp_server(
    hass, hass_client_no_auth, hass_client
) -> None:
    """FR-8: this integration and the official mcp_server must not interact.

    Set up both in the same hass instance and confirm neither's config,
    secret, or entity exposure leaks into the other.
    """
    concierge_entry = await _setup_entry(hass, secret="guest-secret")

    # The official mcp_server integration depends on conversation, which
    # (as of HA 2026.7) expects the core "homeassistant" component's
    # exposed-entities data to already exist once hass reaches the
    # "started" state — the pytest-homeassistant-custom-component hass
    # fixture doesn't set that up on its own.
    assert await async_setup_component(hass, "homeassistant", {})

    mcp_server_entry = MockConfigEntry(
        domain="mcp_server",
        data={"llm_hass_api": ["assist"]},
    )
    mcp_server_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mcp_server_entry.entry_id)
    await hass.async_block_till_done()

    # Our own domain storage only ever references our own entry.
    assert list(hass.data[DOMAIN].keys()) == [concierge_entry.entry_id]

    # Our secret still only unlocks our own endpoint.
    client = await hass_client_no_auth()
    ours = await client.post(
        API_URL, json=_rpc("tools/list"), headers={"Authorization": "Bearer guest-secret"}
    )
    assert ours.status == 200

    # A genuine HA access token still doesn't work on our endpoint, even
    # with the official integration configured alongside it.
    authed_client = await hass_client()
    still_rejected = await authed_client.post(API_URL, json=_rpc("tools/list"))
    assert still_rejected.status == 401

    # The official integration's own endpoint is unaffected by our secret.
    official_with_our_secret = await client.post(
        "/api/mcp", json=_rpc("tools/list"), headers={"Authorization": "Bearer guest-secret"}
    )
    assert official_with_our_secret.status in (401, 403)


def _sign_cf_access_jwt(private_pem: bytes, *, aud: str) -> str:


    return jwt.encode(
        {"aud": aud, "sub": "operator@example.com", "exp": int(time.time()) + 300},
        private_pem,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


def _rsa_keypair() -> tuple[bytes, bytes]:

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


async def test_valid_cloudflare_access_jwt_succeeds_when_configured(
    hass, hass_client_no_auth
) -> None:

    private_pem, public_pem = _rsa_keypair()
    await _setup_entry(
        hass, cf_access={CONF_CF_ACCESS_TEAM_DOMAIN: "myteam", CONF_CF_ACCESS_AUD: "test-aud"}
    )
    token = _sign_cf_access_jwt(private_pem, aud="test-aud")
    client = await hass_client_no_auth()

    with patch(
        "jwt.PyJWKClient.get_signing_key_from_jwt",
        return_value=SimpleNamespace(key=public_pem),
    ):
        resp = await client.post(
            API_URL, json=_rpc("tools/list"), headers={HEADER_CF_ACCESS_JWT: token}
        )

    assert resp.status == 200


async def test_cloudflare_access_jwt_ignored_when_not_configured(
    hass, hass_client_no_auth
) -> None:
    """The critical regression test: this header must not grant access on
    an entry that never opted into the Cloudflare Access auth path."""

    private_pem, public_pem = _rsa_keypair()
    await _setup_entry(hass)  # no cf_access configured
    token = _sign_cf_access_jwt(private_pem, aud="test-aud")
    client = await hass_client_no_auth()

    with patch(
        "jwt.PyJWKClient.get_signing_key_from_jwt",
        return_value=SimpleNamespace(key=public_pem),
    ):
        resp = await client.post(
            API_URL, json=_rpc("tools/list"), headers={HEADER_CF_ACCESS_JWT: token}
        )

    assert resp.status == 401
