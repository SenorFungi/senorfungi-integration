"""Heating controller logic, independent of Home Assistant.

Computes the flow temperature a boiler should be asked for, from the outdoor
temperature (heating curve) and the demand of the heating zones (rooms).
This module only decides; it never talks to a boiler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

CURVE_EXPONENT = 1.3  # typical radiator exponent


class Reason(StrEnum):
    """Why the controller decided what it decided."""

    NO_OUTDOOR = "no_outdoor"  # outdoor temperature unknown
    NO_ZONES = "no_zones"  # no zone with a usable temperature and target
    SUMMER = "summer"  # outdoor temperature at or above the summer limit
    SATISFIED = "satisfied"  # every zone is warm enough
    BLOCKED = "blocked"  # a zone without a valve is too warm
    MIN_OFF = "min_off"  # demand, but the minimum pause isn't over
    MIN_ON = "min_on"  # no demand, but the minimum burn time isn't over
    HEATING = "heating"
    BOOST = "boost"  # large demand: curve plus boost offset
    FROST = "frost"  # a zone is below the frost limit


@dataclass(slots=True)
class Params:
    """Settings of the controller (all temperatures in °C, differences in K)."""

    design_outdoor: float = -12.0  # outdoor temperature the curve is designed for
    design_flow: float = 55.0  # flow temperature at design_outdoor and 20 °C room
    min_flow: float = 30.0
    max_flow: float = 50.0
    room_gain: float = 5.0  # K of flow per K the neediest zone is below its target
    summer_limit: float = 18.0
    on_threshold: float = 0.2  # start when a zone is this far below its target
    off_threshold: float = -0.2  # stop when every zone is at least this far above (negative = above)
    overshoot_block: float = 0.5  # a zone without valve this far above its target stops heating
    boost_threshold: float = 1.0
    boost_offset: float = 10.0
    min_on: timedelta = timedelta(minutes=10)
    min_off: timedelta = timedelta(minutes=10)
    frost_limit: float = 5.0


@dataclass(slots=True)
class ZoneInput:
    """One room as the controller sees it."""

    name: str
    current: float | None
    target: float | None
    has_valve: bool = True
    weight: float = 1.0  # demand is multiplied by this (0..2)

    @property
    def usable(self) -> bool:
        """True if temperature and target are known."""
        return self.current is not None and self.target is not None

    @property
    def deficit(self) -> float:
        """K below the target (negative: above)."""
        assert self.current is not None and self.target is not None
        return (self.target - self.current) * self.weight


@dataclass(slots=True)
class Decision:
    """What the controller would ask the boiler for."""

    heat: bool
    flow: float  # 0 when not heating
    reason: Reason
    zone: str | None = None  # the zone that decided (neediest or blocking)
    demand: float | None = None  # deficit of the neediest zone, K
    curve: float | None = None  # flow from the curve alone


def curve_flow(params: Params, outdoor: float, room_target: float) -> float:
    """Flow temperature from the heating curve.

    The curve passes through (design_outdoor, design_flow) for a 20 °C room and
    reaches the room temperature when outdoor equals it. Other room targets
    shift the curve in parallel.
    """
    ref = 20.0
    if outdoor >= ref:
        flow = ref
    else:
        ratio = (ref - outdoor) / (ref - params.design_outdoor)
        flow = ref + (params.design_flow - ref) * ratio ** (1 / CURVE_EXPONENT)
    return flow + (room_target - ref)


@dataclass(slots=True)
class HeatingController:
    """Stateful controller: hysteresis and minimum burn and pause times."""

    params: Params = field(default_factory=Params)
    heating: bool = False
    since: datetime | None = None  # when `heating` last changed

    def _switch(self, heat: bool, now: datetime) -> None:
        if heat != self.heating:
            self.heating = heat
            self.since = now

    def _held(self, now: datetime, minimum: timedelta) -> bool:
        """True while the current state is younger than minimum."""
        return self.since is not None and now - self.since < minimum

    def decide(self, outdoor: float | None, zones: list[ZoneInput], now: datetime) -> Decision:
        """Return the decision for this moment and update the state."""
        p = self.params
        usable = [z for z in zones if z.usable]
        if not usable:
            self._switch(False, now)
            return Decision(False, 0.0, Reason.NO_ZONES)

        frozen = [z for z in usable if z.current is not None and z.current < p.frost_limit]
        if frozen:
            self._switch(True, now)
            return Decision(True, p.min_flow, Reason.FROST, zone=frozen[0].name)

        if outdoor is None:
            self._switch(False, now)
            return Decision(False, 0.0, Reason.NO_OUTDOOR)

        neediest = max(usable, key=lambda z: z.deficit)
        demand = round(neediest.deficit, 2)
        target = neediest.target if neediest.target is not None else 20.0
        curve = round(curve_flow(p, outdoor, target), 1)

        def off(reason: Reason, zone: str | None) -> Decision:
            if self.heating and self._held(now, p.min_on):
                return on(Reason.MIN_ON)
            self._switch(False, now)
            return Decision(False, 0.0, reason, zone=zone, demand=demand, curve=curve)

        def on(reason: Reason) -> Decision:
            flow = curve + p.room_gain * max(demand, 0.0)
            if demand >= p.boost_threshold:
                flow += p.boost_offset
                reason = Reason.BOOST if reason == Reason.HEATING else reason
            flow = round(min(max(flow, p.min_flow), p.max_flow), 1)
            self._switch(True, now)
            return Decision(True, flow, reason, zone=neediest.name, demand=demand, curve=curve)

        if outdoor >= p.summer_limit:
            return off(Reason.SUMMER, None)

        blocking = [z for z in usable if not z.has_valve and -z.deficit >= p.overshoot_block]
        if blocking:
            return off(Reason.BLOCKED, blocking[0].name)

        if self.heating:
            if demand <= p.off_threshold:
                return off(Reason.SATISFIED, neediest.name)
            return on(Reason.HEATING)

        if demand >= p.on_threshold:
            if self._held(now, p.min_off):
                return Decision(False, 0.0, Reason.MIN_OFF, zone=neediest.name, demand=demand, curve=curve)
            return on(Reason.HEATING)
        return Decision(False, 0.0, Reason.SATISFIED, zone=neediest.name, demand=demand, curve=curve)
