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
from .entity import EmsBusEntity, EmsThermostatEntity, HeatingEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SenorFungiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    if (ems := entry.runtime_data.ems) is not None:
        entities: list[BinarySensorEntity] = [EmsBusConnectedSensor(entry, ems, "connected")]
        if ems.stats.thermostat is not None:
            entities.append(EmsThermostatPresentSensor(entry, ems, "thermostat_present"))
        async_add_entities(entities)
    if (heating := entry.runtime_data.heating) is not None:
        entities: list[BinarySensorEntity] = [HeatRequestSensor(entry, heating, "heat_request")]
        if heating.watches_thermostat:
            entities.append(ThermostatPresentSensor(entry, heating, "thermostat_present"))
        async_add_entities(entities, config_subentry_id=heating.subentry_id)


class EmsThermostatPresentSensor(EmsThermostatEntity, BinarySensorEntity):
    """On from the thermostat's first telegram, off once it has been silent for the presence timeout.

    Unavailable while that can't be known. A controller should treat unavailable as present.
    """

    _attr_device_class = BinarySensorDeviceClass.PRESENCE

    @property
    def is_on(self) -> bool | None:
        """Return true while the thermostat is on the bus."""
        return self.ems.thermostat_present

    @property
    def extra_state_attributes(self) -> dict[str, str | int | None]:
        """Address, last telegram and timeout."""
        th = self.thermostat
        return {
            "thermostat_id": f"0x{th.dev_id:02X}",
            "last_seen": th.seen.isoformat() if th.seen else None,
            "timeout": int(th.timeout.total_seconds()),
        }


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


class HeatRequestSensor(HeatingEntity, BinarySensorEntity):
    """Whether the controller would ask the boiler for heat (shadow mode: nothing is sent)."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    @property
    def is_on(self) -> bool | None:
        """Return true when the controller would heat."""
        return None if self.heating.decision is None else self.heating.decision.heat


class ThermostatPresentSensor(HeatingEntity, BinarySensorEntity):
    """Whether the bus thermostat sent a telegram within the presence timeout."""

    _attr_device_class = BinarySensorDeviceClass.PRESENCE

    @property
    def is_on(self) -> bool | None:
        """Return true while the thermostat is on the bus."""
        return self.heating.thermostat_present

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Address and last telegram."""
        seen = self.heating.thermostat_seen
        return {
            "thermostat_id": f"0x{self.heating.thermostat_id:02X}" if self.heating.thermostat_id is not None else None,
            "last_seen": seen.isoformat() if seen else None,
        }
