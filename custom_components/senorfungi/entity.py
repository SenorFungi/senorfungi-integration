"""Base entity for the EMS bus logger."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from . import SenorFungiConfigEntry
from .const import DOMAIN
from .ems_logger import EmsBusLogger
from .heating import HeatingRuntime


class EmsBusEntity(Entity):
    """Entity that shows values of the EMS bus logger."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: SenorFungiConfigEntry, ems: EmsBusLogger, key: str) -> None:
        """Initialize the entity."""
        self.ems = ems
        self._attr_unique_id = f"{entry.entry_id}_ems_{key}"
        self._attr_translation_key = f"ems_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_ems_bus")},
            name="EMS bus logger",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"http://{ems.client.host}",
        )

    async def async_added_to_hass(self) -> None:
        """Follow the logger's updates."""
        self.async_on_remove(self.ems.async_add_listener(self.async_write_ha_state))


class EmsThermostatEntity(EmsBusEntity):
    """Entity for the bus thermostat the logger follows (presence, heat request).

    Updates only when presence or the request changes, not with the logger's statistics.
    """

    def __init__(self, entry: SenorFungiConfigEntry, ems: EmsBusLogger, key: str) -> None:
        """Initialize the entity."""
        super().__init__(entry, ems, key)
        self.thermostat = ems.stats.thermostat
        self._attr_translation_placeholders = {"id": f"0x{self.thermostat.dev_id:02X}"}

    async def async_added_to_hass(self) -> None:
        """Follow the thermostat's changes."""
        self.async_on_remove(self.ems.async_add_thermostat_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        """Unavailable while presence is unknown (console down, or not watched for a whole timeout yet)."""
        return self.ems.thermostat_present is not None


class HeatingEntity(Entity):
    """Entity of the heating controller (shadow mode)."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: SenorFungiConfigEntry, heating: HeatingRuntime, key: str) -> None:
        """Initialize the entity."""
        self.heating = heating
        self._attr_unique_id = f"{entry.entry_id}_heating_{key}"
        self._attr_translation_key = f"heating_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_heating")},
            name="Heating controller",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Follow the controller's updates."""
        self.async_on_remove(self.heating.async_add_listener(self.async_write_ha_state))
