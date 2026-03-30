"""DataUpdateCoordinator for ZoneTouch 3."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .protocol import DeviceInfo, DeviceState, ZoneTouch3Client

_LOGGER = logging.getLogger(__name__)


def build_device_info(dev_info: DeviceInfo, entry_id: str) -> dict:
    """Build a HA device info dict from a DeviceInfo object."""
    return {
        "identifiers": {(DOMAIN, dev_info.device_id or entry_id)},
        "name": f"{dev_info.owner}'s ZT3" if dev_info.owner else "ZoneTouch 3",
        "manufacturer": "Polyaire",
        "model": "ZoneTouch 3",
        "serial_number": dev_info.device_id or None,
        "sw_version": dev_info.firmware_version or None,
        "hw_version": dev_info.hardware_version or None,
    }


class ZoneTouch3Coordinator(DataUpdateCoordinator[DeviceState]):
    """Coordinator to manage polling the ZoneTouch 3 device."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: ZoneTouch3Client,
        poll_interval: int,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
        )
        self.client = client

    async def _async_update_data(self) -> DeviceState:
        """Fetch data from the device."""
        try:
            new = await self.client.async_query_state()
        except ConnectionError as err:
            raise UpdateFailed(f"Error communicating with ZoneTouch 3: {err}") from err

        self._log_changes(new)
        return new

    def _log_changes(self, new: DeviceState) -> None:
        """Log any state changes since the last poll."""
        prev = self.data
        if prev is None:
            _LOGGER.debug(
                "Initial poll: %d zone(s), temp=%s°C",
                len(new.zones), new.temperature,
            )
            return

        if new.temperature != prev.temperature:
            _LOGGER.debug(
                "Temperature: %s°C -> %s°C", prev.temperature, new.temperature
            )

        for zone_id, zone in new.zones.items():
            old = prev.zones.get(zone_id)
            if old is None:
                _LOGGER.debug("Zone %d (%s): appeared", zone_id, zone.name)
                continue
            changes = []
            if zone.is_on != old.is_on:
                changes.append(f"{'ON' if zone.is_on else 'OFF'} (was {'ON' if old.is_on else 'OFF'})")
            if zone.percent != old.percent:
                changes.append(f"{old.percent}% -> {zone.percent}%")
            if zone.spill != old.spill:
                changes.append(f"spill={'on' if zone.spill else 'off'}")
            if zone.turbo != old.turbo:
                changes.append(f"turbo={'on' if zone.turbo else 'off'}")
            if changes:
                _LOGGER.debug("Zone %d (%s): %s", zone_id, zone.name, ", ".join(changes))

        for zone_id in prev.zones:
            if zone_id not in new.zones:
                _LOGGER.debug("Zone %d: disappeared", zone_id)
