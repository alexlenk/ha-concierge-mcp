"""Tests for the config flow and options flow."""
from __future__ import annotations

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.concierge_mcp.const import CONF_ENTITIES, CONF_SECRET, DOMAIN


async def test_full_setup_flow_generates_and_shows_secret_once(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result2 = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "show_secret"
    secret = result2["description_placeholders"]["secret"]
    assert secret
    assert len(secret) >= 32

    result3 = await hass.config_entries.flow.async_configure(result2["flow_id"], {})
    assert result3["type"] is FlowResultType.CREATE_ENTRY

    entry = result3["result"]
    assert entry.data[CONF_SECRET] == secret
    assert entry.options[CONF_ENTITIES] == []


async def test_single_instance_enforced(hass) -> None:
    MockConfigEntry(
        domain=DOMAIN, data={"secret": "existing"}, options={"entities": []}
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def _setup_entry(hass, secret: str = "old-secret") -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={"secret": secret}, options={"entities": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_options_flow_updates_allowlist(hass) -> None:
    entry = await _setup_entry(hass)
    hass.states.async_set("lock.front_door", "locked", {})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "entities"}
    )
    assert result2["step_id"] == "entities"

    result3 = await hass.config_entries.options.async_configure(
        result2["flow_id"], {"entities": ["lock.front_door"], "control": []}
    )
    assert result3["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert entry.options["entities"] == [
        {"entity_id": "lock.front_door", "read": True, "control": False}
    ]


async def test_options_flow_regenerate_secret_invalidates_old(hass) -> None:
    entry = await _setup_entry(hass, secret="old-secret")

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "regenerate_secret"}
    )
    assert result2["step_id"] == "regenerate_secret"
    new_secret = result2["description_placeholders"]["secret"]
    assert new_secret != "old-secret"

    result3 = await hass.config_entries.options.async_configure(result2["flow_id"], {})
    assert result3["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert entry.data["secret"] == new_secret
    assert entry.data["secret"] != "old-secret"
