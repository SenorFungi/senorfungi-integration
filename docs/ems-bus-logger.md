# EMS bus logger: reference

The [README](../README.md#ems-bus-logger) covers setup, options and entities. This page holds the details: how the logger behaves, what it costs EMS-ESP, how to read the files, and what the telegrams in them mean as far as they are known.

## Behaviour

| What | Value | Where |
|---|---|---|
| Write buffered telegrams to the day file | every 5 s | `ems_logger.py` |
| Update the sensors | every 30 s | `ems_logger.py` |
| Housekeeping (gzip, delete old files) | at start and every 6 h | `ems_logger.py` |
| Reconnect after an error | 10 s, doubling up to 5 min | `ems_bus.py` `RECONNECT_MIN`, `RECONNECT_MAX` |
| Dead console (EMS-ESP restarted, network gone) | noticed by TCP keepalive after about 60 s (idle 30 s, 3 probes 10 s apart) | `ems_bus.py` `KEEPALIVE_*` |
| No output at all (for example `watch off` typed in another session) | reconnect after 120 s | `ems_bus.py` `IDLE_TIMEOUT` |
| Thermostat present | at once with its first telegram | `ems_logger.py` |
| Thermostat absent | presence timeout after its last telegram (option, default 150 s); unavailable while disconnected and for one timeout after connecting | `ems_logger.py` |

- On connect the logger sends an empty line, then `watch on <id>` (one filter ID) or `watch on` (several or none). It refuses every telnet option EMS-ESP offers.
- On stop (options change, Home Assistant shutdown, removal) it sends `watch off` and `exit`. If Home Assistant is killed instead, `watch` stays on in EMS-ESP until EMS-ESP restarts or someone types `watch off`.
- `watch` is global in EMS-ESP: the output shows up in the EMS-ESP web log and in every other telnet session while the logger runs.
- The day boundary is local midnight. A file is named `ems_bus-YYYY-MM-DD.log`, and `.log.gz` once compressed.
- Lines are written as `<ISO local time with offset> <decoded telegram>` in the EMS-ESP 3.7 layout (`src(0x18) -> dst(0x08), W, type(0x02E0), data: …`), whatever EMS-ESP version printed them, so files from before and after an EMS-ESP update can be searched the same way.

## Cost on EMS-ESP

Measured on one gateway (ESP32, EMS-ESP 3.7.1 without PSRAM, then 3.8.6 with PSRAM) with the whole bus watched:

- Free heap dropped by about 4 KB when the watch started (185 → 181 KB on 3.7.1) and stayed there until the firmware update the next day.
- Bus quality stayed at 100 %, with no failed reads or writes.
- An EMS-ESP restart turns `watch` off. The logger turns it on again after the reconnect.

Not checked: how many telnet sessions EMS-ESP allows at once. If it allows only one, the logger blocks a manual telnet session while it runs (turn the logger off in the options for that). Telnet has no login in EMS-ESP; the logger doesn't change that.

## Reading the files

- Live: `tail -f /share/ems_bus/ems_bus-$(date +%F).log` in a terminal on the Home Assistant host (for example the *Advanced SSH & Web Terminal* app). Older days: `zgrep '0x02E0' /share/ems_bus/ems_bus-2026-11-14.log.gz`.
- Over the network: the *Samba share* app exposes `/share` as `smb://<host>/share`.
- One type's payloads over a day: `zgrep -h 'boiler(0x08), W, ?(0x02E0)' ems_bus-*.log.gz | awk '{print $1, $NF}'` (with several data bytes, print everything after `data:`).
- Size, measured on the system below with the whole bus watched (about 70 telegrams per minute on average, more while the burner runs): about 13 MB per day, under 1 MB once gzipped (gzip saves about 94 %). With the default 90 days that's under 100 MB.

## Telegrams

Observed on a Bosch Condens 5300i W (EMS+, EMS-ESP reports it as "ACU-MH/Condens 5000i", product ID 195) with a CR120 room controller (reported as RC310), in November 2026. Other boilers and controllers can differ. The addresses are the standard EMS ones: `0x08` boiler, `0x09` boiler controller part, `0x0B` EMS-ESP gateway, `0x10`/`0x18` thermostat, `0x00` broadcast.

EMS-ESP shows EMS+ types 0x100 higher than they are on the bus (`0x02E0` is `01 E0` on the wire; [EMS-ESP discussion #2025](https://github.com/emsesp/EMS-ESP32/discussions/2025)). Search other documentation with the raw number. Data bytes below are counted from 0, as the log prints them.

### Decoded

| From → to | Type | Meaning | Bytes |
|---|---|---|---|
| thermostat → boiler, every 60 s | `0x02E0` `UBASetPoints` | **Heat request** | 1: flow target °C, 2: maximum burner output %. No demand: `01 00 00 00 01`. Bytes 0, 3, 4 see below. |
| boiler → all, every 60 s | `0x02D6` | Flow target the boiler is using (its answer to `0x02E0`) | 5: flow target °C, follows `0x02E0` byte 1 within a minute. Other bytes `00`. (On heat pumps the same type means something else, see [EMS-ESP issue #2001](https://github.com/emsesp/EMS-ESP32/issues/2001).) |
| boiler → thermostat | `0xF9` `ValueConfig` | Allowed range of a parameter, sent before the thermostat changes it | e.g. `EA 00 00 06 0F 00 00 00 23 00 00 00 3C 00 00 00 3C 00 00 00 32` = type `0xEA` offset 6, min 35, max 60, default 50 °C |
| thermostat → boiler | `0xEA` `UBAParameterWWPlus` | Hot water settings written directly (offset 6 = setpoint °C); confirmed in `0xE9` a few seconds later | |
| boiler → all, about every 10 s | `0xE9` `UBAMonitorWWPlus` | Hot water | 1–2: temperature × 10, 11: flow l/min × 10 |
| thermostat → all | `0x02A5` `RC300Monitor` | Room state | 0–1: room temperature × 10, 2: status bits (bit 4 summer), 3: current setpoint × 2, 6: setpoint by program, 7: next setpoint |
| thermostat → all | `0x06` `RCTime` | Clock | year − 2000, month, hour, day, minute, second, weekday |
| thermostat → all, every 10 min | `0xBF` `SystemError` | Error status | |

EMS-ESP decodes most of the boiler's own broadcasts (`0xE3`, `0xE4`, `0xE5`, `0xE9`, `0xD1`, `0x07`); see `src/devices/boiler.cpp` in the EMS-ESP source.

EMS+ telegrams carry a prefix byte: `FF` data, `F7` menu entries (`MenuConfig`), `F9` parameter ranges (`ValueConfig`), `F6` occasionally. A thermostat reads a dozen `F7` entries at start-up to learn which menu items the boiler offers.

### What this means for writing a flow temperature

- EMS-ESP's `selflowtemp` command writes into `0x02E0` byte 1 (from 3.8; 3.7 writes the EMS 1 telegram `0x1A`, which this boiler ignores), `selburnpow` into byte 2.
- A thermostat on the bus rewrites the whole `0x02E0` every 60 s, also while it is switched `off` (then with `01 00 00 00 01`). So a value written from Home Assistant lasts at most until the thermostat's next write. Driving the boiler from Home Assistant works only with the thermostat off the bus.
- Without a thermostat, one `0x02E0` request holds for about 4 minutes. Resend it every 60 s, as a thermostat does.
- Burner starts come with low output: the request goes to the flow cap with about 17 % output first, and to 100 % about 2 minutes later.

### Not decoded

| From → to | Type | Payload seen | Notes |
|---|---|---|---|
| thermostat → boiler, every 60 s | `0xE7` | `00 00 00` | On a GB192i.2 with a tank: `00 02 00` ([EMS-ESP discussion #1317](https://github.com/emsesp/EMS-ESP32/discussions/1317)) |
| thermostat → boiler, every 60 s | `0x02EA` | `00` | |
| boiler → all, every 60 s | `0xD7` | `01 01` | Same on a GB192i.2 |
| boiler → all, every 60 s | `0xD8` | `00 00 00` | Same on a GB192i.2 |
| thermostat → boiler | `0x02E0` bytes 0, 3, 4 | `01`, `00`, `01` | EMS-ESP's "force heating off" sends `01` in byte 3 |

None of them changed with hot water use, a hot water setpoint change, burner starts and stops, a flow target change, or the thermostat switched to `off` or `auto`. Most likely they are fixed configuration values that change only with a different system setup. Neither the EMS-ESP source nor the [hometop_HT3 message documentation](https://github.com/norberts1/hometop_HT3) describes them.
