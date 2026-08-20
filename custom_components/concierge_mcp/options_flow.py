"""Options flow: allowlist management and guest-secret regeneration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.helpers import selector

from . import auth
from .const import (
    CONF_CF_ACCESS_AUD,
    CONF_CF_ACCESS_TEAM_DOMAIN,
    CONF_CONTROL,
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_READ,
    CONF_SECRET,
)


class ConciergeMCPOptionsFlow(OptionsFlow):
    """Manage the allowlist and the guest secret after setup."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the options flow.

        ``self.config_entry`` is a read-only property on the base
        ``OptionsFlow`` class (resolved from ``self.hass``/``self.handler``,
        populated by the flow framework after construction) — it must not
        be assigned here. This has changed across Home Assistant releases;
        re-check it before bumping the minimum supported version (see the
        design document, section 12.2, on config-entry/options-flow API
        drift). ``config_entry`` is accepted only because
        ``ConciergeMCPConfigFlow.async_get_options_flow`` passes it.
        """
        self._new_secret: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Offer a choice between editing the allowlist, regenerating the
        secret, or configuring the optional Cloudflare Access auth path."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["entities", "regenerate_secret", "cloudflare_access"],
        )

    async def async_step_entities(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {
            item[CONF_ENTITY_ID]: item for item in self.config_entry.options.get(CONF_ENTITIES, [])
        }

        if user_input is not None:
            selected: list[str] = user_input.get(CONF_ENTITIES, [])
            control_entities = set(user_input.get(CONF_CONTROL, []))
            entities = [
                {
                    CONF_ENTITY_ID: entity_id,
                    CONF_READ: True,
                    CONF_CONTROL: entity_id in control_entities,
                }
                for entity_id in selected
            ]
            # async_create_entry's data *replaces* entry.options wholesale
            # (it's not merged) — start from the current options so an
            # allowlist edit doesn't silently wipe out the Cloudflare
            # Access settings, or vice versa in the other steps below.
            new_options = {**self.config_entry.options, CONF_ENTITIES: entities}
            return self.async_create_entry(data=new_options)

        return self.async_show_form(
            step_id="entities",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ENTITIES, default=list(current.keys())
                    ): selector.EntitySelector(selector.EntitySelectorConfig(multiple=True)),
                    vol.Optional(
                        CONF_CONTROL,
                        default=[eid for eid, item in current.items() if item.get(CONF_CONTROL)],
                    ): selector.EntitySelector(selector.EntitySelectorConfig(multiple=True)),
                }
            ),
        )

    async def async_step_regenerate_secret(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            new_data = {**self.config_entry.data, CONF_SECRET: self._new_secret}
            # Reads of the secret always go through entry.data directly
            # (auth.verify_secret / http.py), so the new value is
            # authoritative immediately. async_update_entry also notifies
            # this entry's update listener, which reloads it — harmless,
            # not relied upon for correctness here.
            self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
            return self.async_create_entry(data=self.config_entry.options)

        self._new_secret = auth.generate_secret()
        return self.async_show_form(
            step_id="regenerate_secret",
            data_schema=vol.Schema({}),
            description_placeholders={"secret": self._new_secret},
        )

    async def async_step_cloudflare_access(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure (or clear) the optional Cloudflare Access JWT auth path.

        Both fields must be non-empty for this path to activate — leaving
        either blank disables it, restoring guest-secret-only behavior.
        """
        if user_input is not None:
            new_options = {
                **self.config_entry.options,
                CONF_CF_ACCESS_TEAM_DOMAIN: user_input.get(CONF_CF_ACCESS_TEAM_DOMAIN, "").strip(),
                CONF_CF_ACCESS_AUD: user_input.get(CONF_CF_ACCESS_AUD, "").strip(),
            }
            return self.async_create_entry(data=new_options)

        return self.async_show_form(
            step_id="cloudflare_access",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_CF_ACCESS_TEAM_DOMAIN,
                        default=self.config_entry.options.get(CONF_CF_ACCESS_TEAM_DOMAIN, ""),
                    ): str,
                    vol.Optional(
                        CONF_CF_ACCESS_AUD,
                        default=self.config_entry.options.get(CONF_CF_ACCESS_AUD, ""),
                    ): str,
                }
            ),
        )
