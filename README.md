# SenorFungi

A custom Home Assistant integration. For now it is a working skeleton: a config flow and one diagnostic sensor that shows the installed version.

## Install with HACS

1. In HACS, open the menu (⋮) and choose **Custom repositories**.
2. Repository: `https://github.com/SenorFungi/senorfungi-integration`, type: **Integration**.
3. Find **SenorFungi** in HACS, download it, and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration** and pick **SenorFungi**.

## Development

The integration lives in `custom_components/senorfungi/`. One repository holds one integration, which is a HACS rule. Add features as new platforms (`sensor.py`, `switch.py`, …) and list them in `PLATFORMS` in `__init__.py`.

Edit `strings.json` and copy it to `translations/en.json`. Keep `translations/de.json` in sync.

### Release

Bump `version` in `manifest.json`, commit, and create a GitHub release with a tag that matches (for example `v0.2.0`). HACS offers it as an update.
