"""Example sensor: shows the installed version of this integration."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.loader import async_get_integration

from . import SenorFungiConfigEntry
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SenorFungiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    integration = await async_get_integration(hass, DOMAIN)
    async_add_entities([VersionSensor(entry, str(integration.version))])


class VersionSensor(SensorEntity):
    """Installed version of the SenorFungi integration."""

    _attr_has_entity_name = True
    _attr_translation_key = "version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, entry: SenorFungiConfigEntry, version: str) -> None:
        """Initialize the sensor."""
        self._attr_unique_id = f"{entry.entry_id}_version"
        self._attr_native_value = version
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="SenorFungi",
            entry_type=DeviceEntryType.SERVICE,
            sw_version=version,
        )
