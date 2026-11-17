# STORM

**Severe Thunderstorm Observation and Reconnaissance Monitor**

A desktop application for storm chasing situational awareness. It runs on a laptop in the field and is built for low-bandwidth conditions: offline map tiles, compressed radar data and efficient MQTT messaging. STORM shows live radar, satellite, hazards, surface observations, soundings and your team's vehicles on one map, and can replay a past session on a single synchronized clock.

**Full documentation is under development and coming soon.** It will cover installing, every mode, every feature, keyboard shortcuts, command-line options and troubleshooting. This README covers what you need to get STORM running.

## Modes

STORM asks for a mode each time it starts.

| Mode | Use it to | Needs |
|------|-----------|-------|
| **VIEWER** | See the team live, read-only, plus all weather layers | Nothing |
| **MONITOR** | Watch the team from base or from inside a vehicle | Certificate files |
| **VEHICLE** | Share your vehicle's observations and annotations | Certificate files |
| **ARCHIVE** | Replay a past session | Certificate files |

VIEWER is the easiest way to try STORM.

## Install

STORM runs on macOS, Windows and Linux. You need Python 3.8+ to run the setup script; STORM itself runs in a conda environment on Python 3.14 that the script creates.

**1. Clone the repo.** Do not download the ZIP: the in-app updater uses `git pull` and needs the `.git` folder.

```bash
git clone https://github.com/bobbysaba/storm.git
cd storm
```

**2. Run the setup script.** It finds (or offers to install) conda, builds the `storm` environment and puts a launch shortcut on your Desktop.

```bash
python setup.py
```

**3. Download the map tiles.** The offline base map is too large for git. Download the files from [Google Drive](https://drive.google.com/drive/folders/1q4DJ-mg94tpDWHLEkQ_50oQ3uauQ77it?usp=sharing) into `tiles/`:

```
tiles/storm.mbtiles          offline vector map
tiles/storm_nlcd.mbtiles     optional: NLCD land-cover overlay
tiles/satellite.mbtiles      optional: USGS satellite basemap
```

Without `storm.mbtiles`, STORM loads the base map over the internet instead. That is fine for trying it out, but the map is blank offline.

**4. Launch.** Use the desktop shortcut, or:

```bash
conda activate storm
python main.py
```

Pick **VIEWER** in the launch window to confirm everything works. 

## Certificates (VEHICLE, MONITOR and ARCHIVE)

These three modes need three files in an `aws/` folder in the project:

```
aws/storm.pem
aws/storm.pem.crt
aws/storm-private.pem.key
```

They are handed out by the STORM team and are never in the repository. To request them, use the contact form (coming soon with the documentation site).

## API keys

Some data comes from authenticated services: the NSSL API (surface observations, CLAMPS soundings, SFCOA, archive data) and openRouteService (routing). The keys are not in the repository. Copy `.env.example` to `.env` in the project folder and fill in the values you were given. A real environment variable of the same name takes precedence.

## Updating

If you cloned the repo, the launch window shows an update button when a new version is available. By hand:

```bash
git pull
conda env update -f envs/storm.yml --prune
```

If `git pull` fails, you probably have local changes in the way; run `git status`.

## Command-line options

Most people run `python main.py` with no flags. The options (open a case package, lite mode for slow hardware, performance logs, diagnostic profiles) are listed by `python main.py --help`.

## Headless publisher

A Raspberry Pi can publish a vehicle's data without a display, using the code in `headless/`. See [`headless/README.md`](headless/README.md).

## Repository layout

| Folder | Contents |
|--------|----------|
| `main.py`, `config.py`, `runtime_flags.py` | Entry point, constants, runtime flags |
| `ui/` | The interface: map, toolbar drawers, dialogs, sounding viewer, Video Studio |
| `data/` | Fetchers and decoders for radar, satellite, hazards, surface obs and soundings |
| `archive/` | ARCHIVE mode: session, clock and per-layer archive fetchers |
| `core/` | Shared models: radar scans, annotations, storm tracks, case packages |
| `network/` | MQTT client and sync for vehicles, annotations, cones and drawings |
| `headless/` | The Raspberry Pi publisher |
| `envs/`, `scripts/`, `setup.py` | Conda environment, launchers and the setup script |
| `tests/` | Test suite |

The map is MapLibre GL inside a Qt web view, served in-process through a custom `storm://` URL scheme, with a `QWebChannel` bridge between Python and JavaScript. 

## Citing STORM

See [`CITATION.cff`](CITATION.cff), or use GitHub's "Cite this repository".

## License

GPL-3.0-or-later. See [`LICENSE`](LICENSE).
