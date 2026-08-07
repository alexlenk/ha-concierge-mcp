"""Tests for the allowlist schema and lookups."""
from __future__ import annotations

import pytest
import voluptuous as vol
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.concierge_mcp import allowlist
from custom_components.concierge_mcp.const import DOMAIN


def _entry(entities: list[dict]) -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, data={"secret": "s"}, options={"entities": entities})


async def test_schema_rejects_malformed_entry() -> None:
    with pytest.raises(vol.Invalid):
        allowlist.ALLOWLIST_ENTRY_SCHEMA({"read": True})  # missing entity_id


async def test_schema_defaults_read_true_control_false() -> None:
    validated = allowlist.ALLOWLIST_ENTRY_SCHEMA({"entity_id": "light.kitchen"})
    assert validated == {"entity_id": "light.kitchen", "read": True, "control": False}


async def test_is_allowed_distinguishes_read_and_control() -> None:
    entry = _entry(
        [
            {"entity_id": "lock.front_door", "read": True, "control": False},
            {"entity_id": "switch.gate", "read": True, "control": True},
        ]
    )

    assert allowlist.is_allowed(entry, "lock.front_door", action="read") is True
    assert allowlist.is_allowed(entry, "lock.front_door", action="control") is False
    assert allowlist.is_allowed(entry, "switch.gate", action="control") is True
    assert allowlist.is_allowed(entry, "sensor.unknown", action="read") is False


async def test_list_allowed_filters_by_action() -> None:
    entry = _entry(
        [
            {"entity_id": "lock.front_door", "read": True, "control": False},
            {"entity_id": "switch.gate", "read": True, "control": True},
        ]
    )

    assert allowlist.list_allowed(entry, action="read") == ["lock.front_door", "switch.gate"]
    assert allowlist.list_allowed(entry, action="control") == ["switch.gate"]


async def test_is_allowed_rejects_unknown_action() -> None:
    entry = _entry([])
    with pytest.raises(ValueError):
        allowlist.is_allowed(entry, "lock.front_door", action="delete")
