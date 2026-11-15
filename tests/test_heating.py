"""Integration tests for the heating controller (shadow mode)."""

from datetime import timedelta

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.senorfungi.const import DOMAIN, SUBENTRY_CONTROLLER, SUBENTRY_ZONE
from custom_components.senorfungi.ems_bus import parse_line

CONTROLLER = ConfigSubentryData(
    data={"outdoor_entity": "sensor.outdoor", "thermostat_id": "18", "max_flow": 50, "min_flow": 30},
    subentry_type=SUBENTRY_CONTROLLER,
    title="Heating controller",
    unique_id=None,
)
ZONE_CLIMATE = ConfigSubentryData(
    data={"name": "Bath", "climate_entity": "climate.bath", "has_valve": True},
    subentry_type=SUBENTRY_ZONE,
    title="Bath",
    unique_id=None,
)
ZONE_OWN = ConfigSubentryData(
    data={"name": "Living room", "temperature_entity": "sensor.living", "has_valve": False, "comfort_temperature": 21},
    subentry_type=SUBENTRY_ZONE,
    title="Living room",
    unique_id=None,
)


def _entity(hass: HomeAssistant, unique_suffix: str) -> str:
    reg = er.async_get(hass)
    for e in reg.entities.values():
        if e.platform == DOMAIN and e.unique_id.endswith(unique_suffix):
            return e.entity_id
    raise AssertionError(f"no entity *{unique_suffix}")


async def _setup(hass: HomeAssistant, subentries) -> MockConfigEntry:
    hass.states.async_set("sensor.outdoor", "5.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.living", "20.9", {"unit_of_measurement": "°C"})
    hass.states.async_set("climate.bath", "heat", {"current_temperature": 19.0, "temperature": 21.0})
    entry = MockConfigEntry(domain=DOMAIN, title="SenorFungi", data={}, subentries_data=subentries)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_shadow_decision_and_entities(hass: HomeAssistant) -> None:
    await _setup(hass, [CONTROLLER, ZONE_CLIMATE, ZONE_OWN])
    flow = hass.states.get(_entity(hass, "_heating_flow_target"))
    assert flow.attributes["mode"] == "shadow"
    assert flow.attributes["zone"] == "Bath" and flow.attributes["demand"] == 2.0
    assert 30 <= float(flow.state) <= 50
    assert hass.states.get(_entity(hass, "_heating_heat_request")).state == "on"
    assert hass.states.get(_entity(hass, "_heating_reason")).state in ("heating", "boost")
    # own climate entity only for the zone without a climate entity
    climates = [e for e in er.async_get(hass).entities.values() if e.platform == DOMAIN and e.domain == "climate"]
    assert len(climates) == 1
    living = hass.states.get(climates[0].entity_id)
    assert living.attributes["current_temperature"] == 20.9
    assert living.attributes["temperature"] == 21
    # no logger: no thermostat comparison entities
    reg = er.async_get(hass)
    assert not [e for e in reg.entities.values() if e.unique_id.endswith("_heating_thermostat_present")]


async def test_zone_without_valve_blocks_and_eco_preset(hass: HomeAssistant, freezer) -> None:
    await _setup(hass, [CONTROLLER, ZONE_CLIMATE, ZONE_OWN])
    hass.states.async_set("sensor.living", "21.8", {"unit_of_measurement": "°C"})
    climate = [e for e in er.async_get(hass).entities.values() if e.platform == DOMAIN and e.domain == "climate"][0]
    await hass.services.async_call(
        "climate", "set_preset_mode", {"entity_id": climate.entity_id, "preset_mode": "eco"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(climate.entity_id).attributes["temperature"] == 19
    # 21.8 is 2.8 K above eco 19: the room without valve blocks, even though the bath is cold
    # (the controller is still inside the minimum heating time from setup, so it keeps heating first)
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(_entity(hass, "_heating_reason")).state == "blocked"
    assert hass.states.get(_entity(hass, "_heating_flow_target")).state == "0.0"


async def test_thermostat_request_and_presence(hass: HomeAssistant) -> None:
    entry = await _setup(hass, [CONTROLLER, ZONE_CLIMATE])
    heating = entry.runtime_data.heating
    # without the logger the thermostat isn't followed; feed a telegram the way the logger would
    t = parse_line(
        "2026-11-15T09:30:46.624 N 1: [emsesp] thermostat(0x18) W boiler(0x08), UBASetPoints(0x02E0), "
        "data: 01 28 64 00 01",
        dt_util.now(),
    )
    heating._on_telegram(t)
    assert heating.thermostat_request is not None
    assert (heating.thermostat_request.flow, heating.thermostat_request.output) == (40.0, 100)
    assert heating.thermostat_seen == t.time


async def test_subentry_flows(hass: HomeAssistant) -> None:
    entry = await _setup(hass, [])
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ZONE), context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"name": "Kitchen"})
    assert result["errors"] == {"base": "zone_needs_source"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Kitchen", "temperature_entity": "sensor.living"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_CONTROLLER), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"outdoor_entity": "sensor.outdoor", "min_flow": 50, "max_flow": 40}
    )
    assert result["errors"] == {"max_flow": "max_below_min"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"outdoor_entity": "sensor.outdoor", "thermostat_id": "0x18"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = hass.config_entries.async_get_entry(entry.entry_id)
    # the entry was set up again with the new subentries
    assert entry.runtime_data.heating is not None
    assert entry.runtime_data.heating.thermostat_id == 0x18
    assert [z.name for z in entry.runtime_data.heating.zones] == ["Kitchen"]

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_CONTROLLER), context={"source": "user"}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "controller_exists"
