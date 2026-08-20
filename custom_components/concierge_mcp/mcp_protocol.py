"""MCP tool definitions for the Concierge MCP integration.

Reuses the ``mcp`` SDK's data types (``mcp.types.Tool``,
``mcp.types.TextContent``) so the response shape matches Home Assistant's
own ``mcp_server`` integration (FR-7) and any client already written
against it works here with only a URL/secret change.

Deliberately does **not** reuse ``homeassistant.helpers.llm`` (the
Assist/intent system) — see the design document, section 8.7. Tools are
resolved directly against ``hass.states`` after an allowlist check; there
is no natural-language intent matching.

The upstream ``mcp_server`` integration dispatches a single stateless HTTP
request through ``mcp.server.Server``'s stream-oriented session machinery
(``_async_handle_streamable_message``). That machinery exists to serve a
long-lived bidirectional session (stdio/SSE) and is awkward to bend around
a one-shot HTTP POST. This module instead implements the ``tools/list``
and ``tools/call`` methods as plain functions and lets ``http.py`` dispatch
JSON-RPC directly to them — the wire format is identical, only the
in-process plumbing differs.
"""
from __future__ import annotations

import json
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from mcp import types

from . import allowlist
from .const import (
    CONF_READ,
    ERROR_NOT_ALLOWED,
    ERROR_NOT_FOUND,
    TOOL_GET_STATE,
    TOOL_LIST_ENTITIES,
)

# Home Assistant attribute keys that are internal plumbing rather than
# information a guest-facing chatbot's tool call would ever need — icon
# identifiers, opaque feature bitmasks, and the like. Everything else
# passes through unfiltered, since domain-specific attributes (e.g.
# "current_temperature", or whatever custom attribute an operator stores
# for a checkout-time sensor) can't be safely guessed at generically.
_LOW_SIGNAL_ATTRIBUTES = frozenset(
    {"icon", "entity_picture", "supported_features", "assumed_state", "attribution"}
)

# Anthropic's own tool-design guidance uses 50 as a reasonable default cap
# for potentially-large list responses; there's no operator-facing reason
# to expect an allowlist much bigger than that for a guest chatbot's use case.
MAX_LISTED_ENTITIES = 50


def _filter_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in attributes.items() if k not in _LOW_SIGNAL_ATTRIBUTES}

GET_STATE_TOOL = types.Tool(
    name=TOOL_GET_STATE,
    description="Get the current state and attributes of an allowlisted entity.",
    inputSchema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "The entity_id to look up, e.g. 'lock.front_door'.",
            }
        },
        "required": ["entity_id"],
    },
)

LIST_ENTITIES_TOOL = types.Tool(
    name=TOOL_LIST_ENTITIES,
    description="List the entities exposed to this client, with friendly names.",
    inputSchema={
        "type": "object",
        "properties": {},
    },
)


def list_tools() -> list[types.Tool]:
    """Return the fixed v1 tool set."""
    return [GET_STATE_TOOL, LIST_ENTITIES_TOOL]


def _error_result(code: str, message: str) -> tuple[list[types.TextContent], bool]:
    content = [
        types.TextContent(
            type="text",
            text=json.dumps({"error": code, "message": message}),
        )
    ]
    return content, True


def _list_entities(hass: HomeAssistant, entry: ConfigEntry) -> tuple[list[types.TextContent], bool]:
    allowed_ids = allowlist.list_allowed(entry, action=CONF_READ)
    truncated = len(allowed_ids) > MAX_LISTED_ENTITIES

    entities = []
    for entity_id in allowed_ids[:MAX_LISTED_ENTITIES]:
        state = hass.states.get(entity_id)
        entities.append(
            {
                "entity_id": entity_id,
                "friendly_name": (state.attributes.get("friendly_name") if state else None),
                "available": state is not None,
            }
        )

    payload: dict[str, Any] = {"entities": entities}
    if truncated:
        payload["truncated"] = True
        payload["message"] = (
            f"Showing {MAX_LISTED_ENTITIES} of {len(allowed_ids)} allowlisted entities. "
            "This tool does not support pagination — ask the operator to narrow the "
            "allowlist if you need entities beyond this list."
        )

    content = [types.TextContent(type="text", text=json.dumps(payload))]
    return content, False


def _get_state(
    hass: HomeAssistant, entry: ConfigEntry, arguments: dict[str, Any]
) -> tuple[list[types.TextContent], bool]:
    entity_id = arguments.get("entity_id")
    if not entity_id:
        return _error_result("invalid_arguments", "entity_id is required")

    if not allowlist.is_allowed(entry, entity_id, action=CONF_READ):
        return _error_result(
            ERROR_NOT_ALLOWED, f"{entity_id} is not exposed by this Concierge MCP endpoint"
        )

    state = hass.states.get(entity_id)
    if state is None:
        return _error_result(ERROR_NOT_FOUND, f"{entity_id} has no current state")

    content = [
        types.TextContent(
            type="text",
            text=json.dumps(
                {
                    "entity_id": state.entity_id,
                    "state": state.state,
                    "attributes": _filter_attributes(dict(state.attributes)),
                }
            ),
        )
    ]
    return content, False


async def call_tool(
    hass: HomeAssistant, entry: ConfigEntry, name: str, arguments: dict[str, Any] | None
) -> tuple[list[types.TextContent], bool]:
    """Execute a tool call. Returns (content, is_error)."""
    arguments = arguments or {}

    if name == TOOL_LIST_ENTITIES:
        return _list_entities(hass, entry)

    if name == TOOL_GET_STATE:
        return _get_state(hass, entry, arguments)

    return _error_result("unknown_tool", f"Unknown tool: {name}")
