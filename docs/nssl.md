# Setting up for NSSL

NSSL's setup is a profile and a provider on top of the generic app:
`profiles/nssl.toml` holds the broker, the certificates, the API key and CA
(`[[auth]]`), the regional mesonets, recorded history, the logger layouts and
the field campaigns, and `providers/nssl/` holds the code and data that only
make sense for NSSL. The provider plugs
in through the same hooks as any team's ([providers.md](providers.md)), so
STORM itself doesn't depend on it. This page is the
checklist for an NSSL laptop or Pi; it follows the README's
[Setting up your team's laptops](../README.md#setting-up-your-teams-laptops),
with NSSL's files in place of a team's own. Every key it mentions is in
[configuration.md](configuration.md).

## 1. Install STORM

Follow the README's install steps (clone, `python setup.py`, map tiles).

## 2. Use the NSSL profile

```bash
cp profiles/nssl.toml storm.toml
cp .env.example .env
```

The profile sets:

- **Broker**: NSSL's AWS IoT Core endpoint, mutual TLS, topic root `storm`
  (the viewer certificate's policy only allows `storm/*`), shared items
  cleared at 08 UTC.
- **VIEWER login**: the read-only viewer certificate shipped in
  `providers/nssl/certs/viewer/`. Its AWS IoT policy
  (`viewer-policy.json` there) only lets it subscribe, and only with a
  `viewer-` client ID.
- **Shared items at launch** (`[mqtt] shared_items_url`): the NSSL API's
  `current.json` (see section 5).
- **`[providers.nssl] team = true`**: NSSL is this setup's own team. The
  provider ships with STORM and is there for every team anyway (ARCHIVE's
  **NSSL** button, all from THREDDS); `team = true` starts NSSL > VEHICLES
  on, shows NSSL's vehicles untagged, and reads pre-3.0 vehicle IDs as
  their FOFS folder names.
- **`[[auth]]`** for `api.nssl.noaa.gov` (the key, `NSSL_API_KEY`, and the
  Sectigo intermediate CA both NSSL servers leave out of their TLS chains)
  and for `data.nssl.noaa.gov` (the CA). Everything STORM asks those servers
  for goes through them.
- **Six regional mesonets** (OK, WTM, KS, CO, NE, SD) as `[[sources]]`
  tables. They read the NSSL API, so they need the key.
- **Map**: opens on Norman, OK at zoom 6. ARCHIVE's local clocks show
  Central and Mountain time. PREV LOCS shows NSSL's past deployment
  locations (`[map] deployment_locations`, from `providers/nssl/locs/`).
- **ARCHIVE**: recorded team traffic from THREDDS, then your own
  `recordings/` (`[archive] history`).
- **Logger layouts**: `fofs` (*FOFS mesonet logger*, the truck's
  `YYYYMMDD.txt`) and `gps_ka` (*GPS Ka (position only)*). The launch
  window lists both under **LOGGER FILE**.
- **Campaigns** for the case finder: VORTEX2, RiVorS, TORUS, PERiLS and LIFT.

Edit your copy if you need to. Your `storm.toml` is git-ignored, so
`git pull` won't overwrite it, and it won't pick up later changes to the
profile either. Compare it with `profiles/nssl.toml` after an update.

## 3. Certificates

VIEWER works with the shipped files. VEHICLE and MONITOR need the team
certificates, which the STORM team hands out and which are never in the
repository (ask the STORM team for them, as for `NSSL_API_KEY`). Put them here:

| File | Role |
|------|------|
| `providers/nssl/certs/AmazonRootCA1.pem` | CA certificate (shipped) |
| `providers/nssl/certs/team/storm.pem.crt` | Team device certificate |
| `providers/nssl/certs/team/storm-private.pem.key` | Its private key |

`providers/nssl/certs/team/` is git-ignored. If a file is missing, the
launch window says which one and won't launch VEHICLE or MONITOR.

**Coming from an older install?** Move `aws/storm.pem.crt` and
`aws/storm-private.pem.key` into `providers/nssl/certs/team/`. The old
`aws/storm.pem` isn't needed: the profile uses the shipped
`AmazonRootCA1.pem`. No settings carry over by themselves (there is no
silent upgrade). Copy the profile as in step 2, and pick the logger layout
again in the launch window.

## 4. NSSL API key

Put the key in `.env`:

```
NSSL_API_KEY=...
```

Request one along with the certificates. What it adds:

| Needs | Layers |
|-------|--------|
| Nothing (NSSL's public THREDDS server, and files shipped in `providers/nssl/`) | All of ARCHIVE's NSSL data, for any team: recorded team traffic, FOFS one-second vehicle obs, CLAMPS trailers, the lidar truck's radiosondes, lidar winds, Doppler lidar scans and stares, CLAMPS TROPoe. Live: past deployment locations (PREV LOCS, from `providers/nssl/locs/`) |
| `NSSL_API_KEY` | Live only: CLAMPS soundings (SOUNDINGS > NSSL), SFCOA, the six regional mesonets, the day's shared items at launch (`current.json`) |

Without the key those buttons are greyed out or hidden, and everything else
works. The user guide's section 20 lists every feature and what it needs.

`NSSL_API_ROOT` (default `https://api.nssl.noaa.gov`), set as an
environment variable, points the provider's live CLAMPS soundings somewhere
else for testing (add an `[[auth]]` table for the other server if it wants
the key). The mesonets and SFCOA are `[[sources]]` in the profile; change
their `url` there.

## 5. Things that work differently on NSSL's broker

- **Shared items at launch.** AWS IoT Core doesn't send retained messages
  on wildcard subscriptions, so annotations, drawings and cones published
  before you connected don't arrive from the broker. STORM loads them from
  the NSSL API's `current.json` at launch instead, and **↻ Refresh** in the
  annotation drawer fetches it again. This needs the key. Scan sectors
  still arrive within a few seconds. See
  [wire-format.md](wire-format.md#retained-messages).
- **Vehicle IDs.** Vehicles publish under their FOFS folder names
  (`dltruck`, `probe1`, …), so a vehicle's recorded positions and its
  one-second obs replay as one vehicle with nothing to configure. A new
  vehicle's ID should be its folder name; the steps for adding one are in
  [`providers/nssl/ADDING_A_VEHICLE.md`](../providers/nssl/ADDING_A_VEHICLE.md).
  Recordings from before 3.0 used `lid1` and `p1`–`p7`; the provider reads
  those as their folder names.
- **Recorded traffic** comes from NSSL's recorder, mirrored on THREDDS. You
  don't need to run the recorder yourself. See
  [archive-data.md](archive-data.md#nssl).

## 6. Headless publisher (Pi)

```bash
curl -fsSL https://raw.githubusercontent.com/bobbysaba/storm/main/headless/install.sh | bash -s -- --vehicle-id probe1 --data-dir /home/storm/mesonet --icon-type mesonet --profile nssl
```

A GPS Ka truck adds `--layout gps_ka`. Copy `AmazonRootCA1.pem` and the two
team files into `~/storm-headless/certs/`. Everything else is in
[`headless/README.md`](../headless/README.md).

## 7. Check it

Launch STORM and pick VIEWER: the status pill should read **● MQTT OK**, and
the SURFACE drawer should list OK through SD (greyed out without the key).
Then try VEHICLE or MONITOR with the team certificates in place.

To check that NSSL's THREDDS folders, API and the profile's mesonets still
answer (it asks THREDDS one folder at a time; don't run it in a loop):

```bash
python providers/nssl/check_sources.py
```
