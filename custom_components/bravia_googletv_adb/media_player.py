"""Media player reporting playback state read from the TV's media sessions."""

from __future__ import annotations

from datetime import timedelta
import hashlib
import logging
import re
from xml.sax.saxutils import escape

from androidtv.constants import APPS

from homeassistant.components.media_player import (
    DATA_COMPONENT,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN
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
# Some apps (e.g. Tennis TV) leave their media session empty but still publish an audio player.
CMD_UID = "cmd package list packages -U {}"
CMD_AUDIO = "dumpsys audio | grep AudioPlaybackConfiguration | grep -v SoundPool"

RE_UID = re.compile(r"uid:(\d+)")
RE_AUDIO = re.compile(r"u/pid:(\d+)/\d+ state:(\w+)")
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


def _placeholder_svg(app_id: str, app_name: str) -> bytes:
    """Stand-in art, coloured per app, until real app art is added."""
    hue = int(hashlib.sha256(app_id.encode()).hexdigest()[:4], 16) % 360
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512">'
        f'<rect width="512" height="512" fill="hsl({hue},45%,30%)"/>'
        '<text x="256" y="300" font-size="220" font-family="sans-serif" '
        f'fill="white" text-anchor="middle">{escape(app_name[:1].upper())}</text>'
        '<text x="256" y="440" font-size="44" font-family="sans-serif" '
        f'fill="white" text-anchor="middle">{escape(app_name)}</text></svg>'
    ).encode()


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
        self._uids: dict[str, int] = {}
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title
        )

    def _source_entry(self) -> er.RegistryEntry | None:
        return er.async_get(self.hass).async_get(self._source_registry_id)

    def _stock_config_entry(self, source_entry: er.RegistryEntry) -> ConfigEntry | None:
        if not source_entry.config_entry_id:
            return None
        entry = self.hass.config_entries.async_get_entry(source_entry.config_entry_id)
        return entry if entry and entry.state is ConfigEntryState.LOADED else None

    def _stock_entity(self, source_entry: er.RegistryEntry) -> MediaPlayerEntity | None:
        component = self.hass.data.get(DATA_COMPONENT)
        return component.get_entity(source_entry.entity_id) if component else None

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
            self._attr_app_name = None
            self._attr_media_image_hash = None
            return

        config_entry = self._stock_config_entry(source_entry)
        if config_entry is None:
            self._attr_available = False
            return

        aftv = config_entry.runtime_data.aftv
        try:
            app_output = await aftv.adb_shell(CMD_CURRENT_APP)
            session_output = await aftv.adb_shell(CMD_SESSIONS)
            if app_output is None and session_output is None:
                self._attr_available = False
                return

            app_id = _parse_app(app_output or "")
            playback = _parse_session_states(session_output or "").get(app_id)
            if app_id and playback not in (PLAYBACK_PLAYING, PLAYBACK_PAUSED):
                playback = await self._audio_playback(aftv, app_id)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("ADB query failed", exc_info=True)
            self._attr_available = False
            return

        _LOGGER.debug("app=%s playback=%s", app_id, playback)
        self._attr_app_id = app_id
        apps = {**APPS, **config_entry.options.get("apps", {})}
        self._attr_app_name = apps.get(app_id, app_id) if app_id else None

        stock = self._stock_entity(source_entry)
        if stock and stock.media_image_hash:
            self._attr_media_image_hash = stock.media_image_hash
        elif app_id:
            self._attr_media_image_hash = hashlib.sha256(app_id.encode()).hexdigest()[:16]
        else:
            self._attr_media_image_hash = None

        if playback == PLAYBACK_PLAYING:
            self._attr_state = MediaPlayerState.PLAYING
        elif playback == PLAYBACK_PAUSED:
            self._attr_state = MediaPlayerState.PAUSED
        else:
            self._attr_state = MediaPlayerState.IDLE

    async def _audio_playback(self, aftv, app_id: str) -> int | None:
        """Derive playback from the app's audio players."""
        if app_id not in self._uids:
            match = RE_UID.search(await aftv.adb_shell(CMD_UID.format(app_id)) or "")
            if not match:
                return None
            self._uids[app_id] = int(match.group(1))

        output = await aftv.adb_shell(CMD_AUDIO) or ""
        states = {
            state
            for uid, state in RE_AUDIO.findall(output)
            if int(uid) == self._uids[app_id]
        }
        if "started" in states:
            return PLAYBACK_PLAYING
        if "paused" in states:
            return PLAYBACK_PAUSED
        return None

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        """Return the stock integration's screen capture, else per-app placeholder art."""
        source_entry = self._source_entry()
        stock = self._stock_entity(source_entry) if source_entry else None
        if stock and stock.media_image_hash:
            return await stock.async_get_media_image()
        if self._attr_app_id:
            name = self._attr_app_name or self._attr_app_id
            return _placeholder_svg(self._attr_app_id, name), "image/svg+xml"
        return None, None

    async def _toggle_play_pause(self, expected: MediaPlayerState) -> None:
        # Apps like Tennis TV ignore the discrete play/pause keys; only the toggle works.
        source_entry = self._source_entry()
        config_entry = self._stock_config_entry(source_entry) if source_entry else None
        if config_entry is None:
            return
        await config_entry.runtime_data.aftv.media_play_pause()
        self._attr_state = expected
        self.async_write_ha_state()

    async def async_media_play(self) -> None:
        """Resume playback if paused."""
        if self._attr_state == MediaPlayerState.PAUSED:
            await self._toggle_play_pause(MediaPlayerState.PLAYING)

    async def async_media_pause(self) -> None:
        """Pause playback if playing."""
        if self._attr_state == MediaPlayerState.PLAYING:
            await self._toggle_play_pause(MediaPlayerState.PAUSED)
