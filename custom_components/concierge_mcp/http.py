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
            return _jsonrpc_error(None, _JSONRPC_PARSE_ERROR, "Parse error")

        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
            return _jsonrpc_error(body.get("id") if isinstance(body, dict) else None, _JSONRPC_INVALID_REQUEST, "Invalid Request")

        request_id = body.get("id")
        method = body.get("method")
        params = body.get("params") or {}

        if method == "notifications/initialized":
            return web.Response(status=202)

        if method == "initialize":
            return _jsonrpc_result(
                request_id,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
                },
            )

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

        return _jsonrpc_error(request_id, _JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}")


def _jsonrpc_result(request_id: Any, result: dict[str, Any]) -> web.Response:
    return web.json_response({"jsonrpc": "2.0", "id": request_id, "result": result})


def _jsonrpc_error(request_id: Any, code: int, message: str) -> web.Response:
    return web.json_response(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        status=400,
    )
