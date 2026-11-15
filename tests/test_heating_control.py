"""Tests for the Home Assistant independent heating controller logic."""

import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "heating_control", Path(__file__).parents[1] / "custom_components/senorfungi/heating_control.py"
)
hc = importlib.util.module_from_spec(spec)
sys.modules["heating_control"] = hc
spec.loader.exec_module(hc)

T0 = datetime(2026, 11, 15, 6, 0)


def zone(current, target=21.0, valve=True, name="a", weight=1.0):
    return hc.ZoneInput(name, current, target, valve, weight)


def test_curve_passes_design_point_and_room_temperature() -> None:
    p = hc.Params(design_outdoor=-12, design_flow=55)
    assert hc.curve_flow(p, -12, 20) == 55
    assert hc.curve_flow(p, 20, 20) == 20
    assert 20 < hc.curve_flow(p, 5, 20) < 55
    # a warmer room target shifts the curve up
    assert hc.curve_flow(p, 5, 22) == hc.curve_flow(p, 5, 20) + 2


def test_starts_on_demand_and_limits_flow() -> None:
    c = hc.HeatingController(hc.Params(max_flow=50, min_flow=30))
    d = c.decide(5.0, [zone(20.5)], T0)
    assert d.heat and d.reason == hc.Reason.HEATING
    assert 30 <= d.flow <= 50
    assert d.zone == "a" and d.demand == 0.5


def test_no_start_inside_the_hysteresis() -> None:
    c = hc.HeatingController()
    d = c.decide(5.0, [zone(20.9)], T0)
    assert not d.heat and d.reason == hc.Reason.SATISFIED and d.flow == 0


def test_keeps_heating_until_off_threshold_and_min_on() -> None:
    c = hc.HeatingController(hc.Params(min_on=timedelta(minutes=10)))
    assert c.decide(5.0, [zone(20.0)], T0).heat
    # target reached, but minimum burn time not over
    d = c.decide(5.0, [zone(21.5)], T0 + timedelta(minutes=5))
    assert d.heat and d.reason == hc.Reason.MIN_ON
    d = c.decide(5.0, [zone(21.5)], T0 + timedelta(minutes=11))
    assert not d.heat and d.reason == hc.Reason.SATISFIED


def test_min_off_delays_a_restart() -> None:
    c = hc.HeatingController(hc.Params(min_on=timedelta(0), min_off=timedelta(minutes=10)))
    c.decide(5.0, [zone(20.0)], T0)
    c.decide(5.0, [zone(21.5)], T0 + timedelta(minutes=1))
    d = c.decide(5.0, [zone(20.0)], T0 + timedelta(minutes=5))
    assert not d.heat and d.reason == hc.Reason.MIN_OFF
    assert c.decide(5.0, [zone(20.0)], T0 + timedelta(minutes=12)).heat


def test_neediest_zone_decides_and_zone_without_valve_blocks() -> None:
    c = hc.HeatingController()
    zones = [zone(21.0, name="big", valve=False), zone(18.0, name="bath")]
    d = c.decide(5.0, zones, T0)
    assert d.heat and d.zone == "bath" and d.demand == 3.0
    c2 = hc.HeatingController()
    zones = [zone(22.0, name="big", valve=False), zone(18.0, name="bath")]
    d = c2.decide(5.0, zones, T0)
    assert not d.heat and d.reason == hc.Reason.BLOCKED and d.zone == "big"


def test_boost_adds_offset() -> None:
    p = hc.Params(max_flow=70, room_gain=0, boost_threshold=1.0, boost_offset=10)
    c = hc.HeatingController(p)
    d = c.decide(5.0, [zone(19.5)], T0)
    assert d.reason == hc.Reason.BOOST
    assert d.flow == round(d.curve + 10, 1)


def test_summer_frost_and_missing_values() -> None:
    assert hc.HeatingController().decide(19.0, [zone(18.0)], T0).reason == hc.Reason.SUMMER
    d = hc.HeatingController().decide(19.0, [zone(4.0)], T0)
    assert d.heat and d.reason == hc.Reason.FROST
    assert hc.HeatingController().decide(None, [zone(18.0)], T0).reason == hc.Reason.NO_OUTDOOR
    assert hc.HeatingController().decide(5.0, [zone(None)], T0).reason == hc.Reason.NO_ZONES


def test_weight_scales_demand() -> None:
    d = hc.HeatingController().decide(5.0, [zone(20.0, weight=0.5)], T0)
    assert d.demand == 0.5


def test_thermostat_request_from_telegrams() -> None:
    assert hc.thermostat_request(0x02E0, 0, [0x01, 0x28, 0x64, 0x00, 0x01]) == (40.0, 100)
    assert hc.thermostat_request(0x02E0, 1, [0x22, 0x50]) == (34.0, 80)
    assert hc.thermostat_request(0x1A, 0, [0x2D, 0x64, 0x00]) == (45.0, 100)
    assert hc.thermostat_request(0x02E0, 3, [0x00]) is None
    assert hc.thermostat_request(0x02A5, 0, [0x00, 0x01]) is None
