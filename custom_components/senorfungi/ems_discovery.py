"""Ask an EMS-ESP gateway which EMS devices it sees (for suggestions in the options)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import EMS_API_INFO

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EmsDevice:
    """One device on the EMS bus."""

    dev_id: str  # two or more lowercase hex digits
    type: str  # "boiler", "thermostat", "gateway", ...
    name: str


async def async_discover(hass: HomeAssistant, host: str) -> list[EmsDevice]:
    """Return the devices EMS-ESP reports, plus EMS-ESP itself. Empty list on any error."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(EMS_API_INFO.format(host=host), timeout=aiohttp.ClientTimeout(total=5)) as resp:
            resp.raise_for_status()
            info = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.debug("EMS-ESP device discovery on %s failed: %s", host, err)
        return []
    devices: list[EmsDevice] = []
    try:
        for dev in info.get("devices", []):
            devices.append(
                EmsDevice(f"{int(dev['deviceID'], 16):02x}", str(dev.get("type", "")), str(dev.get("name", "")))
            )
        if (bus_id := info.get("settings", {}).get("emsBusID")) is not None:
            version = info.get("system", {}).get("version", "")
            devices.append(EmsDevice(f"{int(bus_id):02x}", "gateway", f"EMS-ESP {version}".strip()))
    except (KeyError, TypeError, ValueError, AttributeError) as err:
        _LOGGER.debug("Unexpected EMS-ESP info from %s: %s", host, err)
    return devices
