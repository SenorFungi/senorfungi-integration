"""Config flow for the SenorFungi integration."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

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
    DOMAIN,
    EMS_BROADCAST_ID,
    EMS_LOG_SUBDIR,
    SHARE_DIR,
)
from .ems_discovery import EmsDevice, async_discover

LOGGER_KEYS = (
    CONF_EMS_FILTER,
    CONF_EMS_IGNORE,
    CONF_EMS_PAIR_SRC,
    CONF_EMS_PAIR_DST,
    CONF_EMS_LOG_DIR,
    CONF_EMS_RETENTION,
    CONF_EMS_COMPRESS,
)

CONNECTION_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_EMS_ENABLED, default=False): bool,
        probatio.Optional(CONF_EMS_HOST): str,
        probatio.Required(CONF_EMS_PORT, default=DEFAULT_EMS_PORT): probatio.Port(),
    }
)


async def async_default_log_dir(hass: HomeAssistant) -> str:
    """/share/ems_bus where /share exists (HA OS, Supervised), else <config>/ems_bus."""
    has_share = await hass.async_add_executor_job(Path(SHARE_DIR).is_dir)
    return str(Path(SHARE_DIR) / EMS_LOG_SUBDIR) if has_share else hass.config.path(EMS_LOG_SUBDIR)


def _device_options(devices: list[EmsDevice]) -> list[SelectOptionDict]:
    options = [
        SelectOptionDict(value=d.dev_id, label=f"0x{d.dev_id.upper()} {d.type} – {d.name}".rstrip(" –"))
        for d in devices
    ]
    options.append(SelectOptionDict(value=EMS_BROADCAST_ID, label=f"0x{EMS_BROADCAST_ID} broadcast"))
    return options


def _logger_schema(devices: list[EmsDevice], default_dir: str) -> probatio.Schema:
    options = _device_options(devices)
    ids = SelectSelector(
        SelectSelectorConfig(
            options=options, multiple=True, custom_value=True, mode=SelectSelectorMode.DROPDOWN, sort=False
        )
    )
    one_id = SelectSelector(
        SelectSelectorConfig(options=options, custom_value=True, mode=SelectSelectorMode.DROPDOWN, sort=False)
    )

    def days(minimum: int) -> NumberSelector:
        return NumberSelector(
            NumberSelectorConfig(min=minimum, max=3650, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="d")
        )

    return probatio.Schema(
        {
            # no defaults for these: an empty field must stay empty (whole bus, nothing ignored, no pair)
            probatio.Optional(CONF_EMS_FILTER): ids,
            probatio.Optional(CONF_EMS_IGNORE): ids,
            probatio.Optional(CONF_EMS_PAIR_SRC): one_id,
            probatio.Optional(CONF_EMS_PAIR_DST): one_id,
            probatio.Required(CONF_EMS_LOG_DIR, default=default_dir): str,
            probatio.Required(CONF_EMS_RETENTION, default=DEFAULT_EMS_RETENTION): days(1),
            probatio.Required(CONF_EMS_COMPRESS, default=DEFAULT_EMS_COMPRESS): days(0),
        }
    )


def _suggested_pair(devices: list[EmsDevice]) -> dict[str, str]:
    """Thermostat -> boiler, if EMS-ESP reports exactly those roles."""
    by_type = {d.type: d.dev_id for d in reversed(devices)}  # first device of each type wins
    if "thermostat" in by_type and "boiler" in by_type:
        return {CONF_EMS_PAIR_SRC: by_type["thermostat"], CONF_EMS_PAIR_DST: by_type["boiler"]}
    return {}


async def _can_connect(host: str, port: int) -> bool:
    """Open and close a TCP connection to the EMS-ESP console (no watch, no commands)."""
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
    except OSError, TimeoutError:
        return False
    writer.close()
    return True


def _check_dir(path: str) -> bool:
    """Create the log folder if needed and check that a file can be written there."""
    folder = Path(path)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write_test"
        probe.write_text("ok")
        probe.unlink()
    except OSError:
        return False
    return True


def normalize_hex(value: str) -> str | None:
    """Return the ID as two or more lowercase hex digits, '' if empty, None if invalid."""
    value = str(value).strip().lower().removeprefix("0x")
    if not value:
        return ""
    try:
        number = int(value, 16)
    except ValueError:
        return None
    if not 0 <= number <= 0xFFFF:
        return None
    return f"{number:02x}"


def _normalize_ids(values: list[str] | str | None) -> list[str] | None:
    """Normalize a list of IDs (also accepts one comma separated string). None if one is invalid."""
    if values is None:
        return []
    if isinstance(values, str):
        values = values.split(",")
    result: list[str] = []
    for value in values:
        normalized = normalize_hex(value)
        if normalized is None:
            return None
        if normalized and normalized not in result:
            result.append(normalized)
    return result


class SenorFungiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SenorFungi."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SenorFungiOptionsFlow:
        """Create the options flow."""
        return SenorFungiOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is not None:
            return self.async_create_entry(title="SenorFungi", data={})
        return self.async_show_form(step_id="user")


class SenorFungiOptionsFlow(OptionsFlowWithReload):
    """Options: the EMS bus logger. Step 1 connection, step 2 logger settings."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._connection: dict[str, Any] = {}
        self._devices: list[EmsDevice] = []

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 1: turn the logger on or off and say where EMS-ESP is."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = dict(user_input)
            user_input[CONF_EMS_HOST] = user_input.get(CONF_EMS_HOST, "").strip()
            if not user_input[CONF_EMS_ENABLED]:
                return self.async_create_entry(data={**self.config_entry.options, **user_input})
            if not user_input[CONF_EMS_HOST]:
                errors[CONF_EMS_HOST] = "host_required"
            elif not await _can_connect(user_input[CONF_EMS_HOST], user_input[CONF_EMS_PORT]):
                errors["base"] = "cannot_connect"
            if not errors:
                self._connection = user_input
                return await self.async_step_logger()

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(CONNECTION_SCHEMA, user_input or self.config_entry.options),
            errors=errors,
        )

    async def async_step_logger(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 2: what to log, where, and for how long."""
        errors: dict[str, str] = {}
        if not self._devices:
            self._devices = await async_discover(self.hass, self._connection[CONF_EMS_HOST])
        if user_input is not None:
            user_input = dict(user_input)
            user_input[CONF_EMS_LOG_DIR] = user_input.get(CONF_EMS_LOG_DIR, "").strip()
            user_input[CONF_EMS_RETENTION] = int(user_input.get(CONF_EMS_RETENTION, DEFAULT_EMS_RETENTION))
            user_input[CONF_EMS_COMPRESS] = int(user_input.get(CONF_EMS_COMPRESS, DEFAULT_EMS_COMPRESS))
            for key in (CONF_EMS_FILTER, CONF_EMS_IGNORE):
                ids = _normalize_ids(user_input.get(key))
                if ids is None:
                    errors[key] = "invalid_hex"
                else:
                    user_input[key] = ids
            for key in (CONF_EMS_PAIR_SRC, CONF_EMS_PAIR_DST):
                normalized = normalize_hex(user_input.get(key, ""))
                if normalized is None:
                    errors[key] = "invalid_hex"
                else:
                    user_input[key] = normalized
            if bool(user_input.get(CONF_EMS_PAIR_SRC)) != bool(user_input.get(CONF_EMS_PAIR_DST)):
                errors[CONF_EMS_PAIR_DST] = "pair_incomplete"
            if not user_input[CONF_EMS_LOG_DIR].startswith("/"):
                errors[CONF_EMS_LOG_DIR] = "dir_not_absolute"
            elif not await self.hass.async_add_executor_job(_check_dir, user_input[CONF_EMS_LOG_DIR]):
                errors[CONF_EMS_LOG_DIR] = "dir_not_writable"
            if not errors:
                return self.async_create_entry(data={**self._connection, **user_input})

        current = self.config_entry.options
        if user_input is not None:
            suggested = user_input
        elif any(key in current for key in LOGGER_KEYS):
            suggested = current
        else:
            suggested = _suggested_pair(self._devices)
        return self.async_show_form(
            step_id="logger",
            data_schema=self.add_suggested_values_to_schema(
                _logger_schema(self._devices, await async_default_log_dir(self.hass)), suggested
            ),
            errors=errors,
            description_placeholders={"devices": str(len(self._devices))},
        )
