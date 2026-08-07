"""The secret must never be exportable via a diagnostics download."""
from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.concierge_mcp import diagnostics
from custom_components.concierge_mcp.const import DOMAIN


async def test_secret_is_redacted_from_diagnostics(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"secret": "super-secret-value"},
        options={"entities": [{"entity_id": "lock.front_door", "read": True, "control": False}]},
    )
    entry.add_to_hass(hass)

    result = await diagnostics.async_get_config_entry_diagnostics(hass, entry)

    assert "super-secret-value" not in str(result)
    assert result["options"]["entities"][0]["entity_id"] == "lock.front_door"
