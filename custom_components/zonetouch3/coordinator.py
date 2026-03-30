"""DataUpdateCoordinator for ZoneTouch 3."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .protocol import DeviceInfo, DeviceState, ZoneStatus, ZoneTouch3Client

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

    async def async_start(self) -> None:
        """Connect the persistent client and register push callbacks."""
        await self.client.async_connect()
        self.client.register_zone_status_callback(self._on_zone_status_push)
        self.client.register_temperature_callback(self._on_temperature_push)

    async def async_stop(self) -> None:
        """Disconnect the persistent client."""
        await self.client.async_disconnect()

    async def _async_update_data(self) -> DeviceState:
        """Send a FullState keepalive and return the authoritative device state."""
        try:
            new = await self.client.async_query_state()
        except ConnectionError as err:
            raise UpdateFailed(f"Error communicating with ZoneTouch 3: {err}") from err

        self._log_changes(new)
        return new

    @callback
    def _on_zone_status_push(self, updates: dict[int, ZoneStatus]) -> None:
        """Handle an unsolicited 0x21 Group Status push from the device."""
        if self.data is None:
            return

        new_zones = dict(self.data.zones)
        changed = False
        for zone_id, new_zone in updates.items():
            if zone_id not in new_zones:
                continue
            old_zone = new_zones[zone_id]
            new_zone.name = old_zone.name  # preserve name from FullState
            if (
                new_zone.is_on != old_zone.is_on
                or new_zone.percent != old_zone.percent
                or new_zone.spill != old_zone.spill
                or new_zone.turbo != old_zone.turbo
            ):
                _LOGGER.debug(
                    "Zone %d (%s) push update: %s%% %s -> %s%% %s",
                    zone_id, new_zone.name,
                    old_zone.percent, "ON" if old_zone.is_on else "OFF",
                    new_zone.percent, "ON" if new_zone.is_on else "OFF",
                )
                new_zones[zone_id] = new_zone
                changed = True

        if changed:
            self.async_set_updated_data(DeviceState(
                zones=new_zones,
                temperature=self.data.temperature,
                device_info=self.data.device_info,
            ))

    @callback
    def _on_temperature_push(self, temp: float) -> None:
        """Handle an unsolicited 0x2B Temperature push from the device."""
        if self.data is None or self.data.temperature == temp:
            return
        _LOGGER.debug(
            "Temperature push: %s°C -> %s°C", self.data.temperature, temp
        )
        self.async_set_updated_data(DeviceState(
            zones=self.data.zones,
            temperature=temp,
            device_info=self.data.device_info,
        ))

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
