"""Allowlist schema and lookups for the Concierge MCP integration.

This is a small, from-scratch schema. It deliberately does not reuse
``homeassistant.auth.permissions.entities.ENTITY_POLICY_SCHEMA`` — that
schema lives in ``hass.auth``, and pulling it in here would blur the
isolation boundary between this integration's guest secret and Home
Assistant's own auth system.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry

from .const import CONF_CONTROL, CONF_ENTITIES, CONF_ENTITY_ID, CONF_READ

ALLOWLIST_ENTRY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): str,
        vol.Optional(CONF_READ, default=True): bool,
        vol.Optional(CONF_CONTROL, default=False): bool,
    }
)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITIES, default=list): [ALLOWLIST_ENTRY_SCHEMA],
    }
)


def get_entries(entry: ConfigEntry) -> list[dict[str, Any]]:
    """Return the raw, validated allowlist entries for a config entry."""
    raw = entry.options.get(CONF_ENTITIES, [])
    return [ALLOWLIST_ENTRY_SCHEMA(item) for item in raw]


def is_allowed(entry: ConfigEntry, entity_id: str, *, action: str = CONF_READ) -> bool:
    """Return True if entity_id is allowlisted for the given action."""
    if action not in (CONF_READ, CONF_CONTROL):
        raise ValueError(f"Unknown allowlist action: {action}")

    for item in get_entries(entry):
        if item[CONF_ENTITY_ID] == entity_id:
            return bool(item.get(action, False))
    return False


def list_allowed(entry: ConfigEntry, *, action: str = CONF_READ) -> list[str]:
    """Return the entity_ids allowlisted for the given action."""
    return [
        item[CONF_ENTITY_ID]
        for item in get_entries(entry)
        if item.get(action, False)
    ]
