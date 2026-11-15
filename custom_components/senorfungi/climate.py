"""Climate entities for heating zones without a climate entity of their own.

They hold the zone's target temperature and presets (comfort, eco). In shadow
mode they control nothing: the controller only uses the target to compute
what it would ask the boiler for.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import SenorFungiConfigEntry
from .const import DOMAIN
from .heating import PRESET_COMFORT, PRESET_ECO, HeatingRuntime, Zone


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SenorFungiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one climate entity per zone that owns its target."""
    if (heating := entry.runtime_data.heating) is None:
        return
    for zone in heating.zones:
        if zone.owns_target:
            async_add_entities([ZoneClimate(entry, heating, zone)], config_subentry_id=zone.subentry_id)


class ZoneClimate(ClimateEntity, RestoreEntity):
    """Target temperature and presets of a heating zone."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_translation_key = "heating_zone"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = [PRESET_COMFORT, PRESET_ECO]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_target_temperature_step = 0.5
    _attr_min_temp = 5.0
    _attr_max_temp = 30.0

    def __init__(self, entry: SenorFungiConfigEntry, heating: HeatingRuntime, zone: Zone) -> None:
        """Initialize the entity."""
        self.heating = heating
        self.zone = zone
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone.subentry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_zone_{zone.subentry_id}")},
            name=zone.name,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Restore target and preset, then follow the controller."""
        if (last := await self.async_get_last_state()) is not None:
            if last.attributes.get("preset_mode") in self._attr_preset_modes:
                self.zone.preset = last.attributes["preset_mode"]
            if (temp := last.attributes.get(ATTR_TEMPERATURE)) is not None:
                self.zone.target = float(temp)
            self.zone.enabled = last.state != HVACMode.OFF
        self.async_on_remove(self.heating.async_add_listener(self.async_write_ha_state))
        self.heating.async_update()

    @property
    def hvac_mode(self) -> HVACMode:
        """Heat or off."""
        return HVACMode.HEAT if self.zone.enabled else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        """Heating while the controller would heat for this zone."""
        if not self.zone.enabled:
            return HVACAction.OFF
        d = self.heating.decision
        return HVACAction.HEATING if d is not None and d.heat else HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        """Temperature of the zone's sensor."""
        return self.zone.current

    @property
    def target_temperature(self) -> float | None:
        """Target of the zone."""
        return self.zone.target

    @property
    def preset_mode(self) -> str | None:
        """Comfort, eco, or None after a manual target change."""
        return self.zone.preset

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a target (clears the preset if it doesn't match)."""
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        self.zone.target = float(temp)
        presets = {PRESET_COMFORT: self.zone.comfort, PRESET_ECO: self.zone.eco}
        self.zone.preset = next((p for p, t in presets.items() if t == self.zone.target), None)
        self.heating.async_update()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Comfort or eco: use the zone's configured temperature."""
        self.zone.preset = preset_mode
        self.zone.target = self.zone.comfort if preset_mode == PRESET_COMFORT else self.zone.eco
        self.heating.async_update()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Off: the zone asks for no heat."""
        self.zone.enabled = hvac_mode != HVACMode.OFF
        self.heating.async_update()
