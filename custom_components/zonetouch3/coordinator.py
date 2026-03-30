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
            return await self.client.async_query_state()
        except ConnectionError as err:
            raise UpdateFailed(f"Error communicating with ZoneTouch 3: {err}") from err
