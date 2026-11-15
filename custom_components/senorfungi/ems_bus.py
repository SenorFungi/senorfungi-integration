"""Listen to the EMS bus through the EMS-ESP telnet console.

This module does not import Home Assistant, so it can be tested on its own.

EMS-ESP (https://github.com/emsesp/EMS-ESP32) has a telnet console (port 23) whose
`watch` command prints every received EMS telegram, for example:

    299+02:03:31.502 N 26: [emsesp] thermostat(0x18) -> boiler(0x08), W, ?(0x02E0), data: 01 00 00 00 01

The client opens the console, turns `watch on [id]` on, and passes each telegram line
to a callback. It only listens: it never sends anything to the EMS bus itself.
`watch` is a global, non-persistent setting of EMS-ESP, so the client turns it off
again when it stops. A restart of EMS-ESP also turns it off.
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

_LOGGER = logging.getLogger(__name__)

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# "<time> <level> <n>: [emsesp] <telegram>" anywhere in a (prompt-cluttered) line.
# <time> is the uptime ("299+02:03:31.502"), or from EMS-ESP 3.8 on the local
# date and time once the clock is set ("2026-11-15T09:00:00.123").
LOG_RE = re.compile(
    r"(?:\d+\+\d\d:\d\d:\d\d|\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\.\d{3} "
    r"[A-Z] \d+: \[emsesp\] (.+)$"
)
_SRC = r"(?P<src_name>[^(]*)\((?P<src>0x[0-9A-Fa-f]+)\)"
_DST = r"(?P<dst_name>[^(]*)\((?P<dst>0x[0-9A-Fa-f]+)\)"
_TYPE_REST = r"(?P<type_name>[^(]*)\((?P<type>0x[0-9A-Fa-f]+)\), (?P<rest>.*)$"
TELEGRAM_RES = (
    # up to EMS-ESP 3.7: "src(0x18) -> dst(0x08), W, type(0x02E0), data: .."
    re.compile(rf"^{_SRC} -> {_DST}, (?P<op>[A-Z]), {_TYPE_REST}"),
    # from EMS-ESP 3.8: "src(0x18) W dst(0x08), type(0x02E0), data: .."
    re.compile(rf"^{_SRC} (?P<op>[A-Z]) {_DST}, {_TYPE_REST}"),
)

IDLE_TIMEOUT = 120  # s without any output -> reconnect (for example watch turned off elsewhere)
# TCP keepalive notices a dead console (EMS-ESP restarted, network gone) within about
# KEEPALIVE_IDLE + KEEPALIVE_INTERVAL * KEEPALIVE_COUNT seconds.
KEEPALIVE_IDLE = 30
KEEPALIVE_INTERVAL = 10
KEEPALIVE_COUNT = 3
RECONNECT_MIN = 10
RECONNECT_MAX = 300


@dataclass(frozen=True, slots=True)
class Telegram:
    """One EMS telegram as decoded by EMS-ESP."""

    time: datetime
    src: int
    dst: int
    op: str  # B broadcast, W write, R read
    type_id: int
    type_name: str
    src_name: str
    dst_name: str
    rest: str  # "data: .." or "length: .." with optional "(offset n)"
    line: str  # the decoded text as EMS-ESP printed it

    @property
    def offset(self) -> int:
        """Offset of the payload (EMS+ partial telegrams), 0 if none."""
        m = re.search(r" \(offset (\d+)\)$", self.rest)
        return int(m.group(1)) if m else 0

    @property
    def data_bytes(self) -> list[int]:
        """Payload as numbers."""
        data = self.data
        if not data or data == "<empty>":
            return []
        try:
            return [int(b, 16) for b in data.split()]
        except ValueError:
            return []

    @property
    def data(self) -> str:
        """Payload bytes as hex text, without the offset suffix."""
        if not self.rest.startswith("data: "):
            return ""
        return re.sub(r" \(offset \d+\)$", "", self.rest[6:])


def thermostat_request(type_id: int, offset: int, data: list[int]) -> tuple[float, int] | None:
    """Flow temperature and burner output a thermostat asks the boiler for.

    EMS+ `UBASetPoints` 0x02E0 (byte 1 flow °C, byte 2 output %) or EMS 1
    `UBASetPoints` 0x1A (byte 0 flow °C, byte 1 output %). None for other telegrams.
    """
    layout = {0x02E0: (1, 2), 0x1A: (0, 1)}.get(type_id)
    if layout is None:
        return None
    flow_i, pow_i = (i - offset for i in layout)
    if not 0 <= flow_i < len(data) or not 0 <= pow_i < len(data):
        return None
    return float(data[flow_i]), data[pow_i]


def clean_line(raw: str) -> str:
    """Remove ANSI codes and control characters."""
    text = ANSI_RE.sub("", raw)
    return "".join(ch for ch in text if ch >= " " or ch == "\t").strip()


def parse_line(raw: str, now: datetime) -> Telegram | None:
    """Return the telegram in a console line, or None if the line has none."""
    m = LOG_RE.search(clean_line(raw))
    if not m:
        return None
    text = m.group(1)
    t = next((r.match(text) for r in TELEGRAM_RES if r.match(text)), None)
    if not t:
        return None
    return Telegram(
        time=now,
        src=int(t["src"], 16),
        dst=int(t["dst"], 16),
        op=t["op"],
        type_id=int(t["type"], 16),
        type_name=t["type_name"],
        src_name=t["src_name"],
        dst_name=t["dst_name"],
        rest=t["rest"],
        # one format for every EMS-ESP version, so log files stay comparable
        line=(
            f"{t['src_name']}({t['src']}) -> {t['dst_name']}({t['dst']}), {t['op']}, "
            f"{t['type_name']}({t['type']}), {t['rest']}"
        ),
    )


def strip_telnet(data: bytes) -> tuple[bytes, bytes]:
    """Split telnet negotiation from text.

    Returns (text, reply). The reply refuses every option the server asks for.
    """
    out = bytearray()
    reply = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == IAC and i + 1 < len(data):
            cmd = data[i + 1]
            if cmd in (DO, DONT, WILL, WONT) and i + 2 < len(data):
                reply += bytes([IAC, WONT if cmd in (DO, DONT) else DONT, data[i + 2]])
                i += 3
                continue
            if cmd == SB:
                end = data.find(bytes([IAC, SE]), i)
                i = end + 2 if end >= 0 else len(data)
                continue
            i += 2
            continue
        out.append(b)
        i += 1
    return bytes(out), bytes(reply)


def enable_keepalive(sock: socket.socket | None) -> None:
    """Turn on TCP keepalive with short timers (best effort, per platform)."""
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        # Linux: TCP_KEEPIDLE, macOS: TCP_KEEPALIVE (same meaning)
        idle = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)
        for option, value in (
            (idle, KEEPALIVE_IDLE),
            (getattr(socket, "TCP_KEEPINTVL", None), KEEPALIVE_INTERVAL),
            (getattr(socket, "TCP_KEEPCNT", None), KEEPALIVE_COUNT),
        ):
            if option is not None:
                sock.setsockopt(socket.IPPROTO_TCP, option, value)
    except OSError as err:
        _LOGGER.debug("TCP keepalive not set: %s", err)


class EmsBusClient:
    """Keep a telnet session to EMS-ESP open and stream watched telegrams."""

    def __init__(
        self,
        host: str,
        port: int,
        watch_id: str,
        on_telegram: Callable[[Telegram], None],
        on_connection: Callable[[bool], None],
        now: Callable[[], datetime],
    ) -> None:
        """Initialize the client. watch_id is a hex device or type ID, or '' for all."""
        self.host = host
        self.port = port
        self.watch_id = watch_id.strip()
        self._on_telegram = on_telegram
        self._on_connection = on_connection
        self._now = now
        self._writer: asyncio.StreamWriter | None = None
        self._stopping = False

    @property
    def watch_command(self) -> str:
        """Console command that starts the watch."""
        return f"watch on {self.watch_id}".strip()

    async def run(self) -> None:
        """Connect, watch and reconnect until cancelled."""
        delay = RECONNECT_MIN
        while not self._stopping:
            try:
                await self._session()
                delay = RECONNECT_MIN
            except asyncio.CancelledError:
                raise
            except (OSError, TimeoutError) as err:
                if self._stopping:
                    return
                _LOGGER.warning("EMS-ESP console %s:%s: %s", self.host, self.port, err)
            finally:
                self._on_connection(False)
            if self._stopping:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX)

    async def _session(self) -> None:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), timeout=15)
        enable_keepalive(writer.get_extra_info("socket"))
        self._writer = writer
        try:
            await self._send("")
            await asyncio.sleep(1)
            await self._send(self.watch_command)
            self._on_connection(True)
            _LOGGER.info("EMS-ESP console %s: %s", self.host, self.watch_command)
            buf = b""
            while True:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=IDLE_TIMEOUT)
                if not chunk:
                    raise OSError("connection closed by EMS-ESP")
                text, reply = strip_telnet(chunk)
                if reply:
                    writer.write(reply)
                buf += text
                *lines, buf = buf.split(b"\n")
                for raw in lines:
                    telegram = parse_line(raw.decode(errors="replace"), self._now())
                    if telegram is not None:
                        self._on_telegram(telegram)
        finally:
            self._writer = None
            writer.close()

    async def _send(self, command: str) -> None:
        if self._writer is None:
            return
        self._writer.write(command.encode() + b"\r\n")
        await self._writer.drain()

    async def stop(self) -> None:
        """Turn the watch off and leave the console (best effort)."""
        self._stopping = True
        writer = self._writer
        if writer is None:
            return
        try:
            await self._send("watch off")
            await asyncio.sleep(0.5)
            await self._send("exit")
        except OSError:
            pass
        finally:
            writer.close()
