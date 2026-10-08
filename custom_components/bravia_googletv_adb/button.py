"""Button that sends the TV to the launcher."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_SOURCE, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Home button."""
    async_add_entities([BraviaHomeButton(entry)])


class BraviaHomeButton(ButtonEntity):
    """Presses Home over the Android Debug Bridge integration's connection."""

    _attr_has_entity_name = True
    _attr_name = "Home"
    _attr_icon = "mdi:home"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the button on the same device as the media player."""
        self._source_registry_id: str = entry.data[CONF_SOURCE]
        self._attr_unique_id = f"{entry.entry_id}_home"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title
        )

    async def async_press(self) -> None:
        """Send the Home key."""
        source_entry = er.async_get(self.hass).async_get(self._source_registry_id)
        if source_entry is None or not source_entry.config_entry_id:
            return
        config_entry = self.hass.config_entries.async_get_entry(
            source_entry.config_entry_id
        )
        if config_entry is None or config_entry.state is not ConfigEntryState.LOADED:
            return
        await config_entry.runtime_data.aftv.home()
