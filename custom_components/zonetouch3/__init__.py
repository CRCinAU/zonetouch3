"""The ZoneTouch 3 integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_HOST, CONF_POLL_INTERVAL, CONF_PORT, DEFAULT_POLL_INTERVAL, DOMAIN
from .coordinator import ZoneTouch3Coordinator
from .protocol import ZoneTouch3Client

PLATFORMS: list[Platform] = [Platform.NUMBER, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ZoneTouch 3 from a config entry."""
    client = ZoneTouch3Client(
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
    )

    coordinator = ZoneTouch3Coordinator(
        hass,
        client,
        poll_interval=entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
    )

    await coordinator.async_start()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: ZoneTouch3Coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_stop()
    return unload_ok
