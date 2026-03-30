"""Number platform for ZoneTouch 3 zones."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ZoneTouch3Coordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ZoneTouch 3 zone number entities."""
    coordinator: ZoneTouch3Coordinator = hass.data[DOMAIN][entry.entry_id]

    known_zones: set[int] = set()

    @callback
    def _async_add_new_zones() -> None:
        """Add entities for any zones discovered in the latest poll."""
        if not coordinator.data:
            return
        new_zone_ids = set(coordinator.data.zones) - known_zones
        if not new_zone_ids:
            return
        known_zones.update(new_zone_ids)
        async_add_entities(
            ZoneTouch3ZoneNumber(coordinator, zone_id, entry)
            for zone_id in new_zone_ids
        )

    # Add entities from the first poll
    _async_add_new_zones()

    # Listen for future polls that may discover additional zones
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_zones))


class ZoneTouch3ZoneNumber(CoordinatorEntity[ZoneTouch3Coordinator], NumberEntity):
    """A number entity representing a ZoneTouch 3 zone damper percentage."""

    _attr_has_entity_name = True
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:hvac"

    def __init__(
        self,
        coordinator: ZoneTouch3Coordinator,
        zone_id: int,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the zone number entity."""
        super().__init__(coordinator)
        self._zone_id = zone_id
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone_id}"

        # Use the zone name from the device if available
        zone = coordinator.data.zones.get(zone_id)
        self._attr_name = zone.name if zone else f"Zone {zone_id}"

        dev_info = coordinator.data.device_info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, dev_info.device_id or entry.entry_id)},
            "name": f"{dev_info.owner}'s ZT3" if dev_info.owner else "ZoneTouch 3",
            "manufacturer": "Polyaire",
            "model": "ZoneTouch 3",
            "serial_number": dev_info.device_id or None,
            "sw_version": dev_info.firmware_version or None,
            "hw_version": dev_info.hardware_version or None,
        }

    @property
    def native_value(self) -> float | None:
        """Return the current zone percentage."""
        if self.coordinator.data and self._zone_id in self.coordinator.data.zones:
            return self.coordinator.data.zones[self._zone_id].percent
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set the zone percentage."""
        await self.coordinator.client.async_set_zone(
            self._zone_id, int(value)
        )
        await self.coordinator.async_request_refresh()
