"""Tests for the Home Assistant independent EMS bus parser.

Run with: python -m pytest tests
The module is loaded from its file so that Home Assistant isn't needed.
"""

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "ems_bus", Path(__file__).parents[1] / "custom_components/senorfungi/ems_bus.py"
)
ems_bus = importlib.util.module_from_spec(spec)
sys.modules["ems_bus"] = ems_bus
spec.loader.exec_module(ems_bus)

NOW = datetime(2026, 11, 14, 14, 45)
PROMPT = "\x1b[0;92m\x1b[1mems-esp\x1b[0m:$ \x1b[0G\x1b[K"


def test_parse_write_to_boiler() -> None:
    line = (
        PROMPT
        + "299+02:03:31.502 N 26: [emsesp] thermostat(0x18) -> boiler(0x08), W, ?(0x02E0), data: 01 00 00 00 01\r"
    )
    t = ems_bus.parse_line(line, NOW)
    assert t is not None
    assert (t.src, t.dst, t.op, t.type_id) == (0x18, 0x08, "W", 0x02E0)
    assert t.type_name == "?"
    assert t.data == "01 00 00 00 01"
    assert t.line.startswith("thermostat(0x18) -> boiler(0x08)")


def test_parse_broadcast_with_offset() -> None:
    line = (
        "299+02:03:31.844 N 29: [emsesp] thermostat(0x18) -> all(0x00), B, "
        "RC300Monitor(0x02A5), data: 00 00 32 (offset 24)"
    )
    t = ems_bus.parse_line(PROMPT * 3 + line, NOW)
    assert t is not None
    assert (t.dst, t.op, t.type_name, t.type_id) == (0x00, "B", "RC300Monitor", 0x02A5)
    assert t.data == "00 00 32"


def test_parse_read_request_has_no_data() -> None:
    line = "299+02:03:16.001 N 2: [emsesp] me(0x0B) -> thermostat(0x18), R, RC300Monitor(0x02A5), length: 25"
    t = ems_bus.parse_line(line, NOW)
    assert t is not None
    assert (t.src, t.op) == (0x0B, "R")
    assert t.data == ""


def test_parse_ems_esp_3_8_format() -> None:
    """EMS-ESP 3.8+ prints the wall-clock time and the operation between src and dst."""
    line = (
        PROMPT
        + "2026-11-15T09:00:45.507 N 26: [emsesp] thermostat(0x18) W boiler(0x08), ?(0x02E0), data: 01 28 64 00 01\r"
    )
    t = ems_bus.parse_line(line, NOW)
    assert t is not None
    assert (t.src, t.dst, t.op, t.type_id) == (0x18, 0x08, "W", 0x02E0)
    assert t.data == "01 28 64 00 01"
    # written in the same format as for older versions
    assert t.line == "thermostat(0x18) -> boiler(0x08), W, ?(0x02E0), data: 01 28 64 00 01"


def test_parse_ems_esp_3_8_broadcast_and_read() -> None:
    b = ems_bus.parse_line(
        "2026-11-15T09:00:45.507 N 27: [emsesp] boiler(0x08) B all(0x00), UBAMonitorFastPlus(0xE4), "
        "data: 00 00 (offset 35)",
        NOW,
    )
    assert b is not None
    assert (b.src, b.dst, b.op, b.type_name) == (0x08, 0x00, "B", "UBAMonitorFastPlus")
    assert b.data == "00 00"
    r = ems_bus.parse_line(
        "2026-11-15T09:00:46.001 N 28: [emsesp] me(0x0B) R thermostat(0x18), RC300Monitor(0x02A5), length: 25",
        NOW,
    )
    assert r is not None
    assert (r.src, r.op, r.data) == (0x0B, "R", "")


def test_other_lines_are_ignored() -> None:
    for line in (
        "Watching incoming telegrams, displayed in decoded format",
        PROMPT,
        "299+02:03:05.436 I 0: [shell] User session opened on console pty0",
        "2026-11-15T09:00:00.000 I 1: [shell] User session opened on console pty0",
        "",
    ):
        assert ems_bus.parse_line(line, NOW) is None


def test_strip_telnet_refuses_options() -> None:
    data = bytes([255, 251, 1, 255, 253, 3]) + b"hello\n"
    text, reply = ems_bus.strip_telnet(data)
    assert text == b"hello\n"
    assert reply == bytes([255, 254, 1, 255, 252, 3])


def test_watch_command() -> None:
    def noop(*_):
        return None

    assert ems_bus.EmsBusClient("h", 23, "18", noop, noop, datetime.now).watch_command == "watch on 18"
    assert ems_bus.EmsBusClient("h", 23, "", noop, noop, datetime.now).watch_command == "watch on"


def test_enable_keepalive_sets_socket_options() -> None:
    import socket

    class FakeSocket:
        def __init__(self) -> None:
            self.options: dict[tuple[int, int], int] = {}

        def setsockopt(self, level: int, option: int, value: int) -> None:
            self.options[(level, option)] = value

    sock = FakeSocket()
    ems_bus.enable_keepalive(sock)  # type: ignore[arg-type]
    assert sock.options[(socket.SOL_SOCKET, socket.SO_KEEPALIVE)] == 1
    if hasattr(socket, "TCP_KEEPINTVL"):
        assert sock.options[(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL)] == ems_bus.KEEPALIVE_INTERVAL
    ems_bus.enable_keepalive(None)


def test_thermostat_request_from_telegrams() -> None:
    assert ems_bus.thermostat_request(0x02E0, 0, [0x01, 0x28, 0x64, 0x00, 0x01]) == (40.0, 100)
    assert ems_bus.thermostat_request(0x02E0, 1, [0x22, 0x50]) == (34.0, 80)
    assert ems_bus.thermostat_request(0x1A, 0, [0x2D, 0x64, 0x00]) == (45.0, 100)
    assert ems_bus.thermostat_request(0x02E0, 3, [0x00]) is None
    assert ems_bus.thermostat_request(0x02A5, 0, [0x00, 0x01]) is None
