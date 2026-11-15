"""EMS bus logger: writes watched telegrams to daily files and keeps statistics."""

from __future__ import annotations

import gzip
import logging
import shutil
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .ems_bus import EmsBusClient, Telegram

_LOGGER = logging.getLogger(__name__)

FLUSH_INTERVAL = timedelta(seconds=5)
STATS_INTERVAL = timedelta(seconds=30)
CLEANUP_INTERVAL = timedelta(hours=6)
FILE_PREFIX = "ems_bus-"


@dataclass(slots=True)
class PairStats:
    """Telegrams from one device to another (for example thermostat -> boiler)."""

    src: int
    dst: int
    last_time: datetime | None = None
    last_interval: float | None = None  # s between the last two telegrams of the same type
    last_by_type: dict[int, tuple[datetime, str, str]] = field(default_factory=dict)


@dataclass(slots=True)
class EmsBusStats:
    """Values the sensors show."""

    connected: bool = False
    last_telegram: datetime | None = None
    per_minute: float | None = None
    total: int = 0
    pair: PairStats | None = None


class EmsBusLogger:
    """Run the client, write the log files and update the statistics."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        filter_ids: set[int],
        ignore_ids: set[int],
        log_dir: Path,
        retention_days: int,
        compress_after_days: int,
        pair: tuple[int, int] | None,
    ) -> None:
        """Initialize the logger.

        filter_ids: keep only telegrams from or to these devices (empty: all).
        With exactly one ID, EMS-ESP filters itself (`watch on <id>`), otherwise
        the whole bus is watched and filtered here. ignore_ids: drop telegrams
        from or to these devices. compress_after_days: gzip a day's file once it
        is this many days old (0: never).
        """
        self.hass = hass
        self.log_dir = log_dir
        self.retention_days = retention_days
        self.compress_after_days = compress_after_days
        self.filter_ids = filter_ids
        self.ignore_ids = ignore_ids
        watch_id = f"{next(iter(filter_ids)):02x}" if len(filter_ids) == 1 else ""
        self.stats = EmsBusStats(pair=PairStats(*pair) if pair else None)
        self.client = EmsBusClient(host, port, watch_id, self._on_telegram, self._on_connection, dt_util.now)
        self._buffer: list[str] = []
        self._recent: deque[datetime] = deque()
        self._listeners: list[Callable[[], None]] = []
        self._telegram_listeners: list[Callable[[Telegram], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> CALLBACK_TYPE:
        """Register a sensor update callback."""
        self._listeners.append(update)

        def remove() -> None:
            self._listeners.remove(update)

        return remove

    @callback
    def async_add_telegram_listener(self, on_telegram: Callable[[Telegram], None]) -> CALLBACK_TYPE:
        """Register a callback for every telegram that passes the filters."""
        self._telegram_listeners.append(on_telegram)

        def remove() -> None:
            self._telegram_listeners.remove(on_telegram)

        return remove

    @callback
    def async_start(self) -> None:
        """Start the timers. The client runs as a background task of the entry."""
        self._unsubs.append(async_track_time_interval(self.hass, self._flush, FLUSH_INTERVAL))
        self._unsubs.append(async_track_time_interval(self.hass, self._update_stats, STATS_INTERVAL))
        self._unsubs.append(async_track_time_interval(self.hass, self._cleanup, CLEANUP_INTERVAL))
        self.hass.async_create_task(self._cleanup(), eager_start=False)

    async def async_stop(self) -> None:
        """Stop the client and timers, write what is left."""
        await self.client.stop()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._flush()

    @callback
    def _on_connection(self, connected: bool) -> None:
        self.stats.connected = connected
        self._notify()

    @callback
    def _on_telegram(self, telegram: Telegram) -> None:
        ends = (telegram.src, telegram.dst)
        # EMS-ESP's own single-ID watch also matches telegram types with that number
        if self.filter_ids and not any(e in self.filter_ids for e in ends):
            return
        if any(e in self.ignore_ids for e in ends):
            return
        stats = self.stats
        stats.total += 1
        stats.last_telegram = telegram.time
        self._recent.append(telegram.time)
        self._buffer.append(f"{telegram.time.isoformat(timespec='milliseconds')} {telegram.line}\n")
        for on_telegram in list(self._telegram_listeners):
            on_telegram(telegram)
        pair = stats.pair
        if pair and telegram.src == pair.src and telegram.dst == pair.dst:
            prev = pair.last_by_type.get(telegram.type_id)
            if prev is not None:
                pair.last_interval = round((telegram.time - prev[0]).total_seconds(), 1)
            pair.last_time = telegram.time
            pair.last_by_type[telegram.type_id] = (
                telegram.time,
                telegram.type_name,
                telegram.data,
            )

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    async def _update_stats(self, _now: datetime | None = None) -> None:
        cutoff = dt_util.now() - timedelta(minutes=5)
        while self._recent and self._recent[0] < cutoff:
            self._recent.popleft()
        self.stats.per_minute = round(len(self._recent) / 5, 1) if self.stats.connected else None
        self._notify()

    async def _flush(self, _now: datetime | None = None) -> None:
        if not self._buffer:
            return
        lines, self._buffer = self._buffer, []
        path = self.log_dir / f"{FILE_PREFIX}{dt_util.now().date().isoformat()}.log"
        try:
            await self.hass.async_add_executor_job(_append, path, lines)
        except OSError as err:
            _LOGGER.error("Cannot write %s: %s", path, err)

    async def _cleanup(self, _now: datetime | None = None) -> None:
        today = dt_util.now().date()
        keep_from = today - timedelta(days=self.retention_days)
        compress_until = today - timedelta(days=self.compress_after_days) if self.compress_after_days else None
        try:
            removed, compressed = await self.hass.async_add_executor_job(
                _housekeeping, self.log_dir, keep_from, compress_until
            )
        except OSError as err:
            _LOGGER.error("EMS bus log housekeeping in %s failed: %s", self.log_dir, err)
            return
        if removed or compressed:
            _LOGGER.debug("EMS bus logs: removed %s, compressed %s", removed, compressed)


def _append(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.writelines(lines)


def _day_of(file: Path) -> date | None:
    name = file.name.removesuffix(".gz").removesuffix(".log")
    try:
        return datetime.strptime(name[len(FILE_PREFIX) :], "%Y-%m-%d").date()
    except ValueError:
        return None


def _housekeeping(log_dir: Path, keep_from: date, compress_until: date | None) -> tuple[list[str], list[str]]:
    """Delete files older than keep_from; gzip finished days up to and including compress_until."""
    removed: list[str] = []
    compressed: list[str] = []
    if not log_dir.is_dir():
        return removed, compressed
    for file in sorted(log_dir.glob(f"{FILE_PREFIX}*.log*")):
        day = _day_of(file)
        if day is None or file.suffix not in (".log", ".gz"):
            continue
        if day < keep_from:
            file.unlink(missing_ok=True)
            removed.append(file.name)
        elif file.suffix == ".log" and compress_until is not None and day <= compress_until:
            target = file.with_name(file.name + ".gz")
            tmp = file.with_name(file.name + ".gz.tmp")
            with file.open("rb") as src, gzip.open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
            if target.exists():  # a partial day was compressed before (e.g. clock change); keep both parts
                with target.open("ab") as dst:  # gzip allows concatenated members
                    dst.write(tmp.read_bytes())
                tmp.unlink()
            else:
                tmp.replace(target)
            file.unlink()
            compressed.append(file.name)
    return removed, compressed
