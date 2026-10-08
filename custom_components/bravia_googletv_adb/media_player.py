"""Media player reporting playback state read from the TV's media sessions."""

from __future__ import annotations

from datetime import timedelta
import logging
import re

from homeassistant.components.media_player import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_SOURCE, DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=5)
PARALLEL_UPDATES = 1

# The window-based app detection in the stock integration returns nothing on this TV.
CMD_CURRENT_APP = "dumpsys activity activities | grep -m 1 mResumedActivity"
CMD_SESSIONS = "dumpsys media_session | grep -E 'package=|state=PlaybackState'"

RE_APP = re.compile(r"u\d+ ([\w.]+)/")
RE_PACKAGE = re.compile(r"package=([\w.]+)")
RE_PLAYBACK_STATE = re.compile(r"state=PlaybackState \{state=(\d+)")

# android.media.session.PlaybackState
PLAYBACK_PAUSED = 2
PLAYBACK_PLAYING = 3


def _parse_app(output: str) -> str | None:
    """Return the foreground app package from `mResumedActivity`."""
    match = RE_APP.search(output)
    return match.group(1) if match else None


def _parse_session_states(output: str) -> dict[str, int]:
    """Return the first playback state reported for each package."""
    states: dict[str, int] = {}
    package: str | None = None
    for line in output.splitlines():
        if package_match := RE_PACKAGE.search(line):
            package = package_match.group(1)
        elif (state_match := RE_PLAYBACK_STATE.search(line)) and package:
            states.setdefault(package, int(state_match.group(1)))
    return states


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the media player."""
    async_add_entities([BraviaPlaybackPlayer(entry)], update_before_add=True)


class BraviaPlaybackPlayer(MediaPlayerEntity):
    """Playback state of the foreground app on an Android Debug Bridge TV."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = True
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY | MediaPlayerEntityFeature.PAUSE
    )

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the entity."""
        self._source_registry_id: str = entry.data[CONF_SOURCE]
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title
        )

    def _source_entry(self) -> er.RegistryEntry | None:
        return er.async_get(self.hass).async_get(self._source_registry_id)

    async def async_update(self) -> None:
        """Read the foreground app and its media session over the existing ADB connection."""
        source_entry = self._source_entry()
        source_state = (
            self.hass.states.get(source_entry.entity_id) if source_entry else None
        )
        if source_state is None or source_state.state in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            self._attr_available = False
            return

        self._attr_available = True
        if source_state.state == STATE_OFF:
            self._attr_state = MediaPlayerState.OFF
            self._attr_app_id = None
            return

        config_entry = (
            self.hass.config_entries.async_get_entry(source_entry.config_entry_id)
            if source_entry and source_entry.config_entry_id
            else None
        )
        if config_entry is None or config_entry.state is not ConfigEntryState.LOADED:
            self._attr_available = False
            return

        aftv = config_entry.runtime_data.aftv
        try:
            app_output = await aftv.adb_shell(CMD_CURRENT_APP)
            session_output = await aftv.adb_shell(CMD_SESSIONS)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("ADB query failed", exc_info=True)
            self._attr_available = False
            return

        if app_output is None and session_output is None:
            self._attr_available = False
            return

        app_id = _parse_app(app_output or "")
        playback = _parse_session_states(session_output or "").get(app_id)

        self._attr_app_id = app_id
        if playback == PLAYBACK_PLAYING:
            self._attr_state = MediaPlayerState.PLAYING
        elif playback == PLAYBACK_PAUSED:
            self._attr_state = MediaPlayerState.PAUSED
        else:
            self._attr_state = MediaPlayerState.IDLE

    async def _forward(self, service: str) -> None:
        source_entry = self._source_entry()
        if source_entry is None:
            return
        await self.hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            service,
            {ATTR_ENTITY_ID: source_entry.entity_id},
            blocking=True,
        )

    async def async_media_play(self) -> None:
        """Play via the Android Debug Bridge player."""
        await self._forward("media_play")

    async def async_media_pause(self) -> None:
        """Pause via the Android Debug Bridge player."""
        await self._forward("media_pause")
