"""HTTP transport for the Concierge MCP integration.

This view intentionally sets ``requires_auth = False``. That is not an
oversight: Home Assistant's default ``requires_auth = True`` path
validates the request against ``hass.auth`` (a Long-Lived Access Token,
session cookie, or similar), and any of those would grant this endpoint
the same blast radius as the rest of the Home Assistant API — exactly
what this integration exists to avoid (see the design document, section
3.2). There is no admin bypass and no loopback exemption.

Two independent auth paths are accepted — either is sufficient:
``auth.verify_secret`` (the guest secret, for the headless guest chatbot
backend) or ``cloudflare_access.verify_jwt`` (a Cloudflare Access JWT, for
a human operator testing interactively; inert unless explicitly
configured). Neither path can weaken the other; both are checked
independently against this integration's own state, never against
``hass.auth``.

On status codes: a JSON-RPC-level error (unknown method, bad params) is
returned with HTTP **200**, not 4xx. The two layers are distinct — the
JSON-RPC message was delivered successfully, and it happens to carry an
error. This matters in practice because the official MCP client calls
``raise_for_status()`` on each POST, so a 4xx for a well-formed request
raises a transport exception that tears down the entire session; the user
sees "couldn't connect to the server" and never sees the actual error.
Home Assistant's own ``mcp_server`` behaves the same way. Only genuinely
malformed input (unparseable body, not a JSON-RPC envelope) gets a 4xx.
"""
from __future__ import annotations

import logging
from typing import Any

from aiohttp import web
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.http import KEY_HASS, HomeAssistantView

from . import auth, cloudflare_access, mcp_protocol
from .const import (
    API_URL,
    DOMAIN,
    HEADER_AUTHORIZATION,
    HEADER_CF_ACCESS_JWT,
    MCP_PROTOCOL_VERSION,
    MCP_SERVER_NAME,
    MCP_SERVER_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# Protocol versions this endpoint can speak. Defined here rather than in
# const.py so this module stays self-contained: `initialize` echoes the
# client's requested version when it appears here (per the spec's version
# negotiation) instead of unconditionally answering with our own.
_SUPPORTED_PROTOCOL_VERSIONS = frozenset(
    {"2024-11-05", "2025-03-26", MCP_PROTOCOL_VERSION}
)

_JSONRPC_PARSE_ERROR = -32700
_JSONRPC_INVALID_REQUEST = -32600
_JSONRPC_METHOD_NOT_FOUND = -32601


def _get_config_entry(hass: HomeAssistant) -> ConfigEntry | None:
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


class ConciergeMCPView(HomeAssistantView):
    """Streamable-HTTP (stateless, single request/response) MCP endpoint."""

    url = API_URL
    name = "api:concierge_mcp"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app[KEY_HASS]
        entry = _get_config_entry(hass)

        if entry is None:
            return web.json_response({"error": "not_configured"}, status=503)

        authenticated = auth.verify_secret(
            entry, request.headers.get(HEADER_AUTHORIZATION)
        ) or await cloudflare_access.verify_jwt(
            hass, entry, request.headers.get(HEADER_CF_ACCESS_JWT)
        )
        if not authenticated:
            return web.json_response({"error": "unauthorized"}, status=401)

        try:
            body = await request.json()
        except ValueError:
            return _jsonrpc_error(None, _JSONRPC_PARSE_ERROR, "Parse error", status=400)

        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
            return _jsonrpc_error(
                body.get("id") if isinstance(body, dict) else None,
                _JSONRPC_INVALID_REQUEST,
                "Invalid Request",
                status=400,
            )

        request_id = body.get("id")
        method = body.get("method")
        params = body.get("params") or {}

        # A JSON-RPC notification has no "id" and MUST NOT be answered with a
        # response body — any notification (not just notifications/initialized)
        # gets a bare 202, including ones this server doesn't know about.
        if request_id is None and isinstance(method, str) and method.startswith("notifications/"):
            return web.Response(status=202)

        if method == "initialize":
            requested = params.get("protocolVersion")
            negotiated = (
                requested
                if requested in _SUPPORTED_PROTOCOL_VERSIONS
                else MCP_PROTOCOL_VERSION
            )
            return _jsonrpc_result(
                request_id,
                {
                    "protocolVersion": negotiated,
                    # Only advertise what's actually implemented. Declaring
                    # e.g. "resources" here would invite resources/list calls
                    # this server can't answer.
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
                },
            )

        # Required by the spec for any receiver: respond promptly with an
        # empty result. A client that pings to check liveness otherwise
        # treats this endpoint as stale and drops the connection.
        if method == "ping":
            return _jsonrpc_result(request_id, {})

        if method == "tools/list":
            tools = mcp_protocol.list_tools()
            return _jsonrpc_result(
                request_id, {"tools": [t.model_dump(exclude_none=True) for t in tools]}
            )

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments")
            content, is_error = await mcp_protocol.call_tool(hass, entry, name, arguments)
            return _jsonrpc_result(
                request_id,
                {
                    "content": [c.model_dump(exclude_none=True) for c in content],
                    "isError": is_error,
                },
            )

        # Clients probe these during connector setup even when `initialize`
        # didn't advertise the capability. Answering with an empty list is
        # both cheaper and friendlier than a method-not-found error, which
        # some clients surface as a hard connection failure.
        if method in ("resources/list", "resources/templates/list"):
            return _jsonrpc_result(request_id, {"resources": []})

        if method == "prompts/list":
            return _jsonrpc_result(request_id, {"prompts": []})

        return _jsonrpc_error(request_id, _JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}")

    async def get(self, request: web.Request) -> web.Response:
        """Reject the optional server-initiated SSE stream with 405.

        The spec explicitly allows a server that doesn't offer a GET stream to
        answer 405, which tells the client to stop trying. This endpoint is
        stateless request/response, so there is nothing to stream. Declaring
        the handler (rather than leaving aiohttp to 405 for an unknown method)
        keeps the response shape ours and documents the choice.
        """
        return web.json_response(
            {"error": "method_not_allowed", "message": "This endpoint does not offer an SSE stream"},
            status=405,
        )

    async def delete(self, request: web.Request) -> web.Response:
        """Reject explicit session termination with 405.

        No session state is kept (no ``Mcp-Session-Id`` is ever issued), so
        there is nothing for a client to tear down. 405 is the spec's
        sanctioned answer for a server that doesn't allow this.
        """
        return web.json_response(
            {"error": "method_not_allowed", "message": "This endpoint is stateless; no session to delete"},
            status=405,
        )


def _jsonrpc_result(request_id: Any, result: dict[str, Any]) -> web.Response:
    return web.json_response({"jsonrpc": "2.0", "id": request_id, "result": result})


def _jsonrpc_error(
    request_id: Any, code: int, message: str, *, status: int = 200
) -> web.Response:
    """Return a JSON-RPC error.

    Defaults to HTTP 200: a JSON-RPC-level error is a *successfully
    delivered* JSON-RPC message, not a transport failure, and the two layers
    must not be conflated. The official MCP client calls
    ``response.raise_for_status()`` on the POST, so answering a well-formed
    request with a 4xx turns an ordinary "method not found" into an
    ``HTTPStatusError`` that tears down the whole session — the client then
    reports the server as unreachable rather than surfacing the error. Home
    Assistant's own ``mcp_server`` also answers these with 200. Genuinely
    malformed input (unparseable body, not a JSON-RPC envelope) still gets a
    4xx via an explicit ``status``.
    """
    return web.json_response(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        status=status,
    )
