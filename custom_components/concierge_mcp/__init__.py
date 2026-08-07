"""The Concierge MCP integration.

Exposes a narrow, operator-curated allowlist of entities over the Model
Context Protocol at ``/api/concierge_mcp``, authenticated by a guest
secret that this integration owns end-to-end — independent of
``hass.auth`` and the official ``mcp_server`` integration. See the
project README and design document for the full rationale.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .http import ConciergeMCPView

PLATFORMS: list[str] = []

# This integration is config-entry only — there is no YAML configuration.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the HTTP view once, regardless of config entry reloads."""
    if DOMAIN not in hass.data:
        hass.http.register_view(ConciergeMCPView())
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Concierge MCP from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options (allowlist) change."""
    await hass.config_entries.async_reload(entry.entry_id)
