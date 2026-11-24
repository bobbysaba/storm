# Changelog

All notable changes to STORM will be documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## Unreleased

### Changed
- `storm.toml` problems no longer cost the whole file. Each table stands alone: one with an invalid value keeps its defaults (a bad `[units]` no longer drops the broker), and a bad `[[sources]]`, `[[auth]]`, `[providers.<name>]` or named `[obs_columns]` layout is left out on its own. Every problem is listed in one startup window and the log, not only the first, and the window now shows under the diagnostic profiles that turn MQTT off too.
- An unknown table (a misspelled `[unit]`, which was silently ignored) or key is skipped with a suggestion: "unknown key pasword (did you mean password?)". The headless publisher's `--check` gives the same suggestions.
- `archive/recorder.py` logs `storm.toml` problems and records with the rest of the file, instead of exiting.

### Fixed
- An `[archive] vehicle_obs` URL root whose `[[auth]]` key isn't set is skipped (and logged), as `[archive] history` roots already were, instead of being asked without its key.

## [3.0.0] - 2026-11-22
STORM 3.0 works for any team, not only NSSL: bring your own MQTT broker, describe your own station networks, map overlays, soundings, contour layers and archive in `storm.toml`, or plug in a code provider. NSSL's setup ships as a profile and a provider like any other team's.

**Upgrading from 2.x:** STORM no longer has a built-in broker or NSSL setup, so a 2.x install that updates shows NO BROKER until it has a `storm.toml`. Copy `profiles/nssl.toml` to `storm.toml` for NSSL (put `NSSL_API_KEY` in `.env` and the team certificates in `providers/nssl/certs/team/`; see `docs/nssl.md`), or `storm.example.toml` for your own broker (`docs/broker.md`). A `storm.toml` copied from an earlier 3.0 development profile needs re-copying too: `[[auth]]`, the SFCOA `contours` source, `shared_items_url`, `deployment_locations`, `[archive] history` and `vehicle_aliases` are all in the profile now. Raspberry Pi publishers need reinstalling (headless 0.2.0: its `[mqtt]` table replaced `cert_dir`; `install.sh --profile nssl` or `--profile-file`). Rebuild `STORM.app` with `scripts/create_app.sh` (new bundle identifier).

### Added
- `kind = "contours"` `[[sources]]`: runs of gridded fields drawn as labeled contour lines from vector tiles, one `.pmtiles` (or `.mbtiles`) file per field, with a drawer to step through runs and pick fields (see `docs/data-sources.md`). STORM reads PMTiles v3 itself (`core/pmtiles.py`); no new dependency.
- Vehicle payloads may use generic field names (`temperature_c`, `dewpoint_c`, `wind_speed_ms`, `wind_dir_deg`, `pressure_mb`) and an ISO 8601 `timestamp`, alongside the FOFS names STORM sends.
- The headless publisher accepts `icon_type = "hailcam"`.
- `scripts/check_broker.py`: checks that a broker handles retained messages, wildcard and exact-topic delivery, and message expiry the way STORM needs.
- `docs/wire-format.md`: every MQTT topic and payload, with field units, QoS, retain and expiry, for writing your own publisher or subscriber.
- A recorder for ARCHIVE: `python -m archive.recorder`, or *File > Record Broker Traffic* in MONITOR, writes everything under the topic root to daily `<root>.<kind>.<YYYYMMDD>` files in `recordings/` (`[archive] recordings`).
- `docs/archive-data.md`: the recorded-history file layout, where ARCHIVE reads it from, and the recorder.
- Your own vehicle obs in ARCHIVE: one CSV per vehicle per day (`[archive] vehicle_obs_path`, default `{vehicle}/{date}.csv`) read with `[obs_columns]`, with the same cleanup FOFS gets (missing-value codes, impossible readings, GPS glitches). Vehicles without files keep moving on their recorded positions. See `docs/archive-data.md`.
- Your own data sources in `storm.toml`, no code needed: `[[sources]]` tables of kind `station_obs` (station plots in SURFACE from a JSON or CSV feed: rows, keyed or column layouts, nested fields, unit conversion, local-time timestamps, a separate station list, station pressure reduced to sea level, dewpoint from RH, per-source freshness colors) or `overlay` (GeoJSON in the MAP drawer, styled). Either can have its own toolbar button. API keys go in `.env`. See `docs/data-sources.md`.
- `[obs_columns.units]`: logger columns in °F, kt, mph, inHg and so on, converted as they're read (GUI, headless and ARCHIVE).
- `install.sh --profile-file PATH`: take the broker from your own settings file, for setups that aren't shipped in `profiles/`. With it or `--profile`, the file's `[obs_columns]` (units included) is copied into the headless config.
- `scripts/check_data_sources.py` also checks the `[[sources]]` in your `storm.toml`. NSSL's THREDDS folders, API and the profile's mesonets are checked by `providers/nssl/check_sources.py` instead.
- Soundings as `[[sources]]` (`kind = "sounding"`): an index of launches, each a SHARPpy text, CSV or JSON profile (field map, units, missing values), or one "latest" profile with `index = false`. Each gets a button in the SOUNDING drawer, refreshes while the Skew-T viewer shows it, and is a comparison pill on soundings within 150 km. Live only.
- `archive = true` on an overlay shows it in ARCHIVE too, for fixed layers such as field boundaries.
- `docs/nssl.md`: the NSSL setup checklist (profile, certificates, API key, moving from `aws/`, headless). `providers/nssl/ADDING_A_VEHICLE.md`: adding a vehicle to NSSL's fleet (its ID, publishing, THREDDS, the code's roster). The README has a generic quickstart for your own broker.
- Code providers: a team's own Python package in `providers/<name>/` (git-ignored) or at `[providers.<name>] path`, turned on with `enabled = true`, can add station-obs, overlay and sounding sources whose data comes from its own code, and ARCHIVE history and vehicle-obs sources listed by name in `[archive]`. See `docs/providers.md`.
- `docs/agent-setup.md` (and a root `AGENTS.md` pointing to it): a questionnaire an AI coding agent can work through with a team to write its `[[sources]]` tables or code provider.
- `[units]` in `storm.toml`: temperatures in °F or °C and distances in miles or kilometres (defaults °F and miles). Wind stays in knots.
- ARCHIVE's session window is configurable: `[archive] session_start_utc`, `session_hours` and `session_activity_after_hours` (defaults 00Z, 30 hours, 12 hours), for teams whose field day isn't in the Americas.
- `scripts/make_base_mbtiles.py` builds the offline vector base map for any Geofabrik extract with Planetiler (Java 21 from `envs/tiles.yml`, a separate conda environment); `scripts/make_satellite_mbtiles.py` takes `--bbox`, `--maxzoom` and `--output`.
- `CONTRIBUTING.md` and `SECURITY.md`.
- `docs/broker.md`: setting up a broker (Mosquitto with TLS, team and viewer logins, an access list and persistence; or a hosted service), who can see what, and a table for connection problems.
- An **NSSL** button in ARCHIVE for every team: NSSL's public THREDDS data (recorded vehicles and scan sectors, FOFS one-second obs, the CLAMPS trailers, Doppler lidar and lidar winds, the lidar truck's radiosondes, CLAMPS TROPoe) in one drawer, VEHICLES · LIDAR · SOUNDING · RETRIEVALS, with items greyed out on dates without data. NSSL's provider is built in (`[providers.nssl] enabled = false` leaves it out); without the NSSL profile its vehicles start off and are tagged "NSSL", and its annotations, drawings and cones stay out. Any provider can have a drawer like it (`archive_drawer`, `docs/providers.md`), and a provider can trust its own server's CA (`AUTH`). A team's own lidars and wind profiles (`archive_lidar`, `archive_wind_profiles`) show beside NSSL's, in one lidar drawer and one wind-source list. Without the NSSL profile, NSSL's lidar files download only once LIDAR is opened, after NSSL's vehicle tracks (the truck's scans were left unplaced before).
- Lidar stares (LIDAR > STARES): the lidar truck's and the CLAMPS trailers' fixed-point stares from THREDDS as vertical velocity (or intensity, or backscatter) against time and height for the session, with the intensity threshold, colour range, cloud base, a readout, zoom and the archive clock. A day of stares can be hundreds of MB, so the window shows how far its download has got. A file cut short on THREDDS is reported as incomplete.
- Every lidar product (stares, PPI and CSM scans, VAD winds) leaves out its first four gates (the near-range blind zone), as NSSL's processing does.
- Lidar scans are only the sweeps at 5° elevation or less, scanning as a radar does (was under 45°); higher sweeps are other scan strategies, such as the VAD rings behind the VAD winds.
- The lidar truck's processed continuous-scan winds (CSM winds) are no longer offered under WINDS; its VAD winds are.
- Lidar VAD winds have a **min intensity** floor in the VAD window, starting at 1.002 and adjustable (or off): above the aerosol layer the CLAMPS and truck VADs fit noise (gate-to-gate jumps of 4–6 kt and 0–3 km SRH in the hundreds), which it clears. `VADProfile` carries an optional per-level `intensity`.
- PERiLS CopterSonde ascents are withheld until NSSL's archive of them is more complete: NSSL's drawer has no UAS item for now.
- A lidar scan window: a picked lidar's PPI and sector scans with the field, an intensity threshold, the colour range, a cursor readout (range, azimuth, height, position, value, intensity), box and scroll zoom, scan stepping or following the clock, and drawing on the map as an option (off by default).
- `docs/broker.md` says what was tested: 3.0's broker setup was run end to end against a local HiveMQ broker only; Mosquitto, hosted brokers, a remote broker and a real Raspberry Pi weren't.

---

### Changed
- The broker is no longer built in: STORM reads it from `storm.toml` (`[mqtt]`: host, port, TLS mode none / server / mutual, certificate paths, username, client-ID prefix, read-only), with passwords in `.env`. Copy `storm.example.toml`, or `profiles/nssl.toml` for NSSL's setup. See `docs/configuration.md`.
- VEHICLE and MONITOR launch when `storm.toml` names a broker login that can publish, instead of when `aws/` holds certificate files. ARCHIVE, which never connects to the broker, no longer needs one.
- VIEWER uses the optional read-only `[mqtt.viewer]` login (the NSSL profile sets it to the shipped viewer certificate), else the `[mqtt]` login without publishing.
- The broker status pill reads **MQTT OK** / **MQTT OFFLINE** / **NO BROKER** instead of AWS OK / AWS OFFLINE.
- Headless publisher: `[mqtt]` takes the same keys as `storm.toml` and has no default broker; `cert_dir` is gone. `install.sh` takes `--host`, `--port` and `--tls`, or `--profile nssl` to take them from `profiles/nssl.toml`, and creates `certs/` instead of `aws/`.
- Every MQTT topic sits under `topic_root` from `[mqtt]` (default `storm`, so existing topics don't change). The headless publisher reads the same key, and `install.sh` takes `--topic-root` or reads it from a profile.

- Shared annotations, drawings and scan sectors expire at `daily_reset_utc` from `[mqtt]` (default 8, the old fixed 08:00 UTC).
- Annotations, drawings and cones carry the vehicle ID as `creator` instead of `"local"`.
- NSSL's data is a provider, on with `[providers.nssl] enabled = true` (set by `profiles/nssl.toml`): FOFS one-second archive obs, CLAMPS, the lidar truck and past deployment locations from NSSL THREDDS; the regional mesonets, live CLAMPS soundings, SFCOA and `current.json` from the NSSL API, which also needs `NSSL_API_KEY`. Without it SURFACE is ASOS only and those buttons are hidden. The code, certificates and deployment locations moved into `providers/nssl/`.
- FOFS one-second mesonet files are found from `FOFS/Mobile-Mesonet/data/manifest.json`, an index NSSL rebuilds every hour (STORM re-reads it after an hour), instead of STORM crawling the whole THREDDS tree. Until it's published, vehicles load from their `raw/` folders and FOFS days aren't shaded on the launch calendar.
- NSSL's provider plugs in through the same hooks as a team's own code provider instead of being wired into the app, so STORM runs without `providers/nssl/`. New provider hooks feed STORM's own windows: `live_sounding_fetcher`, `archive_sounding_fetcher`, `archive_wind_profiles`, `archive_surface_playback` and `archive_lidar` (see `docs/providers.md`). The sounding drawer's NSSL button is the provider's button, labeled by the provider; its header reads *NSSL · CLAMPS* (was *NSSL · DL Truck*). `[providers.nssl]` takes options like any provider's.
- New `[[auth]]` tables give a server (or a folder on it) its key and CA wherever STORM asks it: `[[sources]]` without keys of their own, recorded-history and vehicle-obs URLs, shared items, case-package downloads, the check script and a provider's fetchers. The longest matching URL wins, and a key never goes to a URL no table covers (the Oklahoma Mesonet's public station list no longer gets the NSSL key). The NSSL profile has one for the API (key and Sectigo CA) and one for THREDDS (CA); its mesonets drop their own `api_key_env` and `ca_cert`.
- NSSL's small settings moved from provider code into the profile, as settings any team can use: `[mqtt] shared_items_url` (the day's shared items at launch; NSSL: `current.json`), `[map] deployment_locations` (PREV LOCS from a CSV file or URL), `[archive] history` URLs (NSSL's THREDDS, then its API; the `nssl` history source is gone) and `[archive] vehicle_aliases` (broker IDs to the names their files use, `p1` → `probe1`; folder and URL vehicle-obs sources use them too). A `storm.toml` copied from the NSSL profile before this needs the new profile's `[[auth]]`, `shared_items_url`, `deployment_locations`, `history` and `vehicle_aliases`.
- SFCOA is a `contours` source in `profiles/nssl.toml` instead of code in the NSSL provider (the `mesoanalysis_fetcher` hook and the `SFCOA_BASE_URL` environment variable are gone). Its button and drawer take the source's name; without `NSSL_API_KEY` the button is greyed out (it was hidden). Labels, tooltips, groups and the order of each group's buttons come from each run's `metadata.json`, so until NSSL's server adds them SFCOA's buttons show variable ids under one column. An NSSL `storm.toml` copied before this needs the SFCOA table added from the profile.
- NSSL certificates moved from `certs/` to `providers/nssl/certs/`; the team certificates go in `providers/nssl/certs/team/` instead of `aws/` (the profile uses the shipped `AmazonRootCA1.pem` as the CA, so `aws/storm.pem` isn't needed).
- Calls to IEM, NCEI and the NWS damage toolkit use the system trust store instead of the NSSL TLS context.
- Annotations, drawings and cones are published retained, deletes included, like scan sectors already were, so a client that connects late gets the day's items from any broker that sends retained messages on wildcard subscriptions (Mosquitto, EMQX, HiveMQ). An empty retained payload also deletes an item. AWS IoT Core doesn't send them on wildcards, so NSSL setups still load `current.json`; AWS certificate policies need `iot:RetainPublish`.
- The annotation drawer's Refresh button (re-fetch the shared items) only shows with `[mqtt] shared_items_url` set.
- ARCHIVE reads recorded broker traffic from the sources in `[archive] history` (folders, URL roots, or a code provider's name), first file wins. Without it: the providers' history sources, then `recordings/`. Before, every install read NSSL's. The NSSL profile lists NSSL's THREDDS copy, then its API copy (only tried with `NSSL_API_KEY`), then `recordings/`. See `docs/archive-data.md`.
- Archive NSSL / CLAMPS soundings moved into the NSSL provider; the generic archive sounding fetcher is OBS radiosondes only.
- ARCHIVE's one-second vehicle obs come from the sources in `[archive] vehicle_obs` (folders, URL roots, or `nssl` for FOFS), first source with rows wins per vehicle. Without it: the providers that are on, so FOFS for the NSSL profile and none otherwise.
- The launch calendar and case finder list the configured sources (recorded-traffic and vehicle-obs folders, URL roots' `dates.json`, FOFS with the NSSL provider) instead of scanning FOFS for everyone. Campaigns come from `[[archive.campaigns]]` in `storm.toml`; `profiles/nssl.toml` lists VORTEX2, RiVorS, TORUS, PERiLS and LIFT, so an NSSL `storm.toml` copied before this needs them added. The finder groups vehicles under RECORDED TRAFFIC, VEHICLES and MOBILE MESONET.
- Obs-file columns are `[obs_columns]` in `storm.toml` (and in `storm-headless.toml`) instead of `config.OBS_FILE_COL_*`; the defaults are the same FOFS columns. Adds a heading column, custom date/time formats and an ISO 8601 `timestamp` column.
- Case packages serve a folder's recorded-traffic and vehicle-obs files on another machine, matched by their path inside the folder.
- The ASOS station roster is refreshed from IEM every 30 days into `~/.cache/storm/asos_stations.json`; `data/asos_stations.json` is only the copy shipped with STORM and is no longer rewritten, so `git pull` updates stay clean.
- The macOS app's bundle identifier is `io.github.bobbysaba.storm` (was `edu.ou.nssl.storm`); rebuild `STORM.app` with `scripts/create_app.sh`.
- The case-package data type *Mobile mesonet (one-second FOFS)* is now *Vehicle obs*, covering your own files too.
- The regional mesonets (OK, WTM, KS, CO, NE, SD) are `[[sources]]` in `profiles/nssl.toml` instead of code in the NSSL provider. Without `NSSL_API_KEY` their SURFACE buttons are greyed out (they were hidden). An NSSL `storm.toml` copied before this needs the six tables added from the profile. Each refreshes on its own `refresh_min`; plots of these networks now drop impossible readings like vehicle obs do.
- The launch window's surface-obs row lists the station networks in `storm.toml` (SD included), and is hidden without any.
- Vehicle logger files: `[obs_columns]` also says which file in the folder is today's (`file`, a pattern with `{date}`), and its defaults are now a plain `*YYYYMMDD.csv` with an ISO 8601 `time` column and STORM's field names, not the FOFS logger's. FOFS and GPS Ka are named layouts (`[obs_columns.fofs]`, `[obs_columns.gps_ka]`) in `profiles/nssl.toml`; an NSSL `storm.toml` copied before this needs them added. Teams with several kinds of logger name a layout for each.
- The launch window's *GPS data file* box is now a **Logger File** list of those layouts, shown when there's more than one. Headless `gps_mode` is gone: `layout = "gps_ka"` (or `install.sh --layout gps_ka`) picks a layout, and `install.sh --profile` copies named layouts too.
- The map opens on `[map]` `home_lat` / `home_lon` / `zoom` in `storm.toml`, also the fallback position while your vehicle has none; the default is the middle of the contiguous US at zoom 4 (it was Norman, OK). `profiles/nssl.toml` sets Norman at zoom 6; an NSSL `storm.toml` copied before this needs the `[map]` table added.
- The radar site list has every WSR-88D site in the US and its territories (158) instead of 55 Plains sites. KOUN, NSSL's research radar, which the public feeds don't carry, is no longer listed.
- ARCHIVE's local clocks (beside UTC in the archive bar and the Video Studio) show the zones in `[map] time_zones`, else the computer's own zone, instead of always Central / Mountain. `profiles/nssl.toml` sets Central and Mountain.
- Saved UI settings are kept in one `STORM` / `STORM` store; most were under `NSSL` / `STORM` before. They're copied over once on first start, so layer order, drawers and the like carry over.
- Live radar starts on the site nearest `[map]` home instead of always KTLX (Oklahoma City); VEHICLE still switches to the site nearest its GPS once it has a fix. No nearest-site pick (home, GPS or ARCHIVE) lands on KCRI, the Radar Operations Center's test radar in Norman, which the new full site list had made the nearest site to Norman; it can still be chosen by hand.
- NSSL's ARCHIVE data never asks the NSSL API: the lidar truck's radiosondes and CLAMPS TROPoe are separate products, both from THREDDS (found through index files NSSL publishes, else by listing folders), and the profile's recorded history no longer lists the API. LIDAR, VAD and the provider sounding buttons are left for teams' own providers; NSSL's are in its drawer. NSSL's vehicles publish under their FOFS folder names (`dltruck`, `probe1`), so the profile has no `[archive] vehicle_aliases`; pre-3.0 recordings' `lid1` / `p1`–`p7` read as those names. The lidar drawer is the lidar pick, SCANS and WINDS; its field, intensity and "show on map" moved into the scan window.
- Core STORM no longer names NSSL's instruments: a provider's objects name their own (`uas_name`, `pill_labels`, `description`, a lidar's `name` and `marker`, a sounding set's `label`; see `docs/providers.md`), and STORM uses generic words without them. NSSL's buttons and windows read as before. The sounding sources `clamps_tropoe` and `coptersonde` are now `retrieval` and `uas` (`coptersondes_ready` / `fetch_coptersondes` are `uas_ready` / `fetch_uas`), and case packages' `clamps surface`, `clamps profiles` and `coptersonde` kinds are `instrument surface`, `instrument profiles` and `uas` (older packages keep their labels). The export dialog offers a provider's other kinds too.
- Internal names: the contours layer, drawer and feature flag are `contours` (were `sfcoa`; saved layer order, pins and sessions carry over, and `icon = "sfcoa"` still reads), and the archive instruments mixin is `ui/app/main_window_instruments.py`. The vehicle ID placeholder is `car1` and PREV LOCS says "past deployment locations".

### Fixed
- When the broker refuses STORM's subscriptions (a `topic_root` the login may not use), the status bar says so instead of showing MQTT OK over an empty map; the recorder logs it too.
- The headless publisher and the recorder say why they can't reach the broker (the same reasons as STORM's status bar), once per reason instead of retrying silently, and the publisher's `--check` (which `install.sh` runs before enabling the service) connects once: a refused login or a TLS problem fails it, an unreachable broker only warns.
- A logger file whose header lacks the `[obs_columns]` position columns (or has no header) is reported once in the log, by the headless publisher and VEHICLE mode alike, instead of every row being skipped silently.
- A dropped broker connection says "connection dropped" instead of "unspecified error".
- STORM says why it can't reach the broker: an untrusted certificate, a CA certificate not marked as a CA (Python 3.13+ refuses those), a certificate for another host name, a broker that wants a client certificate, a port that doesn't speak TLS, or nothing listening. Before, the status bar stayed empty or said "unspecified error". A refused login keeps its reason ("not authorized") instead of being overwritten by "protocol error".
- `scripts/check_broker.py` checks the viewer login with `STORM_MQTT_VIEWER_PASSWORD` (it sent the team password), reads both logins through the same settings code as STORM, checks that the broker won't let the viewer login publish, and explains TLS failures instead of stopping with a traceback.
- `scripts/check_broker.py` reads the broker password from `.env` and the settings file from `STORM_CONFIG`, as STORM does; it used to fail with "bad username/password" when the password was only in `.env`.
- `scripts/check_broker.py` says when a broker takes QoS 0 only (STORM needs QoS 1), and gives the broker's reason when it drops the connection on a publish, instead of always suggesting AWS IoT's retain permission.
- The OBS sounding station list had seven sites IEM has no data for (Denver, Miles City, Pocatello, Williston, Mobile, Grand Rapids and Louisville) and no sites outside the central and southern US. It now has the 80 US upper-air sites in IEM's RAOB network, Alaska, Hawaii and Puerto Rico included.
- The live GOES CONUS imagery was clipped to the central US. The optional NLCD and satellite basemaps now take their extent from their files instead of a built-in box, so tiles built for another area show.
- In ARCHIVE without a vehicle-obs source, the OBS status read "probing" for the whole session; it reads "MQTT fallback".
- In ARCHIVE, the radar finishing a load cleared whatever the status bar said (a picked lidar's place and first scan, a sounding's "none by …"); it now clears only its own "Radar: loading …".
- The lidar drawer's text ("Pick a lidar on the map (1 location)") was cut off when it changed after the drawer opened.
- On AWS IoT Core (NSSL's broker), a client that connected late never got the current scan sectors, because AWS doesn't send retained messages on wildcard subscriptions. STORM now also subscribes to each vehicle's scan-sector topic as soon as it hears from the vehicle.

### Removed
- The SatSquatch mesoanalysis overlay (MESO, admin mode only), which wasn't being used. Admin mode no longer turns on any layer.

## [2.0.0] - 2026-11-13
STORM 2.0 is a large archive-mode release: case packages, a Video Studio, storm tracks, observation trails, velocity dealiasing, and CLAMPS, lidar, CopterSonde, ASOS and damage-path replay, plus a command palette and a map right-click menu in every mode.

### Added
- Case packages: *File > Export Case Package…* saves an archive case (storm track, case settings, source addresses and checksums, and optionally the data files) as a ZIP; *File > Open Case Package…* or `python main.py --case <zip>` reopens it, serving packed files before downloading anything.
- Video Studio (*File > Video Studio*, Ctrl+Shift+V): keyframe a case's clock, camera and radar, preview it on the map, and export MP4, GIF or PNG frames at up to 4K.
- Menu bar (File, Edit, View, Help), with every entry also in the command palette.
- Command palette (Ctrl+K / ⌘K): search actions, drawers, radar sites and products, satellite sectors, SPC products, vehicles, deployment locations and places.
- Map right-click menu: sounding here, route to / from here, measure from here, place an annotation, start a drawing, nearest radar sites and copy lat/lon on bare map; edit / move / delete, follow, timeseries or open text on map features.
- Storm tracks (archive, TRACK drawer, T): one point per radar frame, placed and dragged on the map, with undo / redo, a table of exact times and positions, CSV import / export (MESO-VIEW columns), autosave to `storm_tracks/`, and mean / current storm motion. The sounding viewer shows the observed motion and its SRH.
- Observation trails (archive, VEHICLES panel): each platform's recent path colored by a measured or derived quantity (θ, θv, θe, θw, mixing ratio, wind components…), with optional wind barbs, storm-relative barbs and time-to-space.
- Velocity dealiasing (archive, RADAR > DEALIAS): region-based unfolding of NEXRAD velocity; the archive bar says whether the frame on screen is dealiased.
- Sharp radar when paused: after the archive clock stops, the frame on screen is re-rendered at up to 4096 px.
- Archive time slider over the whole session, with hour labels, radar-scan ticks, data coverage, and previous / next radar scan buttons.
- Archive sessions run past 00Z, to the next morning's 06Z, trimmed to the day's last activity.
- One-second FOFS mobile mesonet data in archive mode for every user, found from the THREDDS index even on dates with no MQTT history, with value, position and date cleanup.
- Launch window case finder: dates with data are shaded in the calendars, *Browse available cases…* finds dates by campaign, year, month and vehicle, and *Open case…* opens a case package. *File > Change Day…* returns to the launch window without quitting.
- Archive ASOS replay (SURFACE > ASOS) and NWS damage-survey tornado paths (HAZARDS > DAMAGE PATHS) inside a drawn box.
- Archive infrared satellite (SATELLITE > IR, band 13) alongside visible.
- Archive CLAMPS and lidar truck data: lidar wind profiles in the VAD window, NSSL sondes and TROPoe soundings, CLAMPS trailer surface obs on the map, Doppler lidar PPI scans (LIDAR), and PERiLS CopterSonde ascents (SOUNDINGS > COPTERSONDE).
- Performance logging (`scripts/launch_storm_perf_log.bat`, `scripts/summarize_perf_log.py`, `scripts/collect_perf_logs.py`) and a data-source check (`scripts/check_data_sources.py`).
- Colorado Mesonet surface observations are now available from the SURFACE drawer and launch dialog alongside OK Mesonet, WTM, KS Mesonet, and ASOS.
- CO Mesonet station plots use the NSSL API-hosted `co_mesonet.json` feed with station positions and names from `co_metadata.json`.
- Nebraska Mesonet surface observations are now available to all users from the SURFACE drawer and launch dialog.
- NE Mesonet station plots use the NSSL API-hosted `ne_mesonet.json` feed with embedded station metadata.
- Synced live loop: playing or stepping the radar loop moves the satellite to the matching frame (SYNC, on by default) and, optionally, vehicle markers to where they were (VEH). A LOOP chip by the clock returns to live.
- Headless publisher (`headless/`): a Qt-free `storm-headless` service for Raspberry Pis that tails a vehicle's mesonet logger file and publishes to MQTT exactly like VEHICLE mode, with a one-line installer, `--check`, and `--dry-run`.

### Changed
- VIEWER is open to anyone and shows the team's vehicles, annotations, drawings, cones and scan sectors live (read-only, through a viewer certificate). VEHICLE, MONITOR and ARCHIVE need the MQTT certificate files instead of a passphrase; admin mode still asks for one.
- Archive loading no longer blocks the window: a small indicator lists what is still loading while the map stays usable. The modal archive loading dialog is gone.
- Archive replay shows each annotation, drawing and cone as of the clock, so edits appear, deletions disappear, cones expire after their hour, and rewinding no longer re-adds everything. MQTT history is read from THREDDS first.
- Archive radar picks its station from vehicle positions near the session start, waiting briefly for one-second data when MQTT has none.
- Archive soundings have no HRRR source; NSSL reads the lidar truck's sondes, then the NSSL API, then CLAMPS TROPoe.
- Data endpoints are collected in `data/endpoints.py` and THREDDS paths in `data/thredds_paths.py`.
- Surface observation diagnostics and layer ordering now include CO and NE Mesonet as independent 5-minute mesonet sources.
- MESO satellite keeps older frames at one per 5 minutes, up to 40 minutes back, instead of dropping everything past the newest 10.
- `ui/app/main_window.py` is split into mixins by area (`main_window_radar.py`, `main_window_vehicles.py`, …); behavior is unchanged.
- Obs file parsing and the vehicle MQTT payload now live in `headless/storm_headless` and are shared by the GUI and the headless publisher; behavior is unchanged.

### Fixed
- Closing the SATELLITE drawer before startup finished raised an error, and a sector picked then was ignored.
- Closing the window during an archive session could fire callbacks on a closed window; archive workers are now silenced and the radar render pools shut down on close.
- Archive data requests use verified TLS.

---

## [1.5.0] - 2026-05-07
### Added
- Kansas Mesonet surface observations are now available from the SURFACE drawer and launch dialog alongside OK Mesonet, WTM, and ASOS.
- KS Mesonet station plots use the NSSL API-hosted `ks_mesonet.json` feed with embedded station metadata.

### Changed
- Surface observation diagnostics, layer ordering, and station-model rendering now include KS Mesonet as an independent 5-minute mesonet source.

---

## [1.4.0] - 2026-05-07
### Changed
- Migrated NSSL-hosted CLAMPS soundings, surface mesonet feeds, annotation snapshots, archive annotation logs, and SFCOA overlays to authenticated NSSL API endpoints.
- SFCOA now discovers available runs from the API index and product metadata from each run's `metadata.json`.
- NSSL CLAMPS soundings now use the API directly in live and archive modes without THREDDS fallback.

### Removed
- Removed the HRRR map overlay product and its UI controls.
- Removed legacy NSSL THREDDS fallback code for CLAMPS soundings.

## [1.3.0] - 2026-04-28
### Added
- MAP drawer now exposes optional offline NLCD land-cover and USGS satellite-basemap toggles to normal users when `tiles/storm_nlcd.mbtiles` and `tiles/satellite.mbtiles` are present.
- Land-cover controls include an opacity slider and NLCD class legend, with hover labels for land-cover classes on the map.
- Added `scripts/make_satellite_mbtiles.py` to build the optional USGS satellite basemap cache.
- Added high- and low-pressure annotation markers with a pressure selector in the placement dialog.

### Changed
- NLCD land cover and satellite basemap no longer require admin mode.
- Routing and measurement controls now live under the MAP drawer with the optional base-layer controls.
- Road labels and road hover readouts are available at lower zoom levels and prefer route/reference shields when available.

## [1.2.0] - 2026-04-24
### Added
- SFCOA mesoanalysis overlays are now available in normal user sessions, with valid-time stepping, refresh controls, grouped variable buttons, and MapLibre vector-tile rendering.

### Changed
- SFCOA no longer requires admin mode; the SFCOA toolbar drawer appears alongside the other standard map layers.

---

## [1.1.0] - 2026-04-16
### Added
- ASOS/AWOS surface observations — draw a map bounding box to fetch IEM ASOS current observations, then render them as full station plots alongside OK Mesonet and West Texas Mesonet data
- ASOS bbox reuse and redraw workflow — toggling ASOS back on reuses the last domain; the surface status text includes a `new box` link to select a replacement domain
- Surface station freshness coloring — surface station plots color their center dot by observation age, with wider freshness thresholds for hourly ASOS reports
- Custom drawing styling — polylines and polygons can be titled and styled with custom color and line style; saved edits sync through the existing drawing workflow
- ASOS station plot transport optimization — station plot PNGs are served through the in-process `storm://` scheme instead of being embedded as base64 payloads

### Changed
- Surface obs drawer now supports OK Mesonet, WTM, and ASOS controls from one place
- ASOS selection uses the application accent color for the click-drag bounding box
- Surface plot caching now includes observation timestamp so station freshness colors update from the observation valid time rather than stale cached plot images
- OK Mesonet and WTM surface plots render and appear as a single batch; ASOS plots are chunked because user-selected domains may contain many stations

### Fixed
- ASOS plot URL decoding for station IDs served via `storm://app/plots/...`
- ASOS bbox draw mode now exits and restores map interaction state immediately after selection
- Stale ASOS render batches are discarded when a new bbox is requested

---

## [1.0.0] - 2026-04-14
### Added
- Vehicle meteorological timeseries dialog — interactive time-series plots of temperature, dewpoint, wind speed/direction, and pressure for any tracked vehicle; scroll-wheel zoom, click-drag selection zoom, double-click to reset, and inline cursor readouts with 10-second grid snapping; works in both live and archive modes
- VAD wind profile — initial support for VAD-derived wind profiles from NEXRAD data
- Layer ordering pill — floating UI control to reorder map layer draw order (radar, satellite, hazards, annotations, drawings) at runtime
- Screenshot capability — save the current map view as an image from the layer pill
- CWA warning display — NWS warning polygons now include county warning area context and improved text formatting
- Warning filtering — filter active NWS warnings by type; improved relevance for field operations
- Annotation expiration — annotations now carry a configurable expiration time and auto-clear from the map when expired
- Auto environment updating — `conda env update --prune` step integrated into the in-app update flow

### Changed
- NSSL/OBS sounding dialog defaults to the most recent available sounding on open
- NSSL sounding unit handling fixed (temperature/dewpoint consistency)

### Fixed
- Radar data toggle state bug — toggling radar off/on no longer drops the last fetched frame
- Surface station display bug during network interruptions
- Layer pill layout and sizing on various screen resolutions
- Satellite/radar map projection alignment fix
- Various stability patches and minor UI refinements

---

## [0.9.0] - 2026-03-31
### Added
- Archive mode — replay any past session with full data reconstruction; select a date/time at launch to enter archive playback
- Central time controller with play/pause, configurable speed multipliers (1×–300×), and ←/→ step buttons (30-second steps)
- Archive fetchers for NEXRAD radar, satellite, hazards, soundings, and MQTT vehicle position data — all synchronized to the archive clock
- Archive controls bar with timeline scrubber, playback speed selector, and per-layer status indicators (radar, satellite)
- Archive loading dialog — shows fetch progress before playback begins
- Window title and status pill reflect the active archive timestamp (`[ARCHIVE YYYY-MM-DD HH:MMZ]`)

---

## [0.8.0] - 2026-03-25
### Added
- Mesonet surface observation overlay — live station data fetched and displayed on the map
- Observed sounding dialog — fetch and display real-time vertical profiles from surface obs networks
- CLAMPS sounding support — additional sounding data source via CLAMPS fetcher
- Routing and turn-by-turn navigation with off-route recalculation and arrival detection
- On-launch data fetch selection — choose which data products to load at startup

### Changed
- Status pill top row reorganized: version anchor (`STORM vX.X.X`), update indicator, and status message now occupy a dedicated top row above mode/position and connectivity rows
- Launch window and viewer mode patching

---

## [0.7.0] - 2026-03-19
### Added
- HRRR point sounding dialog — click any map location to fetch a live vertical atmospheric profile from the open-meteo HRRR API (free tier, single HTTP request per click)
- Skew-T log-P diagram with temperature, dewpoint, virtual temperature curve, wind barbs, surface-based parcel profile, and CAPE/CIN shading
- SHARPpy-inspired features: dendritic growth zone shading (-10 to -20°C), effective inflow layer bracket on left spine, AGL height reference lines (0.5–9 km) in red
- Hodograph inset (color-coded by height: 0–3 km red, 3–6 km gold, 6–9 km blue) with EIL segment highlight, Bunkers RM/LM dots, and storm motion dir/spd readout
- Parcel table showing CAPE, CIN, LCL, LFC, and EL for surface-based, mixed-layer, and most-unstable parcels
- Kinematics table showing bulk shear, SRH, and mean storm-relative wind for 0–500 m, 0–1 km, 0–3 km, and 0–6 km layers
- Composite indices row: LR 700–500, LR 0–3 km, SFC θe, PW, Convective Temperature, STP, SCP, EHI — with threshold-based color coding
- F0–F3 forecast hour scrubber in the dialog header (cyan accent); header displays both init time and valid time
- Interactive pressure-level cursor readout (hover over SkewT to see T, Td, wind, height)

### Changed
- Point sounding data source: open-meteo HRRR CONUS at 3 km / hourly resolution; rate limit 10,000 calls/day on free tier

## [0.6.0] - 2026-03-13
### Added
- Internet connectivity indicator in status pill (● NET OK / ● NET SLOW / ● NO INTERNET) — TCP check to 1.1.1.1:53 every 30 seconds
- "AWAITING VEHICLES..." placeholder in vehicle panel that auto-hides after first fetch completes

### Changed
- Tile and asset serving migrated from Flask (localhost:8765) to QWebEngineUrlSchemeHandler (storm://app/) — no open TCP port, no firewall exposure, faster startup
- Flask and Werkzeug removed as dependencies from both Mac and Windows env files
- MQTT status indicator renamed: CONNECTED → AWS OK, OFFLINE → AWS OFFLINE
- Monitor mode badge in status pill renamed: OBSERVER → MONITOR
- Update check failure message changed from red error to amber warning with "PROCEED AND TRY AGAIN LATER" guidance
- Update available text simplified from "N updates available" to "UPDATE AVAILABLE"
- Git fetch timeout in launch dialog reduced from 10s to 5s for faster failure on slow connections

### Fixed
- Hazard error clear timer was incorrectly wired to the radar error clear method — each now only clears its own prefix
- Radar error in status bar now clears immediately when a successful scan arrives instead of waiting for the timer
- Vehicle panel placeholder visibility check used `isVisible()` which returned False when panel was closed — now hides unconditionally after first fetch
- Net connectivity indicator used `QTimer.singleShot` from a background thread (unreliable) — replaced with `_NetChecker` QObject using a proper pyqtSignal

---

## [0.5.0] - 2026-03-08
### Added
- Hazard overlay panel (SPC and NWS layers accessible via HAZARDS toolbar button)
- SPC Day 1 convective outlook (MRGL / SLGT / ENH / MDT / HIGH risk tiers)
- SPC tornado, wind, and hail probability overlays
- SPC severe thunderstorm and tornado watches
- SPC Mesoscale Discussions via NOAA MapServer GeoJSON endpoint
- NWS active warnings with per-event color coding
- Click any SPC outlook or MD polygon to read the full discussion text in a sliding panel
- Version number displayed in window title and status overlay

### Fixed
- SPC outlook now correctly renders ENH (Enhanced) and MRGL (Marginal) risk tiers — previously silently dropped
- Hazard and annotation drawers now have transparent backgrounds consistent with the radar drawer
- NWS warning bounding box updates dynamically as the map is panned

---

## [0.4.0] - 2026-03-08
### Added
- Variable radar resolution control
- Front annotations (cold, warm, stationary, occluded, dry line)

### Fixed
- Default window size and position settings on Windows
- Vehicle ID assignment bug

---

## [0.3.0] - 2026-03-06
### Added
- Previous deployment locations overlay
- Windows compatibility (Chromium/ANGLE GPU workarounds, setup scripts)
- macOS and Windows application build and update scripts

### Fixed
- Various Windows setup and startup bugs
- Small road layer visibility adjustments

---

## [0.2.0] - 2026-03-05
### Added
- NEXRAD radar overlay with site selector, product toggle (reflectivity / velocity), and frame playback
- Annotation tools (road conditions, storm motion, point markers)
- Measure tool

---

## [0.1.0] - 2026-02-28
### Added
- Initial build — MapLibre GL map with local MBTiles tile server
- Basic application shell, dark theme, floating toolbar
