"""Binary sensors of the SenorFungi integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SenorFungiConfigEntry
from .entity import EmsBusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SenorFungiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    if (ems := entry.runtime_data.ems) is not None:
        async_add_entities([EmsBusConnectedSensor(entry, ems, "connected")])


class EmsBusConnectedSensor(EmsBusEntity, BinarySensorEntity):
    """Whether the telnet session to EMS-ESP is open and watching."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool:
        """Return true when connected."""
        return self.ems.stats.connected

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Show what is watched."""
        return {"watch": self.ems.client.watch_command}
