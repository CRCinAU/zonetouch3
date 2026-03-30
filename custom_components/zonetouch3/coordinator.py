"""DataUpdateCoordinator for ZoneTouch 3."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .protocol import DeviceState, ZoneTouch3Client

_LOGGER = logging.getLogger(__name__)


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
