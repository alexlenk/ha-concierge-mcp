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

import functools
import json
from datetime import timedelta
from typing import Any

from homeassistant.components.recorder import get_instance, history
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from mcp import types

from . import allowlist
from .const import (
    CONF_READ,
    DEFAULT_HISTORY_HOURS,
    ERROR_HISTORY_UNAVAILABLE,
    ERROR_NOT_ALLOWED,
    ERROR_NOT_FOUND,
    MAX_HISTORY_HOURS,
    MAX_HISTORY_STATES,
    TOOL_GET_HISTORY,
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

GET_HISTORY_TOOL = types.Tool(
    name=TOOL_GET_HISTORY,
    description=(
        "Get how an allowlisted entity's state changed over a recent time "
        "window, e.g. to answer 'when was the door unlocked' or 'how has "
        "the temperature changed today'. Returns state transitions only "
        "(not every attribute update), most recent last."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "The entity_id to look up, e.g. 'lock.front_door'.",
            },
            "hours": {
                "type": "integer",
                "description": (
                    f"How many hours of history to look back, ending now. "
                    f"Defaults to {DEFAULT_HISTORY_HOURS}. Capped at "
                    f"{MAX_HISTORY_HOURS} (7 days); larger values are "
                    "silently clamped to that cap."
                ),
            },
        },
        "required": ["entity_id"],
    },
)


def list_tools() -> list[types.Tool]:
    """Return the fixed v1 tool set."""
    return [GET_STATE_TOOL, LIST_ENTITIES_TOOL, GET_HISTORY_TOOL]


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


async def _get_history(
    hass: HomeAssistant, entry: ConfigEntry, arguments: dict[str, Any]
) -> tuple[list[types.TextContent], bool]:
    entity_id = arguments.get("entity_id")
    if not entity_id:
        return _error_result("invalid_arguments", "entity_id is required")

    if not allowlist.is_allowed(entry, entity_id, action=CONF_READ):
        return _error_result(
            ERROR_NOT_ALLOWED, f"{entity_id} is not exposed by this Concierge MCP endpoint"
        )

    hours = arguments.get("hours", DEFAULT_HISTORY_HOURS)
    if not isinstance(hours, int) or isinstance(hours, bool) or hours < 1:
        return _error_result("invalid_arguments", "hours must be a positive integer")
    clamped = hours > MAX_HISTORY_HOURS
    hours = min(hours, MAX_HISTORY_HOURS)

    # This tool is read-only history, same allowlist gate as get_state — but
    # it needs the recorder component, which (unlike this integration's own
    # dependencies) an operator can legitimately run Home Assistant without.
    # Fail with an explicit, actionable error rather than an unhandled
    # KeyError from recorder.get_instance() (FR-3: no crashes).
    if "recorder" not in hass.config.components:
        return _error_result(
            ERROR_HISTORY_UNAVAILABLE,
            "History is unavailable: the recorder integration is not running",
        )

    start_time = dt_util.utcnow() - timedelta(hours=hours)
    states_by_entity = await get_instance(hass).async_add_executor_job(
        functools.partial(
            history.get_significant_states,
            hass,
            start_time,
            entity_ids=[entity_id],
            no_attributes=True,  # this tool only returns state + timestamp
        )
    )
    states = states_by_entity.get(entity_id, [])

    truncated = len(states) > MAX_HISTORY_STATES
    # Keep the most recent entries when truncating — the tail is almost
    # always what a "what happened" question cares about.
    history_entries = [
        {"state": state.state, "last_changed": state.last_changed.isoformat()}
        for state in states[-MAX_HISTORY_STATES:]
    ]

    payload: dict[str, Any] = {
        "entity_id": entity_id,
        "hours": hours,
        "history": history_entries,
    }
    if clamped:
        payload["message"] = (
            f"Requested window exceeds the {MAX_HISTORY_HOURS}-hour cap; "
            f"showing the last {MAX_HISTORY_HOURS} hours instead."
        )
    if truncated:
        payload["truncated"] = True
        payload.setdefault("message", (
            f"Showing the most recent {MAX_HISTORY_STATES} state changes "
            f"of {len(states)} in this window. Ask for a shorter `hours` "
            "window if you need finer detail."
        ))

    content = [types.TextContent(type="text", text=json.dumps(payload))]
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

    if name == TOOL_GET_HISTORY:
        return await _get_history(hass, entry, arguments)

    return _error_result("unknown_tool", f"Unknown tool: {name}")
