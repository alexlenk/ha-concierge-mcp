"""Diagnostics support for the Concierge MCP integration.

The guest secret must never be exportable via a support bundle — it is
the entire credential guarding the endpoint (see the design document,
section 8.5). Redact it explicitly rather than relying on convention.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_SECRET

TO_REDACT = {CONF_SECRET}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, with the secret redacted."""
    return {
        "data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
    }
