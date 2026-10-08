"""Config flow for Bravia Google TV ADB Playback."""

from __future__ import annotations

from typing import Any

import probatio

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from .const import ANDROIDTV_DOMAIN, CONF_SOURCE, DOMAIN


class BraviaGoogleTvAdbConfigFlow(ConfigFlow, domain=DOMAIN):
    """Let the user pick which Android Debug Bridge media player to use."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            registry_entry = er.async_get(self.hass).async_get(user_input[CONF_SOURCE])
            if registry_entry is None:
                errors["base"] = "entity_not_registered"
            else:
                # The registry id survives entity_id renames.
                await self.async_set_unique_id(registry_entry.id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={CONF_SOURCE: registry_entry.id},
                )

        schema = probatio.Schema(
            {
                probatio.Required(CONF_SOURCE): EntitySelector(
                    EntitySelectorConfig(
                        domain="media_player", integration=ANDROIDTV_DOMAIN
                    )
                ),
                probatio.Required(CONF_NAME, default="TV playback"): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
