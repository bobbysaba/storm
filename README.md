# STORM

**Severe Thunderstorm Observation and Reconnaissance Monitor**

A field operations tool for teams that work around severe storms: real-time situational awareness while you're out, and replay afterwards. It runs on a laptop in the field and is built for low bandwidth: offline map tiles, compressed radar data and efficient MQTT messaging. It shows live radar, satellite, hazards, surface observations, soundings and your team's vehicles on one map, and can replay a past day on a single synchronized clock.

Bring your own MQTT broker and your team shares vehicles, annotations, drawings and storm-motion cones. Your own station networks, map overlays, soundings, contour layers, logger files and recorded days are added in a settings file, `storm.toml`, with no code. The built-in weather layers (NEXRAD radar, GOES satellite, SPC and NWS hazards, IEM surface obs and radiosondes, HRRR soundings) cover the US.

STORM was built for NSSL's field operations. NSSL's setup ships as one example profile of what a team can configure for itself; STORM needs none of it.

## Documentation

**https://bobbysaba.com/docs/storm/**: install, a quick start, every mode and feature, setting up your team (broker, `storm.toml`, your own data), the full `storm.toml` reference, the MQTT wire format, command-line options, shortcuts and troubleshooting.

## Modes

STORM asks for a mode each time it starts.

| Mode | Use it to | Needs |
|------|-----------|-------|
| **VIEWER** | See the team live, read-only, plus all weather layers | Nothing (a broker in `storm.toml` to see the team) |
| **MONITOR** | Watch the team from base or from inside a vehicle | A broker login in `storm.toml` |
| **VEHICLE** | Share your vehicle's observations and annotations | A broker login in `storm.toml` |
| **ARCHIVE** | Replay a past session | Nothing |

VIEWER is the easiest way to try STORM.

## Install

STORM runs on macOS, Windows and Linux. You need Python 3.8+ to run the setup script; STORM itself runs in a conda environment on Python 3.14 that the script creates.

```bash
git clone https://github.com/bobbysaba/storm.git     # clone, don't download the ZIP: the updater uses git
cd storm
python setup.py                                       # builds the conda environment and a launcher
conda activate storm
python main.py                                        # pick VIEWER to confirm it works
```

The offline base map is too big for git: download it into `tiles/`, or build one for your own area with `scripts/make_base_mbtiles.py`. Without it STORM loads the map over the internet, which is fine for trying it out. Details, per-platform notes and the tile downloads are in the [install guide](https://bobbysaba.com/docs/storm/install/).

## Set up your team

```bash
cp storm.example.toml storm.toml
cp .env.example .env
```

Set `host` under `[mqtt]` in `storm.toml` and put the password in `.env`. Then follow [Set up your team](https://bobbysaba.com/docs/storm/team/): run a broker, add your own data, put the same setup on every laptop. `python scripts/check_broker.py` checks your broker. A ready-made setup lives in `profiles/` (NSSL's, as an example).

## Contributing, security, citing

Bug reports and pull requests are welcome; see [`CONTRIBUTING.md`](CONTRIBUTING.md). Report security problems privately, as [`SECURITY.md`](SECURITY.md) describes. Cite STORM with [`CITATION.cff`](CITATION.cff), or GitHub's "Cite this repository". STORM is released under the GPL-3.0-or-later license (see `LICENSE`).
