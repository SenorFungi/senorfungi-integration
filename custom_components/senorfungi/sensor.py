"""Sensors of the SenorFungi integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.loader import async_get_integration

from . import SenorFungiConfigEntry
from .const import DOMAIN
from .entity import EmsBusEntity, HeatingEntity
from .heating_control import Reason


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SenorFungiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    integration = await async_get_integration(hass, DOMAIN)
    entities: list[SensorEntity] = [VersionSensor(entry, str(integration.version))]
    if (ems := entry.runtime_data.ems) is not None:
        entities += [
            EmsLastTelegramSensor(entry, ems, "last_telegram"),
            EmsRateSensor(entry, ems, "rate"),
        ]
        if ems.stats.pair is not None:
            entities += [
                EmsPairLastSensor(entry, ems, "pair_last"),
                EmsPairIntervalSensor(entry, ems, "pair_interval"),
            ]
    async_add_entities(entities)
    if (heating := entry.runtime_data.heating) is not None:
        controller: list[SensorEntity] = [
            FlowTargetSensor(entry, heating, "flow_target"),
            ReasonSensor(entry, heating, "reason"),
        ]
        if heating.watches_thermostat:
            controller.append(ThermostatFlowSensor(entry, heating, "thermostat_flow"))
        async_add_entities(controller, config_subentry_id=heating.subentry_id)


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


class EmsLastTelegramSensor(EmsBusEntity, SensorEntity):
    """Time of the last watched telegram."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> datetime | None:
        """Return the time."""
        return self.ems.stats.last_telegram


class EmsRateSensor(EmsBusEntity, SensorEntity):
    """Watched telegrams per minute (mean of the last 5 minutes)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "telegrams/min"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> float | None:
        """Return the rate."""
        return self.ems.stats.per_minute


class _PairSensor(EmsBusEntity, SensorEntity):
    def __init__(self, entry: SenorFungiConfigEntry, ems, key: str) -> None:
        super().__init__(entry, ems, key)
        pair = ems.stats.pair
        self._attr_translation_placeholders = {
            "src": f"0x{pair.src:02X}",
            "dst": f"0x{pair.dst:02X}",
        }


class EmsPairLastSensor(_PairSensor):
    """Time of the last telegram from the source to the destination device."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        """Return the time."""
        return self.ems.stats.pair.last_time

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Last payload per telegram type."""
        return {
            f"0x{type_id:02X}": f"{name} {data}".strip()
            for type_id, (_t, name, data) in sorted(self.ems.stats.pair.last_by_type.items())
        }


class EmsPairIntervalSensor(_PairSensor):
    """Seconds between the last two telegrams of the same type, source to destination."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    @property
    def native_value(self) -> float | None:
        """Return the interval."""
        return self.ems.stats.pair.last_interval


class FlowTargetSensor(HeatingEntity, SensorEntity):
    """Flow temperature the controller would ask for (0 when it wouldn't heat). Shadow mode: not sent."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> float | None:
        """Return the flow target."""
        return None if self.heating.decision is None else self.heating.decision.flow

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Inputs and details of the decision."""
        d = self.heating.decision
        h = self.heating
        attrs: dict[str, Any] = {"mode": "shadow", "outdoor": h.outdoor}
        if d is not None:
            attrs |= {"reason": d.reason.value, "zone": d.zone, "demand": d.demand, "curve": d.curve}
        attrs["zones"] = {
            z.name: {"current": z.current, "target": z.effective_target, "valve": z.valve} for z in h.zones
        }
        return attrs


class ReasonSensor(HeatingEntity, SensorEntity):
    """Why the controller decided as it did."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [r.value for r in Reason]

    @property
    def native_value(self) -> str | None:
        """Return the reason."""
        return None if self.heating.decision is None else self.heating.decision.reason.value


class ThermostatFlowSensor(HeatingEntity, SensorEntity):
    """Flow temperature the bus thermostat last asked the boiler for, for comparison."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> float | None:
        """Return the requested flow, None if no request seen yet."""
        req = self.heating.thermostat_request
        return None if req is None else req.flow

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Burner output and time of the request."""
        req = self.heating.thermostat_request
        if req is None:
            return {}
        return {"output": req.output, "time": req.time.isoformat(), "telegram": f"0x{req.type_id:02X}"}
