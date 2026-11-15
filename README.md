# SenorFungi

A custom Home Assistant integration with a collection of small helpers. Features are turned on in the integration's options. Requires Home Assistant 2026.10 or newer.

## Install with HACS

1. In HACS, open the menu (⋮) and choose **Custom repositories**.
2. Repository: `https://github.com/SenorFungi/senorfungi-integration`, type: **Integration**.
3. Find **SenorFungi** in HACS, download it, and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration** and pick **SenorFungi**.

## EMS bus logger

Logs the EMS bus traffic of a Bosch, Buderus or Junkers heating system that an [EMS-ESP](https://github.com/emsesp/EMS-ESP32) gateway sees, to daily files in Home Assistant.

- **How it works:** it keeps a session to the EMS-ESP telnet console (port 23) open and turns on `watch`. EMS-ESP then prints every received telegram, already decoded. The logger writes each one with a timestamp to a daily file `ems_bus-YYYY-MM-DD.log` in the log folder, compresses finished days with gzip and deletes old files. It reconnects after an EMS-ESP or Home Assistant restart and turns the watch on again; TCP keepalive notices a dead console within about a minute.
- **It only listens.** It sends nothing to the EMS bus. `watch` is a global, non-persistent EMS-ESP setting: the logger turns it off when it stops. While it runs, the watch output also appears in the EMS-ESP web log and in other telnet sessions, and `watch off` in another session stops it until the next reconnect.
- **Setup:** Settings → Devices & services → SenorFungi → Configure. The options have two steps:
  1. **Connection:** turn the logger on and enter the EMS-ESP host and telnet port (default 23). Telnet must be enabled in EMS-ESP (Settings → Services, on by default). The step checks that the console can be reached.
  2. **What and where:** the logger asks EMS-ESP's REST API (`http://<host>/api/system/info`, no login) which devices it sees, and offers them in the ID fields, for example `0x08 boiler – …`. Any other hex ID can be typed in. If EMS-ESP reports a thermostat and a boiler, the pair is pre-filled with thermostat → boiler.

| Option | Default | Meaning |
|---|---|---|
| Filter: devices | empty (whole bus) | Keep only telegrams from or to these devices. With one ID, EMS-ESP filters itself (`watch on <id>`); with several, the whole bus is watched (`watch on`) and filtered in Home Assistant. EMS-ESP's own filter also matches telegram *types* with that number; the logger drops those, so the filter always means "from or to these devices". |
| Ignore: devices | empty | Drop telegrams from or to these devices, for example the EMS-ESP gateway itself (usually `0B`) to leave out its own polling. |
| Pair source / destination | from discovery, else empty | Adds sensors for the telegrams from source to destination. Both empty: none. |
| Log folder | `/share/ems_bus` if `/share` exists (Home Assistant OS, Supervised), else `<config>/ems_bus` | Absolute path. A folder in `/share` isn't part of `/config` and can be left out of full backups. |
| Keep log files | 90 days | Older files (also compressed ones) are deleted. |
| Compress after | 1 day | Gzip a day's file once it is this many days old (1: yesterday's file). 0: never. Read with `zcat`/`zgrep`. |

Housekeeping (compress, delete) runs at start and every 6 hours.

Entities (device "EMS bus logger"):

| Entity | Meaning |
|---|---|
| Console connected | Telnet session open and watching (diagnostic). |
| Last telegram | Time of the last watched telegram (diagnostic). |
| Telegrams per minute | Mean over the last 5 minutes (diagnostic). |
| Last telegram \<source\> to \<destination\> | Time of the last telegram from source to destination. The attributes hold the last payload per telegram type. |
| Interval \<source\> to \<destination\> | Seconds between the last two telegrams of the same type from source to destination. Shows whether a device sends on a schedule. |

Log line example: `2026-11-14T14:45:37.567+01:00 thermostat(0x18) -> boiler(0x08), W, ?(0x02E0), data: 01 00 00 00 01`. File size depends on the system and the filter. As a guide, a whole bus of one boiler and one thermostat was about 13 MB per day, and under 1 MB gzipped.

**Reference:** [docs/ems-bus-logger.md](docs/ems-bus-logger.md) has the timers and reconnect behaviour, the load on EMS-ESP, how to read the files, and the telegrams known so far (heat request `0x02E0`, the boiler's answer `0x02D6`, parameter ranges, and the types nobody has decoded yet).

The EMS-ESP console's text format is not an official interface, so a new EMS-ESP version could change it. The logger reads the console formats of EMS-ESP 3.7 and 3.8 or later (decoded `watch` output and `/api/system/info`) and writes the same line format for both.

## Heating controller (shadow mode)

Computes the flow temperature a boiler should be asked for, from a heating curve and the demand of the rooms ("zones"). **For now it runs in shadow mode only: it computes and shows the value, and sends nothing to the boiler.** It is meant to be compared with the thermostat that controls the boiler today, before anything is switched over.

- **Setup:** Settings → Devices & services → SenorFungi → add a *Heating controller* (one) and one *Heating zone* per room.
- **Controller:** an outdoor temperature sensor, the heating curve (design outdoor temperature and design flow temperature for a 20 °C room), minimum and maximum flow temperature, room correction, summer limit, boost offset, minimum heating time and pause. Optional: the EMS ID of the bus thermostat that controls the boiler now. With the EMS bus logger running (and that ID passing its filter), the controller shows the thermostat's own flow request (`UBASetPoints`, EMS+ `0x02E0` or EMS 1 `0x1A`) and whether the thermostat is on the bus.
- **Zones:** either an existing climate entity (its current temperature and target are used; a zone in mode `off` asks for nothing) or a temperature sensor, in which case the integration creates a climate entity for the room's target with the presets *comfort* and *eco*. Per zone: whether its radiators have a controllable valve, a weight for its demand, and an optional valve position sensor (shown only).

How it decides, every 30 seconds:

1. The neediest zone (largest `(target − temperature) × weight`) sets the demand.
2. Heating starts when the demand reaches +0.2 K and stops when every zone is 0.2 K above its target, respecting the minimum heating time and pause.
3. Flow = heating curve for the outdoor temperature (shifted by the neediest zone's target) + room correction × demand, plus the boost offset while the demand is 1 K or more, limited to the minimum and maximum flow temperature.
4. No heating at or above the summer limit, or while a zone **without** a valve is 0.5 K or more above its target (its radiators can't close, so heating would overheat it). Any zone below 5 °C forces heating at the minimum flow temperature (frost protection).

Entities (device "Heating controller", plus one climate entity per zone without its own):

| Entity | Meaning |
|---|---|
| Flow target | Flow temperature the controller would ask for, 0 when it wouldn't heat. Attributes: reason, deciding zone, demand, curve value, outdoor temperature, per-zone values, `mode: shadow`. |
| Reason | Why: heating, boost, satisfied, min. heating time, min. pause, blocked, summer, frost, no outdoor temperature, no usable zone. |
| Heat request | Whether it would heat. |
| Thermostat flow request | Flow the bus thermostat last requested (with the logger and a thermostat ID). |
| Thermostat on the bus | Whether the bus thermostat sent a telegram within the presence timeout (default 150 s). |

## Development

The integration lives in `custom_components/senorfungi/`. One repository holds one integration, which is a HACS rule. Add features as new platforms (`sensor.py`, `switch.py`, …) and list them in `PLATFORMS` in `__init__.py`.

Edit `strings.json` and copy it to `translations/en.json`. Keep `translations/de.json` in sync.

### Tests

`tests/` uses [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component) and needs Python 3.14: `pip install pytest-homeassistant-custom-component`, then `pytest` in the repo root. The EMS bus tests use a fake EMS-ESP console on localhost. `ruff check .` and `ruff format .` use the settings in `pyproject.toml`.

### Release

Bump `version` in `manifest.json`, commit, and create a GitHub release with a tag that matches (for example `v0.2.0`). HACS offers it as an update.
