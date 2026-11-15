"""Config flow for the SenorFungi integration."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import probatio
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_BOOST_OFFSET,
    CONF_DESIGN_FLOW,
    CONF_DESIGN_OUTDOOR,
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
    CONTROLLER_DEFAULTS,
    DEFAULT_EMS_COMPRESS,
    DEFAULT_EMS_PORT,
    DEFAULT_EMS_RETENTION,
    DOMAIN,
    EMS_BROADCAST_ID,
    EMS_LOG_SUBDIR,
    SHARE_DIR,
    SUBENTRY_CONTROLLER,
    SUBENTRY_ZONE,
    ZONE_DEFAULTS,
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

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        """Heating controller (one) and heating zones (any number)."""
        return {SUBENTRY_CONTROLLER: ControllerSubentryFlow, SUBENTRY_ZONE: ZoneSubentryFlow}

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


class SenorFungiOptionsFlow(OptionsFlow):
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


def _number(minimum: float, maximum: float, step: float, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=NumberSelectorMode.BOX, unit_of_measurement=unit)
    )


def _controller_schema(devices: list[EmsDevice]) -> probatio.Schema:
    d = CONTROLLER_DEFAULTS
    thermostat = SelectSelector(
        SelectSelectorConfig(
            options=_device_options(devices)[:-1], custom_value=True, mode=SelectSelectorMode.DROPDOWN, sort=False
        )
    )
    return probatio.Schema(
        {
            probatio.Required(CONF_OUTDOOR_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.TEMPERATURE)
            ),
            probatio.Optional(CONF_THERMOSTAT_ID): thermostat,
            probatio.Required(CONF_PRESENCE_TIMEOUT, default=d[CONF_PRESENCE_TIMEOUT]): _number(60, 3600, 1, "s"),
            probatio.Required(CONF_DESIGN_OUTDOOR, default=d[CONF_DESIGN_OUTDOOR]): _number(-30, 5, 0.5, "°C"),
            probatio.Required(CONF_DESIGN_FLOW, default=d[CONF_DESIGN_FLOW]): _number(25, 90, 0.5, "°C"),
            probatio.Required(CONF_MIN_FLOW, default=d[CONF_MIN_FLOW]): _number(20, 60, 0.5, "°C"),
            probatio.Required(CONF_MAX_FLOW, default=d[CONF_MAX_FLOW]): _number(25, 90, 0.5, "°C"),
            probatio.Required(CONF_ROOM_GAIN, default=d[CONF_ROOM_GAIN]): _number(0, 20, 0.5, "K/K"),
            probatio.Required(CONF_SUMMER_LIMIT, default=d[CONF_SUMMER_LIMIT]): _number(5, 30, 0.5, "°C"),
            probatio.Required(CONF_BOOST_OFFSET, default=d[CONF_BOOST_OFFSET]): _number(0, 30, 0.5, "K"),
            probatio.Required(CONF_MIN_ON, default=d[CONF_MIN_ON]): _number(0, 60, 1, "min"),
            probatio.Required(CONF_MIN_OFF, default=d[CONF_MIN_OFF]): _number(0, 60, 1, "min"),
        }
    )


def _check_controller(user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    data = dict(user_input)
    errors: dict[str, str] = {}
    tid = normalize_hex(data.get(CONF_THERMOSTAT_ID, ""))
    if tid is None:
        errors[CONF_THERMOSTAT_ID] = "invalid_hex"
    else:
        data[CONF_THERMOSTAT_ID] = tid
    if float(data[CONF_MIN_FLOW]) >= float(data[CONF_MAX_FLOW]):
        errors[CONF_MAX_FLOW] = "max_below_min"
    return data, errors


class ControllerSubentryFlow(ConfigSubentryFlow):
    """Add or change the heating controller (shadow mode: computes only, writes nothing)."""

    async def _devices(self) -> list[EmsDevice]:
        host = self._get_entry().options.get(CONF_EMS_HOST)
        return await async_discover(self.hass, host) if host else []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add the controller."""
        if self._get_entry().get_subentries_of_type(SUBENTRY_CONTROLLER):
            return self.async_abort(reason="controller_exists")
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = _check_controller(user_input)
            if not errors:
                return self.async_create_entry(title="Heating controller", data=data)
        devices = await self._devices()
        suggested = user_input or {CONF_THERMOSTAT_ID: next((d.dev_id for d in devices if d.type == "thermostat"), "")}
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(_controller_schema(devices), suggested),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Change the controller."""
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = _check_controller(user_input)
            if not errors:
                return self.async_update_and_abort(self._get_entry(), subentry, data=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _controller_schema(await self._devices()), user_input or subentry.data
            ),
            errors=errors,
        )


def _zone_schema() -> probatio.Schema:
    d = ZONE_DEFAULTS
    return probatio.Schema(
        {
            probatio.Required(CONF_ZONE_NAME): TextSelector(),
            probatio.Optional(CONF_ZONE_CLIMATE): EntitySelector(EntitySelectorConfig(domain="climate")),
            probatio.Optional(CONF_ZONE_TEMPERATURE): EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.TEMPERATURE)
            ),
            probatio.Optional(CONF_ZONE_VALVE_ENTITY): EntitySelector(EntitySelectorConfig(domain="sensor")),
            probatio.Required(CONF_ZONE_HAS_VALVE, default=d[CONF_ZONE_HAS_VALVE]): BooleanSelector(),
            probatio.Required(CONF_ZONE_WEIGHT, default=d[CONF_ZONE_WEIGHT]): _number(0, 2, 0.1, "×"),
            probatio.Required(CONF_ZONE_COMFORT, default=d[CONF_ZONE_COMFORT]): _number(5, 30, 0.5, "°C"),
            probatio.Required(CONF_ZONE_ECO, default=d[CONF_ZONE_ECO]): _number(5, 30, 0.5, "°C"),
        }
    )


def _check_zone(user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    data = {k: v for k, v in user_input.items() if v not in ("", None)}
    data[CONF_ZONE_NAME] = str(data.get(CONF_ZONE_NAME, "")).strip()
    errors: dict[str, str] = {}
    if not data[CONF_ZONE_NAME]:
        errors[CONF_ZONE_NAME] = "name_required"
    if not data.get(CONF_ZONE_CLIMATE) and not data.get(CONF_ZONE_TEMPERATURE):
        errors["base"] = "zone_needs_source"
    return data, errors


class ZoneSubentryFlow(ConfigSubentryFlow):
    """Add or change a heating zone (room)."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a zone."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = _check_zone(user_input)
            if not errors:
                return self.async_create_entry(title=data[CONF_ZONE_NAME], data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(_zone_schema(), user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Change a zone."""
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = _check_zone(user_input)
            if not errors:
                return self.async_update_and_abort(self._get_entry(), subentry, title=data[CONF_ZONE_NAME], data=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(_zone_schema(), user_input or subentry.data),
            errors=errors,
        )
