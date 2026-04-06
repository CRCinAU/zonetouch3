"""Sensor platform for ZoneTouch 3 temperature."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ZoneTouch3Coordinator, build_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ZoneTouch 3 temperature sensor."""
    coordinator: ZoneTouch3Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ZoneTouch3TemperatureSensor(coordinator, entry)])


class ZoneTouch3TemperatureSensor(
    CoordinatorEntity[ZoneTouch3Coordinator], SensorEntity
):
    """Sensor entity for the ZoneTouch 3 temperature reading."""

    _attr_has_entity_name = True
    _attr_name = "Temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:thermometer"

    def __init__(
        self,
        coordinator: ZoneTouch3Coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the temperature sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_temperature"

        if coordinator.data:
            self._attr_device_info = build_device_info(
                coordinator.data.device_info, entry.entry_id
            )

    @property
    def native_value(self) -> float | None:
        """Return the current temperature."""
        if self.coordinator.data:
            return self.coordinator.data.temperature
        return None
