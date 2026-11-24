# Adding a vehicle to NSSL's fleet

What to do when a vehicle joins NSSL's fleet (or an old one comes back), so
that it shows up live, in the recorded traffic, and in ARCHIVE with its
one-second obs. The setup itself (profile, certificates, API key) is in
[docs/nssl.md](../../docs/nssl.md).

## 1. Pick its ID: the FOFS folder name

A vehicle's ID on the broker and its folder on THREDDS
(`FOFS/Mobile-Mesonet/data/<id>/`) must be the same name. That is how
STORM matches a vehicle's recorded positions with its one-second obs, for
NSSL's own laptops and for every other team's NSSL button.

- **Lowercase letters and digits only**, such as `probe8` or `hailcam2`. No
  hyphens, underscores or spaces. STORM turns `_` into `-` in vehicle IDs,
  and the FOFS index skips folder names with `-`, so a name with either can
  never match. (`noxp_scout` is an old folder that can't match a broker ID
  for this reason.)
- **Not one already used.** The folders on THREDDS and
  `KNOWN_FOFS_PLATFORMS` in
  [`archive/vehicle_aliases.py`](archive/vehicle_aliases.py) list the names
  taken. Reusing a retired name (`probe3`) is fine only if it is the same
  vehicle, since its old days replay under that name too.
- **Not `storm`.** That is the app's own ID when nothing is set.

## 2. Set up the vehicle

**A laptop running STORM:** in the launch window pick VEHICLE, enter the ID
under **VEHICLE ID**, then the logger's **DATA DIRECTORY**, its **LOGGER
FILE** layout (`FOFS mesonet logger` or `GPS Ka (position only)`) and the
**VEHICLE ICON**.

**A Pi running the headless publisher:**

```bash
curl -fsSL https://raw.githubusercontent.com/bobbysaba/storm/main/headless/install.sh | bash -s -- --vehicle-id probe8 --data-dir /home/storm/mesonet --icon-type mesonet --profile nssl
```

Add `--layout gps_ka` for a GPS Ka logger. Copy `AmazonRootCA1.pem` and the
two team certificate files into `~/storm-headless/certs/`. Every option is
in [headless/README.md](../../headless/README.md).

Both use the shared team certificate, so nothing changes on AWS IoT. The
client ID comes from the vehicle ID, which is one more reason IDs must be
unique: two vehicles with the same ID keep knocking each other off the
broker.

## 3. Publish its files on THREDDS

The logger's daily files go under the vehicle's folder:

```
FOFS/Mobile-Mesonet/data/probe8/raw/YYYYMMDD.txt
```

in the FOFS mesonet logger's format. The hourly FOFS index
(`FOFS/Mobile-Mesonet/data/manifest.json`) picks up the new folder by
itself. Once it lists it (within the hour, and STORM re-reads it within
another), every STORM finds the vehicle by itself: ARCHIVE loads its obs, and
the archive calendar shows its days under **Other mesonet vehicles** until
step 4 gives it a name.

Recorded traffic (positions, scan sectors) needs nothing: NSSL's recorder
keeps everything on the broker under `storm/`, whatever the vehicle.

## 4. Register it in the code (optional, recommended)

These make the vehicle a named, first-class entry rather than one found
only through the index. One change, with a test:

| Where | What to add | What it does |
|-------|-------------|--------------|
| `KNOWN_FOFS_PLATFORMS` in [`archive/vehicle_aliases.py`](archive/vehicle_aliases.py) | `"probe8"` | Probed for files when the FOFS index can't be read; gives it its own row on the calendar |
| `_FOFS_STAGE_NAMES` in [`archive/catalog.py`](archive/catalog.py) | `"probe8": "Probe 8"` | Its name on the calendar |
| `_LATEST_OBSERVED_YEAR` in [`archive/catalog.py`](archive/catalog.py) | `"FOFS-probe8": 2027` | Only orders the calendar's scan, newest first; update it each season |

Run `python -m pytest -q` and add a line to `CHANGELOG.md`.

## 5. Check it

- **Live:** launch STORM in VIEWER. The vehicle should appear on the map
  with its icon a few seconds after it starts publishing.
- **ARCHIVE**, the day after its first deployment, once the files are on
  THREDDS: open that date. With the NSSL profile it appears under its ID
  with NSSL > VEHICLES on; for any other team it appears as
  `NSSL probe8` once VEHICLES is turned on. Its **Timeseries** (right-click the
  vehicle) should show the one-second obs.
- `python providers/nssl/check_sources.py` checks that THREDDS answers.

## Special cases

**Renaming a vehicle.** Publish under the new name from then on and rename
its THREDDS folder (or start a new one). Add `"oldname": "newname"` to
`LEGACY_IDS` in [`archive/vehicle_aliases.py`](archive/vehicle_aliases.py)
so recordings from before the rename replay under the new name and find
its files. Add the pair to `tests/test_vehicle_aliases.py`.

**A vehicle that carries instruments** (a lidar, radiosondes, a CLAMPS-like
trailer). Its instrument files live in their own THREDDS folders, not
under FOFS, and are registered in [`paths.py`](paths.py) (the lidar,
wind-profile, surface and sonde tables). The lidar scan window and the
lidar truck's radiosondes follow the vehicle named by `track_vehicle`
(`"dltruck"`) in `archive/raw_lidar_quicklook_fetcher.py` and
`archive/sounding_archive_fetcher.py`. A second lidar vehicle needs more
than a table entry; talk it through with whoever maintains STORM.
