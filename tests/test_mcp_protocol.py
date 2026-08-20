"""Tests for the MCP tool implementations."""
from __future__ import annotations

import json

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.concierge_mcp import mcp_protocol
from custom_components.concierge_mcp.const import (
    DOMAIN,
    TOOL_GET_STATE,
    TOOL_LIST_ENTITIES,
)


def _entry(hass, entities):
    entry = MockConfigEntry(domain=DOMAIN, data={"secret": "s"}, options={"entities": entities})
    entry.add_to_hass(hass)
    return entry


def test_list_tools_returns_fixed_v1_toolset() -> None:
    tools = mcp_protocol.list_tools()
    names = {t.name for t in tools}
    assert names == {TOOL_GET_STATE, TOOL_LIST_ENTITIES}


async def test_list_entities_reflects_allowlist(hass) -> None:
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen Light"})
    hass.states.async_set("light.not_exposed", "on", {"friendly_name": "Secret Light"})
    entry = _entry(hass, [{"entity_id": "light.kitchen", "read": True, "control": False}])

    content, is_error = await mcp_protocol.call_tool(hass, entry, TOOL_LIST_ENTITIES, {})

    assert is_error is False
    payload = json.loads(content[0].text)
    entity_ids = [e["entity_id"] for e in payload["entities"]]
    assert entity_ids == ["light.kitchen"]
    assert "light.not_exposed" not in entity_ids


async def test_list_entities_truncates_beyond_cap(hass) -> None:
    cap = mcp_protocol.MAX_LISTED_ENTITIES
    allowed = []
    for i in range(cap + 5):
        entity_id = f"sensor.entity_{i}"
        hass.states.async_set(entity_id, "on", {"friendly_name": f"Entity {i}"})
        allowed.append({"entity_id": entity_id, "read": True, "control": False})
    entry = _entry(hass, allowed)

    content, is_error = await mcp_protocol.call_tool(hass, entry, TOOL_LIST_ENTITIES, {})

    assert is_error is False
    payload = json.loads(content[0].text)
    assert len(payload["entities"]) == cap
    assert payload["truncated"] is True
    assert str(cap) in payload["message"]
    assert str(cap + 5) in payload["message"]


async def test_list_entities_under_cap_has_no_truncation_marker(hass) -> None:
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen Light"})
    entry = _entry(hass, [{"entity_id": "light.kitchen", "read": True, "control": False}])

    content, is_error = await mcp_protocol.call_tool(hass, entry, TOOL_LIST_ENTITIES, {})

    assert is_error is False
    payload = json.loads(content[0].text)
    assert "truncated" not in payload
    assert "message" not in payload


async def test_get_state_returns_state_for_allowed_entity(hass) -> None:
    hass.states.async_set("lock.front_door", "locked", {"friendly_name": "Front Door"})
    entry = _entry(hass, [{"entity_id": "lock.front_door", "read": True, "control": False}])

    content, is_error = await mcp_protocol.call_tool(
        hass, entry, TOOL_GET_STATE, {"entity_id": "lock.front_door"}
    )

    assert is_error is False
    payload = json.loads(content[0].text)
    assert payload["entity_id"] == "lock.front_door"
    assert payload["state"] == "locked"


async def test_get_state_filters_low_signal_attributes(hass) -> None:
    hass.states.async_set(
        "light.kitchen",
        "on",
        {
            "friendly_name": "Kitchen Light",
            "icon": "mdi:lightbulb",
            "entity_picture": "/local/kitchen.png",
            "supported_features": 63,
            "assumed_state": True,
            "attribution": "Data provided by Example",
            "brightness": 128,
        },
    )
    entry = _entry(hass, [{"entity_id": "light.kitchen", "read": True, "control": False}])

    content, is_error = await mcp_protocol.call_tool(
        hass, entry, TOOL_GET_STATE, {"entity_id": "light.kitchen"}
    )

    assert is_error is False
    attributes = json.loads(content[0].text)["attributes"]
    assert attributes == {"friendly_name": "Kitchen Light", "brightness": 128}


async def test_get_state_rejects_entity_outside_allowlist(hass) -> None:
    hass.states.async_set("lock.back_door", "locked", {})
    entry = _entry(hass, [])  # nothing allowlisted

    content, is_error = await mcp_protocol.call_tool(
        hass, entry, TOOL_GET_STATE, {"entity_id": "lock.back_door"}
    )

    assert is_error is True
    payload = json.loads(content[0].text)
    assert payload["error"] == "entity_not_allowed"


async def test_get_state_does_not_crash_on_missing_entity_id(hass) -> None:
    entry = _entry(hass, [])
    _content, is_error = await mcp_protocol.call_tool(hass, entry, TOOL_GET_STATE, {})
    assert is_error is True


async def test_get_state_reports_not_found_for_allowed_but_absent_entity(hass) -> None:
    entry = _entry(hass, [{"entity_id": "lock.ghost", "read": True, "control": False}])
    content, is_error = await mcp_protocol.call_tool(
        hass, entry, TOOL_GET_STATE, {"entity_id": "lock.ghost"}
    )
    assert is_error is True
    payload = json.loads(content[0].text)
    assert payload["error"] == "entity_not_found"


async def test_unknown_tool_returns_explicit_error_not_crash(hass) -> None:
    entry = _entry(hass, [])
    content, is_error = await mcp_protocol.call_tool(hass, entry, "delete_everything", {})
    assert is_error is True
    payload = json.loads(content[0].text)
    assert payload["error"] == "unknown_tool"
