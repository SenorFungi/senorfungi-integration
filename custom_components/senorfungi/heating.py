"""Heating controller runtime in shadow mode.

Reads the zones and the outdoor temperature from Home Assistant, asks the
controller logic what it would request from the boiler, and compares that with
what the bus thermostat requests. Nothing is written to the boiler.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BOOST_OFFSET,
    CONF_DESIGN_FLOW,
    CONF_DESIGN_OUTDOOR,
    CONF_MAX_FLOW,
    CONF_MIN_FLOW,
    CONF_MIN_OFF,
    CONF_MIN_ON,
    CONF_OUTDOOR_ENTITY,
    CONF_PRESENCE_TIMEOUT,
    CONF_ROOM_GAIN,
    CONF_SUMMER_LIMIT,
    CONF_THERMOSTAT_ID,
    CONF_ZONE_CLIMATE,
    CONF_ZONE_COMFORT,
    CONF_ZONE_ECO,
    CONF_ZONE_HAS_VALVE,
    CONF_ZONE_NAME,
    CONF_ZONE_TEMPERATURE,
    CONF_ZONE_VALVE_ENTITY,
    CONF_ZONE_WEIGHT,
    CONTROL_INTERVAL,
    CONTROLLER_DEFAULTS,
    ZONE_DEFAULTS,
)
from .ems_bus import Telegram, thermostat_request
from .ems_logger import EmsBusLogger
from .heating_control import Decision, HeatingController, Params, ZoneInput

_LOGGER = logging.getLogger(__name__)

PRESET_COMFORT = "comfort"
PRESET_ECO = "eco"


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except TypeError, ValueError:
        return None


@dataclass(slots=True)
class ThermostatRequest:
    """The last heat request the bus thermostat sent to the boiler."""

    time: datetime
    flow: float
    output: int
    type_id: int


class Zone:
    """One heating zone (room)."""

    def __init__(self, subentry: ConfigSubentry) -> None:
        """Initialize from the subentry."""
        data = {**ZONE_DEFAULTS, **subentry.data}
        self.subentry_id = subentry.subentry_id
        self.name: str = data[CONF_ZONE_NAME]
        self.climate_entity: str | None = data.get(CONF_ZONE_CLIMATE) or None
        self.temperature_entity: str | None = data.get(CONF_ZONE_TEMPERATURE) or None
        self.valve_entity: str | None = data.get(CONF_ZONE_VALVE_ENTITY) or None
        self.has_valve = bool(data[CONF_ZONE_HAS_VALVE])
        self.weight = float(data[CONF_ZONE_WEIGHT])
        self.comfort = float(data[CONF_ZONE_COMFORT])
        self.eco = float(data[CONF_ZONE_ECO])
        # own target, used when the zone has no climate entity (restored by the climate entity)
        self.preset: str | None = PRESET_COMFORT
        self.target: float | None = self.comfort
        self.enabled = True
        # last values read
        self.current: float | None = None
        self.valve: float | None = None
        self.effective_target: float | None = None

    @property
    def owns_target(self) -> bool:
        """True if the integration provides the target (no climate entity)."""
        return self.climate_entity is None

    def read(self, hass: HomeAssistant) -> ZoneInput:
        """Read the zone's values from Home Assistant."""
        current = target = None
        if self.climate_entity and (state := hass.states.get(self.climate_entity)):
            if state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN, "off"):
                current = _float(state.attributes.get("current_temperature"))
                target = _float(state.attributes.get("temperature"))
        elif self.owns_target and self.enabled:
            target = self.target
        if self.temperature_entity and (state := hass.states.get(self.temperature_entity)):
            current = _float(state.state)
        self.valve = None
        if self.valve_entity and (state := hass.states.get(self.valve_entity)):
            self.valve = _float(state.state)
        self.current, self.effective_target = current, target
        return ZoneInput(self.name, current, target, self.has_valve, self.weight)


class HeatingRuntime:
    """Compute decisions every CONTROL_INTERVAL seconds and follow the bus thermostat."""

    def __init__(
        self,
        hass: HomeAssistant,
        controller: ConfigSubentry,
        zones: list[ConfigSubentry],
        ems: EmsBusLogger | None,
    ) -> None:
        """Initialize from the controller and zone subentries."""
        data = {**CONTROLLER_DEFAULTS, **controller.data}
        self.hass = hass
        self.subentry_id = controller.subentry_id
        self.outdoor_entity: str = data[CONF_OUTDOOR_ENTITY]
        tid = data.get(CONF_THERMOSTAT_ID) or ""
        self.thermostat_id: int | None = int(tid, 16) if tid else None
        self.presence_timeout = timedelta(seconds=int(data[CONF_PRESENCE_TIMEOUT]))
        self.controller = HeatingController(
            Params(
                design_outdoor=float(data[CONF_DESIGN_OUTDOOR]),
                design_flow=float(data[CONF_DESIGN_FLOW]),
                min_flow=float(data[CONF_MIN_FLOW]),
                max_flow=float(data[CONF_MAX_FLOW]),
                room_gain=float(data[CONF_ROOM_GAIN]),
                summer_limit=float(data[CONF_SUMMER_LIMIT]),
                boost_offset=float(data[CONF_BOOST_OFFSET]),
                min_on=timedelta(minutes=float(data[CONF_MIN_ON])),
                min_off=timedelta(minutes=float(data[CONF_MIN_OFF])),
            )
        )
        self.zones = [Zone(z) for z in zones]
        self.ems = ems
        self.outdoor: float | None = None
        self.decision: Decision | None = None
        self.updated: datetime | None = None
        self.thermostat_seen: datetime | None = None
        self.thermostat_request: ThermostatRequest | None = None
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []

    @property
    def watches_thermostat(self) -> bool:
        """True if the thermostat can be followed on the bus."""
        return self.ems is not None and self.thermostat_id is not None

    @property
    def thermostat_present(self) -> bool | None:
        """True if the thermostat sent a telegram within the presence timeout. None if unknown."""
        if not self.watches_thermostat:
            return None
        if self.thermostat_seen is None:
            return False
        return dt_util.now() - self.thermostat_seen <= self.presence_timeout

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> CALLBACK_TYPE:
        """Register an entity update callback."""
        self._listeners.append(update)

        def remove() -> None:
            self._listeners.remove(update)

        return remove

    @callback
    def async_start(self) -> None:
        """Start the timer and follow the bus."""
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=CONTROL_INTERVAL)))
        if self.watches_thermostat and self.ems is not None:
            self._unsubs.append(self.ems.async_add_telegram_listener(self._on_telegram))

    @callback
    def async_stop(self) -> None:
        """Stop the timer and the bus listener."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def _on_telegram(self, telegram: Telegram) -> None:
        if telegram.src != self.thermostat_id:
            return
        self.thermostat_seen = telegram.time
        if telegram.op == "W" and (req := thermostat_request(telegram.type_id, telegram.offset, telegram.data_bytes)):
            self.thermostat_request = ThermostatRequest(telegram.time, req[0], req[1], telegram.type_id)

    async def _tick(self, _now: datetime | None = None) -> None:
        self.async_update()

    @callback
    def async_update(self) -> None:
        """Compute a new decision and update the entities."""
        state = self.hass.states.get(self.outdoor_entity)
        self.outdoor = _float(state.state) if state else None
        inputs = [zone.read(self.hass) for zone in self.zones]
        now = dt_util.now()
        self.decision = self.controller.decide(self.outdoor, inputs, now)
        self.updated = now
        for update in list(self._listeners):
            update()
