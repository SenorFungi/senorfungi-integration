"""Integration tests: options flow, setup against a fake EMS-ESP console, unload."""

import asyncio
import gzip
from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.senorfungi.const import DOMAIN

PROMPT = b"\x1b[0;92m\x1b[1mems-esp\x1b[0m:$ \x1b[0G\x1b[K"
TELEGRAMS = [
    b"299+02:03:31.402 N 25: [emsesp] thermostat(0x18) -> boiler(0x08), W, ?(0xE7), data: 00 00 00\r\n",
    b"299+02:03:31.502 N 26: [emsesp] thermostat(0x18) -> boiler(0x08), W, ?(0x02E0), data: 01 00 00 00 01\r\n",
    b"299+02:03:31.844 N 29: [emsesp] thermostat(0x18) -> all(0x00), B, RC300Monitor(0x02A5), data: 00 (offset 24)\r\n",
    b"299+02:03:32.001 N 30: [emsesp] me(0x0B) -> thermostat(0x18), R, RC300Monitor(0x02A5), length: 25\r\n",
    b"299+02:03:32.101 N 31: [emsesp] boiler(0x08) -> all(0x00), B, UBAMonitorFast(0x18), data: 2A 00\r\n",
]


class FakeConsole:
    """Telnet server that answers `watch on` with telegrams."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.server: asyncio.Server | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        writer.write(bytes([255, 251, 1]) + b"EMS-ESP version 3.7.1\r\n" + PROMPT)
        try:
            await self._serve(reader, writer)
        except ConnectionError:  # the options flow only opens and closes the connection
            return

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while line := await reader.readline():
            cmd = line.replace(bytes([255, 254, 1]), b"").decode(errors="replace").strip()
            self.commands.append(cmd)
            if cmd.startswith("watch on"):
                writer.write(b"Watching incoming telegrams\r\n")
                for t in TELEGRAMS:
                    writer.write(PROMPT + t)
                await writer.drain()
            if cmd == "exit":
                writer.close()
                return

    async def send(self, data: bytes) -> None:
        assert self.writer is not None
        self.writer.write(PROMPT + data)
        await self.writer.drain()

    async def stop(self) -> None:
        if self.server:
            self.server.close()


async def _wait_for(cond, timeout: float = 5.0) -> None:
    for _ in range(int(timeout / 0.05)):
        if cond():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met")


@pytest.fixture
async def console():
    fake = FakeConsole()
    yield fake
    await fake.stop()


INFO = {
    "system": {"version": "3.7.1"},
    "settings": {"emsBusID": 11},
    "devices": [
        {"type": "boiler", "name": "Example boiler", "deviceID": "0x08"},
        {"type": "thermostat", "name": "Example thermostat", "deviceID": "0x18"},
    ],
}


def _options(port: int, tmp_path, **changes) -> dict:
    options = {
        "ems_enabled": True,
        "ems_host": "127.0.0.1",
        "ems_port": port,
        "ems_filter_ids": ["18"],
        "ems_ignore_ids": ["0b"],
        "ems_pair_src": "18",
        "ems_pair_dst": "08",
        "ems_log_dir": str(tmp_path / "ems_bus"),
        "ems_retention_days": 90,
        "ems_compress_after_days": 1,
    }
    options.update(changes)
    return options


CONNECTION_KEYS = ("ems_enabled", "ems_host", "ems_port")


async def _run_options_flow(hass: HomeAssistant, entry: MockConfigEntry, options: dict):
    """Both steps of the options flow; returns the result of the last step."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {k: options[k] for k in CONNECTION_KEYS if k in options}
    )
    if result.get("step_id") != "logger":
        return result
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {k: v for k, v in options.items() if k not in CONNECTION_KEYS}
    )


async def _setup(hass: HomeAssistant, options: dict) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="SenorFungi", data={}, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_options_flow_connection_errors(hass: HomeAssistant, socket_enabled) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="SenorFungi", data={})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"ems_enabled": True, "ems_port": 23})
    assert result["errors"] == {"ems_host": "host_required"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"ems_enabled": True, "ems_host": "127.0.0.1", "ems_port": 1}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_options_flow_off_needs_no_connection(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="SenorFungi", data={}, options={"ems_retention_days": 7})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"ems_enabled": False, "ems_port": 23}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["ems_enabled"] is False
    assert entry.options["ems_retention_days"] == 7  # other options are kept


async def test_options_flow_discovery_and_validation(
    hass: HomeAssistant, socket_enabled, console: FakeConsole, aioclient_mock, tmp_path
) -> None:
    aioclient_mock.get("http://127.0.0.1/api/system/info", json=INFO)
    port = await console.start()
    entry = MockConfigEntry(domain=DOMAIN, title="SenorFungi", data={})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"ems_enabled": True, "ems_host": "127.0.0.1", "ems_port": port}
    )
    assert result["step_id"] == "logger"
    assert result["description_placeholders"] == {"devices": "3"}
    schema = result["data_schema"].schema
    suggested = {str(k): k.description.get("suggested_value") for k in schema if k.description}
    # neutral defaults: whole bus, nothing ignored; the pair comes from discovery
    assert suggested.get("ems_filter_ids") is None
    assert suggested["ems_pair_src"] == "18"
    assert suggested["ems_pair_dst"] == "08"
    filter_key = next(k for k in schema if str(k) == "ems_filter_ids")
    labels = [option["label"] for option in schema[filter_key].config["options"]]
    assert labels == [
        "0x08 boiler – Example boiler",
        "0x18 thermostat – Example thermostat",
        "0x0B gateway – EMS-ESP 3.7.1",
        "0x00 broadcast",
    ]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "ems_filter_ids": ["18", "zz"],
            "ems_pair_src": "18",
            "ems_log_dir": "relative/path",
            "ems_retention_days": 90,
            "ems_compress_after_days": 1,
        },
    )
    assert result["errors"] == {
        "ems_filter_ids": "invalid_hex",
        "ems_pair_dst": "pair_incomplete",
        "ems_log_dir": "dir_not_absolute",
    }


async def test_discovery_failure_gives_empty_suggestions(
    hass: HomeAssistant, socket_enabled, console: FakeConsole, aioclient_mock
) -> None:
    aioclient_mock.get("http://127.0.0.1/api/system/info", status=500)
    port = await console.start()
    entry = MockConfigEntry(domain=DOMAIN, title="SenorFungi", data={})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"ems_enabled": True, "ems_host": "127.0.0.1", "ems_port": port}
    )
    assert result["step_id"] == "logger"
    assert result["description_placeholders"] == {"devices": "0"}
    schema = result["data_schema"].schema
    assert not any(k.description and k.description.get("suggested_value") for k in schema)
    log_dir_key = next(k for k in schema if str(k) == "ems_log_dir")
    assert log_dir_key.default() == hass.config.path("ems_bus")  # no /share in the test environment


async def test_logger_end_to_end(
    hass: HomeAssistant, socket_enabled, console: FakeConsole, aioclient_mock, tmp_path
) -> None:
    aioclient_mock.get("http://127.0.0.1/api/system/info", json=INFO)
    port = await console.start()
    entry = MockConfigEntry(domain=DOMAIN, title="SenorFungi", data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.ems_bus_logger_console_connected") is None

    # turn the logger on through the options flow (reloads the entry); IDs are normalized
    result = await _run_options_flow(
        hass, entry, _options(port, tmp_path, ems_filter_ids=["0x18"], ems_ignore_ids=["0B", "0b"], ems_pair_dst="8")
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["ems_filter_ids"] == ["18"]
    assert entry.options["ems_ignore_ids"] == ["0b"]
    assert entry.options["ems_pair_dst"] == "08"
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    # one filter ID: EMS-ESP filters itself
    await _wait_for(lambda: "watch on 18" in console.commands)
    ems = entry.runtime_data.ems
    await _wait_for(lambda: ems.stats.total >= 3)
    assert hass.states.get("binary_sensor.ems_bus_logger_console_connected").state == "on"

    # second round of the same type, 60 s later in "bus time" -> interval
    later = dt_util.now() + timedelta(seconds=60)
    ems.client._now = lambda: later  # noqa: SLF001
    await console.send(TELEGRAMS[0])
    await _wait_for(lambda: ems.stats.total >= 4)

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    pair_last = hass.states.get("sensor.ems_bus_logger_last_telegram_0x18_to_0x08")
    assert pair_last is not None
    assert pair_last.attributes["0xE7"] == "? 00 00 00"
    assert pair_last.attributes["0x2E0"] == "? 01 00 00 00 01"
    interval = hass.states.get("sensor.ems_bus_logger_interval_0x18_to_0x08")
    assert float(interval.state) == pytest.approx(60, abs=1)
    rate = hass.states.get("sensor.ems_bus_logger_telegrams_per_minute")
    assert float(rate.state) > 0

    # flush -> daily file. Left out: ems-esp polling (ignored) and the boiler's
    # broadcast of type 0x18 (EMS-ESP's watch matches it, but 0x18 isn't its device)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=62))
    await hass.async_block_till_done()
    files = list((tmp_path / "ems_bus").glob("ems_bus-*.log"))
    assert len(files) == 1
    text = files[0].read_text()
    assert "thermostat(0x18) -> boiler(0x08), W, ?(0xE7), data: 00 00 00" in text
    assert "me(0x0B)" not in text
    assert "UBAMonitorFast" not in text
    assert text.count("\n") == 4
    assert ems.stats.total == 4

    # unload -> watch off, exit
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    await _wait_for(lambda: "watch off" in console.commands and "exit" in console.commands)


async def test_several_filter_ids_watch_whole_bus(
    hass: HomeAssistant, socket_enabled, console: FakeConsole, tmp_path
) -> None:
    port = await console.start()
    entry = await _setup(hass, _options(port, tmp_path, ems_filter_ids=["18", "08"], ems_ignore_ids=[]))
    await _wait_for(lambda: "watch on" in console.commands)
    ems = entry.runtime_data.ems
    await _wait_for(lambda: ems.stats.total >= 5)  # all five: 0x0B -> 0x18 and 0x08 -> 0x00 match too
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_housekeeping_compresses_and_removes(
    hass: HomeAssistant, socket_enabled, console: FakeConsole, tmp_path
) -> None:
    log_dir = tmp_path / "ems_bus"
    log_dir.mkdir()
    today = dt_util.now().date()

    def name(days_ago: int) -> str:
        return f"ems_bus-{(today - timedelta(days=days_ago)).isoformat()}.log"

    (log_dir / name(400)).write_text("very old\n")
    (log_dir / (name(200) + ".gz")).write_bytes(gzip.compress(b"old\n"))
    (log_dir / name(3)).write_text("three days\n")
    (log_dir / name(1)).write_text("yesterday\n")
    (log_dir / name(0)).write_text("today\n")
    (log_dir / "notes.txt").write_text("not ours\n")
    port = await console.start()
    entry = await _setup(hass, _options(port, tmp_path, ems_retention_days=90, ems_compress_after_days=1))
    await _wait_for(lambda: not (log_dir / name(1)).exists())
    assert not (log_dir / name(400)).exists()
    assert not (log_dir / (name(200) + ".gz")).exists()
    assert gzip.decompress((log_dir / (name(3) + ".gz")).read_bytes()) == b"three days\n"
    assert gzip.decompress((log_dir / (name(1) + ".gz")).read_bytes()) == b"yesterday\n"
    assert (log_dir / name(0)).read_text().startswith("today")
    assert (log_dir / "notes.txt").exists()
    assert hass.states.get("sensor.ems_bus_logger_interval_0x18_to_0x08") is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_compression_off(hass: HomeAssistant, socket_enabled, console: FakeConsole, tmp_path) -> None:
    log_dir = tmp_path / "ems_bus"
    log_dir.mkdir()
    old = log_dir / f"ems_bus-{(dt_util.now().date() - timedelta(days=5)).isoformat()}.log"
    old.write_text("x\n")
    port = await console.start()
    entry = await _setup(hass, _options(port, tmp_path, ems_compress_after_days=0, ems_pair_src="", ems_pair_dst=""))
    await _wait_for(lambda: "watch on 18" in console.commands)
    await hass.async_block_till_done()
    assert old.exists()
    assert not list(log_dir.glob("*.gz"))
    assert hass.states.get("sensor.ems_bus_logger_interval_0x18_to_0x08") is None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
