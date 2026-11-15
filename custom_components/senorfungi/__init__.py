"""The SenorFungi integration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .config_flow import async_default_log_dir
from .const import (
    CONF_EMS_COMPRESS,
    CONF_EMS_ENABLED,
    CONF_EMS_FILTER,
    CONF_EMS_HOST,
    CONF_EMS_IGNORE,
    CONF_EMS_LOG_DIR,
    CONF_EMS_PAIR_DST,
    CONF_EMS_PAIR_SRC,
    CONF_EMS_PORT,
    CONF_EMS_RETENTION,
    DEFAULT_EMS_COMPRESS,
    DEFAULT_EMS_PORT,
    DEFAULT_EMS_RETENTION,
    SUBENTRY_CONTROLLER,
    SUBENTRY_ZONE,
)
from .ems_logger import EmsBusLogger
from .heating import HeatingRuntime

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.CLIMATE, Platform.SENSOR]


@dataclass(slots=True)
class SenorFungiData:
    """Runtime data of the config entry."""

    ems: EmsBusLogger | None = None
    heating: HeatingRuntime | None = None


type SenorFungiConfigEntry = ConfigEntry[SenorFungiData]


def _pair(options: dict) -> tuple[int, int] | None:
    src, dst = options.get(CONF_EMS_PAIR_SRC, ""), options.get(CONF_EMS_PAIR_DST, "")
    if not src or not dst:
        return None
    return int(src, 16), int(dst, 16)


async def async_setup_entry(hass: HomeAssistant, entry: SenorFungiConfigEntry) -> bool:
    """Set up SenorFungi from a config entry."""
    data = SenorFungiData()
    options = entry.options
    if options.get(CONF_EMS_ENABLED) and options.get(CONF_EMS_HOST):
        log_dir = options.get(CONF_EMS_LOG_DIR) or await async_default_log_dir(hass)
        ems = EmsBusLogger(
            hass,
            host=options[CONF_EMS_HOST],
            port=int(options.get(CONF_EMS_PORT, DEFAULT_EMS_PORT)),
            filter_ids={int(i, 16) for i in options.get(CONF_EMS_FILTER, [])},
            ignore_ids={int(i, 16) for i in options.get(CONF_EMS_IGNORE, [])},
            log_dir=Path(log_dir),
            retention_days=int(options.get(CONF_EMS_RETENTION, DEFAULT_EMS_RETENTION)),
            compress_after_days=int(options.get(CONF_EMS_COMPRESS, DEFAULT_EMS_COMPRESS)),
            pair=_pair(options),
        )
        data.ems = ems
        ems.async_start()
        entry.async_create_background_task(hass, ems.client.run(), "senorfungi_ems_bus")
    if controllers := entry.get_subentries_of_type(SUBENTRY_CONTROLLER):
        data.heating = HeatingRuntime(hass, controllers[0], entry.get_subentries_of_type(SUBENTRY_ZONE), data.ems)
    entry.runtime_data = data
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if data.heating is not None:
        data.heating.async_start()
        data.heating.async_update()
    # options and subentries (controller, zones) changed: set up again
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: SenorFungiConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SenorFungiConfigEntry) -> bool:
    """Unload a config entry."""
    if entry.runtime_data.heating is not None:
        entry.runtime_data.heating.async_stop()
    if entry.runtime_data.ems is not None:
        await entry.runtime_data.ems.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
