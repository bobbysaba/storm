# STORM archive plan (port from the storm_ty_edits fork)

The fork at `../storm_ty_edits` (a plain copy branched from `4d586b7`) rebuilt much of ARCHIVE mode. Phase 1 of the review ported its changes to the whole tool and the live viewer; they are in the working tree, uncommitted. This plan covers phase 2: the archive work, split into sessions of one coherent change each.

## How to run a session

- Start a fresh session with: "Read `ARCHIVE_PLAN.md` and do session N."
- Read the fork's files listed for the session and port them into this repo's structure. Don't copy them over: this repo split `main_window.py` into mixins (`ui/app/main_window_archive.py`, `_radar`, `_vehicles`, …) and restyled the UI. Fork code that lives in its `ui/app/main_window.py` goes into the matching mixin.
- Keep the classic UI: style with the `ui/theme.py` tokens (`P.*`), keep this repo's tooltips and control positions. Don't bring over the fork's tooltip removals or its `top: 52px` controls offset.
- URLs come from `data/endpoints.py` and `data/thredds_paths.py`. `tests/test_endpoints.py` fails on hard-coded URLs.
- Add or port tests with every change. Run the full suite at the end of the session (`conda run -n storm python -m pytest -q`).
- Nothing is committed unless the user says so. Commit points between sessions are recommended, so each session's diff is reviewable by itself.
- At the end, set the session's **Status** line here: done, with notes on what changed from the fork's version and anything left unverified.
- The fork's archive tests pass in the fork (163 passed, checked 2026-11-11). Porting a fork test file is the quickest start on a session's tests.

## Before session 0

Phase 1 and the UI overhaul are uncommitted in the same files these sessions touch. Commit them (the user's call) before session 1, so each session starts from a clean tree.

## Order and dependencies

| # | Session | Plan items | Needs |
|---|---|---|---|
| 0 | NSSL data access check | prerequisite | — |
| 1 | Archive radar fixes | A1–A5 | — |
| 2 | Hazard and sounding fixes | A6–A8 | — |
| 3 | Replay, radar auto-pick, safe close | A9, A10, A12 | 0 |
| 4 | One-second FOFS, archive tools without admin | A11, B17 | 0 |
| 5 | Sessions past 00Z | B14 | 1, 3, 4 |
| 6 | Archive time slider, non-blocking loading | B13, B15 | 5 |
| 7 | Sharp radar when paused | B16 | 1, 6 |
| 8 | Storm tracks: model and map editing | D25 | 6 |
| 9 | Storm tracks: table, files, motion | D25 | 8 |
| 10 | Velocity dealiasing | D26 | 1 |
| 11 | Observation trails | D27 | 4, 9 |
| 12 | Case packages and menu bar | D28, D31 | 1–11 |
| 13 | Video Studio: model and export | D29 | 6, 12 |
| 14 | Video Studio: panel | D29 | 13 |
| 15 | Launch window case finder, change day | D30 | 5, 12 |
| 16 | Damage paths and ASOS replay | C23, C22 | 6 |
| 17 | Infrared satellite | C24 | 5 |
| 18 | CLAMPS and CopterSonde | C18, C21 | 0, 5 |
| 19 | Lidar | C20 | 4, 18 |

Out of scope: NOXP mobile radar (C19), storm-relative velocity (held), the fork's satellite rewrite (this repo's own is better), shipping the fork's 109 existing storm tracks, and the fork's day-checker, benchmark and audit scripts.

## Decisions taken

- **Storm tracks without a new dependency.** The fork adds `openpyxl` only to read and write Excel copies of tracks. Tracks are saved as CSV instead (stdlib `csv`), in MESO-VIEW's column layout, so files still open in MESO-VIEW and in Excel. No shipped tracks, no import script.
- **Dealiasing without Py-ART (confirmed at session 10).** The fork calls Py-ART's `dealias_region_based`, which needs `arm_pyart` (plus `netcdf4` and its own dependencies). With NOXP out of scope, Py-ART would be there for this alone. Instead, write the same region-based method with `scipy.ndimage` (already shipped): label regions of similar velocity, then unfold each region by multiples of twice the Nyquist velocity to agree with its neighbors. Check it against Py-ART on a few aliased cases using the `dissertation` conda env (it has Py-ART); nothing from that env ships. If it falls clearly short, add `arm_pyart` and use the fork's code.
- **Menu bar (D31): adopt.** File / Edit / View / Help. On macOS it sits in the system menu bar and takes no window space; on Windows and Linux it adds a row of about 24 px above the toolbar. Several entries repeat what the command palette (Ctrl+K) and shortcuts already do (screenshot, error log, debug panel). The new ones (open/export case package, Video Studio, change day, user guide, About) need a findable home. Keep this repo's shortcuts (screenshot stays Ctrl+S, not the fork's Ctrl+Shift+S), and add every new action to the command palette too.
- **Infrared satellite (C24): data is manageable.** A GOES CONUS band 13 (IR) file is about 3.9 MB, against about 62 MB for the band 2 (visible) file this repo already uses (checked 2026-05-17 22Z). Added to this repo's fetcher as a VIS / IR choice; the fork's rewrite isn't used.
- **Trails (D27): one session.** Their modules (`core/derived.py`, `core/trails.py`, `ui/controls/trail_controls.py`) port almost as they are. Storm-relative barbs and time-to-space use the track's motion from session 9.

---

## Session 0: NSSL data access check

On 2026-11-11, from this machine:
- the NSSL API (`api.nssl.noaa.gov/annotations/storm.vehicles.<date>`) answered 404;
- THREDDS FOFS paths answered 410 without a browser user agent and 404 with one;
- the THREDDS catalog front page now links to `customConfig/FOFS.html`.

So this repo's archive vehicles may not load at all right now. Sessions 3, 4, 18 and 19 depend on these sources.

- Port `scripts/check_data_sources.py`'s THREDDS checks (phase 1 already added the script) and run it.
- Find where `FOFS/Storm/annotations` (recorded MQTT), `FOFS/Mobile-Mesonet/data` and the CLAMPS folders went, if they moved. Update `data/thredds_paths.py` (adding the fork's folder names from `archive/thredds_paths.py` as needed) and `config.NSSL_API_ROOT`.
- Note that THREDDS needs a browser-like `User-Agent` (the fork's `vehicle_obs_archive_fetcher.py` comments describe its firewall).
- If neither host answers, write that down here and continue with sessions 1–2, which don't need NSSL.

**Done when:** each NSSL source the plan uses has a working URL recorded, or is noted as down.
**Status:** checked 2026-11-11 (the user ran the fork's `scripts/check_data_sources.py`). The user is still investigating the API.
- **THREDDS works from Python** with STORM's SSL context (`_ssl_context()` in `vehicle_obs_archive_fetcher.py`) and a browser-like User-Agent. Plain `curl` gets 404/410, so test THREDDS with Python, not curl.
- **50 of 51 folders answer:**
  - FOFS Mobile-Mesonet;
  - every CLAMPS lidar, TROPoe, surface and sonde folder, except the optional CLAMPS2 `aeri_mwr.v2`;
  - raw lidar;
  - CopterSonde;
  - NOXP.
- **Recorded MQTT history moved** to **`FOFS/STORM/annotations`** (uppercase; the user found it). The fork's `FOFS/Storm/annotations` answers 404. Checked from Python:
  - 162 files: 51 vehicles, 35 annotations, 35 drawings, 28 cones, 13 scan_sectors;
  - vehicle files cover 2026-04-01 to 2026-06-26, in the same JSONL format;
  - the folder holds the 2026 season only (the fork noted a rolling ~5-month retention), so older dates still need the API.
- **The NSSL API answers 404 on every path**, the live ones too: `current.json`, all six state mesonets, CO metadata and the sonde index.
- Effects:
  - recorded vehicles, annotations, cones, drawings and scan sectors exist only on THREDDS while the API is down, so session 3's THREDDS-first fetch is needed. Add `FOFS_ANNOTATIONS = "FOFS/STORM/annotations"` to `data/thredds_paths.py`, read from THREDDS with STORM's SSL context and User-Agent, and keep the API as the fallback;
  - session 4 matters more: `fofs_index.py` finds vehicles from Mobile-Mesonet directly, so archive vehicles can come back from one-second data even without MQTT. Today this repo starts one-second data only from MQTT's vehicle list, so it shows none;
  - live mesonet observations and NSSL soundings, which read the API, are down too (outside this plan).
- Open-Meteo historical timed out once during the check (likely transient; recheck in session 2).

## Session 1: Archive radar fixes (A1–A5)

File: `archive/fetchers/radar_archive_fetcher.py`. Fork reference: the same file.

- **A1, dates before about 2016.** The AWS keys end in `.gz` (`KTLX20130520_000122_V06.gz`); this repo only tries `_V06`, `_V03` and no suffix, so every such scan is a 404 (confirmed). Fix:
  - keep each scan's exact key from the bucket listing (`_index_keys`) and download that;
  - fall back to the guessed names plus `.gz` variants;
  - decompress when the bytes start with `1f 8b`.
- **A2, split cuts.** Build the tilt list from the VCP's intended angles (`vcp_info.els`, by `el_num`). Record which products each sweep has. When the chosen product isn't in the chosen sweep, use the nearest sweep that has it, instead of silently showing reflectivity. Raise "No <product> data in this radar volume" when no sweep has it. Store `scan.tilt_index` so the tilt picker follows.
- **A3, spectrum width.** Convert it from m/s to kt like velocity.
- **A4, station switch.** Add `shutdown()`:
  - a closed flag checked by every worker;
  - `_emit` that drops signals after close;
  - executor shutdown and clearing the parsed and decoded caches;
  - removing the temp folder off the GUI thread.

  In `MainWindowArchiveMixin._start_archive_radar`, call `shutdown()` on the old fetcher before making the new one. Today the old one lives on with its parsed volume (about 0.5 GB).
- **A5, seeks:**
  - track the scan the clock wants (`_target_scan`) and cancel queued downloads and decodes for other scans (`_drop_stale_work`; `_fetch_lock` becomes an `RLock`, since a cancel runs the done-callback in the cancelling thread);
  - fetch neighbors only after the wanted scan's file is in;
  - evict decoded scans by time window, not only alongside their raw file.
- Keep this repo's own memory work: lazy gate geometry, `_parse_lock`, the parsed volume kept for the displayed scan only, and `_index_pos`. Add the fork's idle release of the kept parse (45 s) if it fits cleanly.
- Leave the fork's `az_offset` change out: this repo maps rows by real azimuth (`_az_row_table`), which already fixes the rotated-scan bug.
- Leave velocity options out (session 10).
- Use `endpoints.NEXRAD_LEVEL2_BUCKET`; add it to `data/endpoints.py` if phase 1 didn't.

Tests: port the relevant parts of the fork's `tests/test_radar_archive_fetcher.py`. Cover the `.gz` key and gzip bytes, split-cut tilt choice, spectrum-width units, cancelling stale work, and no signals after `shutdown()`.
**Status:** done 2026-11-11 (uncommitted; full suite 472 passed). Changes from the fork's version:
- Kept this repo's structure: `_pool`, `_index_pos`, `_parse_lock` around parse and decode (no separate `_decode_lock`), and the repo's `Level2RadarScan` arguments. The 45 s idle release of the kept parse was added.
- `_parse_s3_listing` returns `{time: key}`, and `_fetch_index` stores the keys under `_index_lock`. The fork's two-day index (session 5) and its `package_sources` / `provenance` / `perf_log` hooks (session 12) were left out.
- Decode keys stay 3-tuples (no `VelocityOptions` until session 10). `_decode` takes `product` / `tilt_idx`, so a product change during a parse can't mislabel the result.
- `shutdown()` doesn't take `_parse_lock`, because a running parse would block the GUI thread during a station switch.
- **Fixed a bug the fork also has:** `Level2RadarScan.available_tilts` was sorted, so sweep indices didn't match the volume once SAILS rescans appear. It now stays in volume order. `RadarControls.set_archive_tilts` lists each angle once with its sweep index as item data, and `current_tilt_index()` / `select_tilt()` were added. `_on_archive_radar_scan` follows `scan.tilt_index`.
- Unverified: not run against real AWS data in the app (a pre-2016 date such as KTLX 2013-05-31 and a split-cut VCP); the fork's real-volume tilt test needs its benchmark file, so it wasn't ported.

## Session 2: Hazard and sounding fixes (A6–A8)

Files: `archive/fetchers/hazard_archive_fetcher.py`, `archive/fetchers/sounding_archive_fetcher.py`.

- **A6, SPC outlooks before 2020.** When no day 1/2 `.lyr.geojson` product is found, try the cycle's `-shp.zip`. Read its `.prj` with pyproj (Lambert Conformal → lon/lat). Simplify the rings (0.05°, shapely). Skip DN=0 placeholder polygons on `sig*` layers. Map categorical DN 2–6 to MRGL…HIGH, and normalize through `_normalize_archive_spc_geojson`. Fork functions: `_archive_spc_shapefile_zip_url`, `_shapefile_lonlat_transform`, `_simplify_outlook_geometry`, `_parse_outlook_shapefile_zip`, and `_parse_shp(transform=)`.
- **A7, throttled MD replies.** When the IEM reply isn't a zip (doesn't start with `PK`), log it and return without caching, so the next clock change asks again.
- **A8, archive soundings:**
  - drop levels with non-finite values (`np.isfinite`);
  - label the Open-Meteo profile as what it is, `source="hrrr"` with label `HRRR HH:MM UTC`, not `ruc_rap` / "Archive Analysis";
  - round its valid time to the hour.

  The fork's provider chain (THREDDS sonde, API, TROPoe) belongs to session 18.

Tests: port from the fork's `tests/test_hazard_archive_fetcher.py`, with a small synthetic shapefile zip and a non-zip MD reply; add a sounding NaN-level test.
**Status:** done 2026-11-11 (uncommitted; full suite 491 passed). Changes from the fork's version:
- `hazard_archive_fetcher.py` is the fork's file with plain `requests.get` in place of `package_sources.requests_get` (session 12). Its URLs and the sounding fetcher's now come from `data/endpoints.py`: `IEM_STORM_BASED_WARNINGS`, `IEM_SPC_WATCHES`, `IEM_SPC_MD_GIS`, `SPC_OUTLOOK_ARCHIVE` and `SPC_EXTENDED_OUTLOOK_ARCHIVE` were added, with check entries in the endpoint list.
- **A8 was replaced by removing the archive model sounding** (the user's call). Open-Meteo's historical HRRR returned all-null values for 2019-06-08 and timed out at 150 s for 2024-05-17; it looks like HRRR was dropped there. So:
  - `ArchiveSoundingFetcher` keeps only OBS (IEM RAOB) and NSSL. `fetch_model_sounding`, the Open-Meteo parser, and the dead `_parse_iem_raob` / `_nearest_synoptic_time` are gone;
  - in ARCHIVE the sounding drawer hides HRRR and starts on OBS (`SoundingControls.set_hrrr_available(False)`, `reset_to_hrrr` → `reset_mode`). The right-click menu has no HRRR item, and the sounding dialog offers no HRRR comparison pill (it would have fetched today's live HRRR);
  - another archive model source can come later. `endpoints.OPEN_METEO_HISTORICAL` stays, because the live `SoundingFetcher` still uses it.
- The fork's provider chain, CopterSonde signal and `provenance` were left out (sessions 18 and 12).
- Tests: the fork's `tests/test_hazard_archive_fetcher.py`, plus an end-to-end pre-2020 fallback (geojson 404 → zip), no zip request when geojson exists, and the non-zip MD reply. `tests/test_archive_sounding_fetcher.py` covers the HRRR-less drawer and comparison pills; there's also an archive case in `tests/test_main_window_context_menu.py`. `_on_archive_sounding_map_click` was removed from the method snapshot.
- Checked live: `day1otlk_20190608_1300-shp.zip` parses to 9 categorical, 4 wind, 3 hail, 2 tornado and 1 sig-hail polygons, all within CONUS; the DN=0 sigwind / sigtorn placeholders are dropped. Not looked at on the map in the app.
- Seen in passing, not fixed: in ARCHIVE the OBS and NSSL comparison pills still use the live comparison fetchers, so they fetch the latest sounding, not the one at the archive time.

## Session 3: Replay, radar auto-pick, safe close (A9, A10, A12)

Files:
- `archive/fetchers/mqtt_reader.py`;
- `ui/app/main_window_archive.py`;
- `ui/app/main_window_radar.py` (`_try_auto_select_radar_station`);
- `ui/app/main_window.py` (close).

- **A9, replay.** Replace the emitted-id sets with `items_at(topic, t)`: each item's latest version as of `t`, removed when deleted or past `expires_at`. Add `_sync_items`, which removes what's gone and re-sends what changed. A rewind no longer deletes and re-adds everything. Edits now show, and cones expire after their hour.
- **MQTT history from THREDDS (needed: the API is down).** Add the fork's `_fetch_topic_text`: THREDDS (`FOFS/STORM/annotations`, uppercase, per session 0) first, then the API, all topics fetched concurrently. Send the API key only to the API. Use STORM's SSL context and a browser-like User-Agent for THREDDS.
- **A10, radar auto-pick.** `vehicle_positions_near(session start)` replaces `first_vehicle_positions()` (MQTT reader and `vehicle_obs_archive_fetcher.py`).
- **A12, safe close:**
  - add `_later(msec, callback)`, a single-shot timer parented to the window that skips its callback once closing has begun, and use it for the archive startup polls (`_check_mqtt_loaded` and the rest);
  - connect worker signals to bound methods, not lambdas capturing the window (the radar `loading_changed` lambda, the satellite error lambdas);
  - in `closeEvent`, set `_closing` and disconnect every archive worker (`_silence_archive_workers`), then shut the radar fetcher down.

Tests: port `items_at` and expiry tests from the fork's `tests/test_archive_mqtt_reader.py`. Add a `_later` test (no callback after close) and an auto-pick test.
**Status:** done 2026-11-11 (uncommitted; full suite 513 passed). Changes from the fork's version:
- `mqtt_reader.py` took A9 (`items_at`, `_expired`, `_sync_items`, `_shown`), `_fetch_topic_text` and concurrent topic fetches. Left out:
  - the next-UTC-morning files, `session_bounds`, `activity_times` and the `loaded` signal (session 5);
  - `package_sources` (session 12).
- `FOFS_ANNOTATIONS = "FOFS/STORM/annotations"` went into this repo's `data/thredds_paths.py` (and `all_folders()`), not a new `archive/thredds_paths.py`.
- Both hosts use `config.NSSL_SSL_CONTEXT` (verified). THREDDS accepted it as well as the unverified `_ssl_context()`, checked live. Only the API gets `X-API-Key`. Only an `HTTPError` 404 counts as a clean miss; other errors fall through to the API.
- A10: `vehicle_positions_near(self._archive_time)` in both readers. The one-second fetcher's version bisects `_timestamps` instead of scanning every fix. Auto-pick still reads only MQTT; the fork's wait for dense observations is session 4.
- A12:
  - `_later` and `_silence_archive_workers` live in `MainWindowArchiveMixin`;
  - every archive startup `QTimer.singleShot` and the 45 s radar-index timeout now go through `_later`;
  - the radar `loading_changed`, satellite loading error, sounding error and one-second error lambdas became bound methods;
  - `closeEvent` sets `_closing`, silences archive workers (archive only) and calls `_archive_radar.shutdown()`;
  - the loading-dialog lambdas that capture `loading` (not the window) were left, since blocked worker signals cover them at close.
- Tests:
  - the fork's `tests/test_archive_mqtt_reader.py`, plus: no API key to THREDDS, the uppercase folder, a rewind re-sends nothing unchanged, and auto-pick near the session start;
  - new `tests/test_archive_safe_close.py`: `_later` before and after close, silencing archive workers only, the window's auto-pick, and the one-second `vehicle_positions_near`.
- Checked live: 2026-05-17 loads from THREDDS in 0.9 s (13,378 vehicle, 4 annotation, 13 cone, 13 drawing records; no scan sectors that day). At 22:15Z: 1 annotation, 1 cone, 5 drawings. Auto-pick near 20Z found hailcam in Nebraska.
- Not verified: closing the app during archive loading, and replay on the map in the running app. The real data has one delete with no `deleted_at` (cones, 2026-03-29); `_parse_jsonl` drops untimed records, so that cone stays until it expires.

## Session 4: One-second FOFS, archive tools without admin (A11, B17)

Files:
- `archive/fetchers/vehicle_obs_archive_fetcher.py`;
- new `archive/fofs_index.py` and `archive/fofs_known_corrections.py`;
- `archive/vehicle_aliases.py`;
- `ui/app/main_window_archive.py`.

This is the largest fix (about 1,300 lines in the fork). Port in this order, each step tested:
1. Value checks: drop -999 / 999 sentinels and impossible readings, a dewpoint above the temperature (beyond sensor slack), and dewpoint columns that are really RH.
2. Positions: null-island and placeholder fixes, same-second duplicates, short GPS jump-and-return glitches, and frozen fixes beside a jump.
3. Dates: resolve a whole file's `gps_date` encoding by hypothesis (plain DDMMYY, the conversion's garbled form, day/month-swapped file names), midnight rollover, misfiled duplicates, and the known-corrections table.
4. Discovery: `fofs_index.py` lists which vehicles have files for the day (with `KNOWN_FOFS_PLATFORMS` as fallback), so dates with no MQTT history still get one-second data. Concurrent downloads with retries on timeouts / 5xx only, never on 404 / 410, and a browser-like User-Agent.

**B17:** remove the `admin_mode` gate from `_start_archive_vehicle_obs` and the OBS status text. Port the fork's `tests/test_archive_features_not_admin.py`. Admin keeps turning on only the live mesoanalysis overlays.

Tests: port the fork's `tests/test_archive_vehicle_obs_fetcher.py` and `tests/test_fofs_index.py`.
**Status:** done 2026-11-11 (uncommitted; full suite 609 passed). Changes from the fork's version:
- `vehicle_obs_archive_fetcher.py` took all four steps (value checks, position cleanup, file dating with the known-corrections table, index discovery with retries). Left out:
  - the next-morning rows: `_fetch_vehicle` keeps only the session's UTC day, and `session_bounds` / `activity_times` aren't used (session 5 adds the next day to `days` and `available_vehicles`);
  - `package_sources` and `provenance` (session 12);
  - the lidar truck's track cache (`remember_dltruck_track`, `load_dltruck_track`, `TruckFix`; session 19);
  - `compass_dir` → `heading_deg`, since this repo's `Observation` has no heading field. Add both when trails or lidar need it.
- Kept this repo's `vehicle_positions_near` (bisects `_timestamps`), not the fork's scan of every fix.
- Requests use `config.NSSL_SSL_CONTEXT` (verified TLS), not the fork's unverified context. Checked live.
- `KNOWN_FOFS_PLATFORMS` (in `archive/vehicle_aliases.py`) is also the roster probed when there is no index, as well as naming vehicles in odd folder names. In the fork it was only the second.
- `fofs_index.py` reads paths from `data/thredds_paths.py` (`FOFS_MESONET` added) and fetches catalogs directly, with no `package_sources`. Its cache is `~/.cache/storm/fofs_index.json`.
- `fofs_known_corrections.py` copied unchanged, apart from its docstring pointing at the fork, not the fork's planning notes.
- B17: `_start_archive_vehicle_obs` has no admin gate, and it also starts when MQTT has no vehicles. Status reads "OBS: probing", then "OBS: 1-second (n)" or "OBS: catalog (n)" (or "partial n/m"), "OBS: MQTT fallback" or "OBS: no data for this date". The fork's feature-flag test doesn't apply, since this repo has none of those archive flags. `tests/test_archive_features_not_admin.py` instead covers starting without admin and with no MQTT history, the status text, and that only mesoanalysis is admin-only.
- The fork's auto-pick wait came along (from session 3's note). When MQTT has no positions, `_try_auto_select_radar_station` waits for the one-second load. After `RADAR_WAIT_FOR_VEHICLES_MS` (10 s, through `_later`) it starts radar from home, then moves once vehicles arrive, unless the user picked a station meanwhile. New methods: `_radar_wait_for_vehicles_timed_out`, `_finish_radar_station_selection`, `_apply_radar_station`.
- Tests:
  - the fork's `tests/test_archive_vehicle_obs_fetcher.py`, minus the lidar truck and `PositionTrack` tests. The next-morning test became a UTC-day test. Added: roster merge, roster without an index, a date with no MQTT history, retrying a timeout but not a 404, and the User-Agent;
  - the fork's `tests/test_fofs_index.py`;
  - new `tests/conftest.py`, which stubs the index out for every test so none reach THREDDS;
  - four auto-pick wait tests in `tests/test_archive_safe_close.py`.
- Checked live: the index crawl found 3,267 files for 16 vehicles in 4.3 s. 2024-04-27 loaded dltruck, probe1 and probe2 (25–33k fixes each). 2019-06-01, a date with no MQTT history, loaded farfield, dltruck, probe1 and probe2 from an empty roster. Each day took 1.6 s.
- Not verified: the app itself (vehicles on the map, the OBS label, the radar wait), and the no-index fallback against real THREDDS.

## Session 5: Sessions past 00Z (B14)

Files:
- `archive/session.py`, `archive/time_controller.py`;
- the radar, MQTT, vehicle-obs, satellite and hazard archive fetchers;
- `ui/app/main_window_archive.py`.

- `session_bounds`: from 00Z of the session day to 06Z the next day. `activity_end` trims that to the day's last activity plus 30 min. Activity is the last driving time (`last_moving_time`; a parked rack doesn't count) or the last annotation / cone / drawing / sector change. Only activity from 12Z counts; if there's none, the session keeps its full UTC day.
- `TimeController`:
  - a window (`window`, `set_window_end`, `window_changed`) and clamping;
  - `seconds_since_start` / `set_seconds_since_start` replace the midnight-based pair;
  - pause at the end of the window.
- Fetchers read the next day too:
  - the radar index lists both days' S3 prefixes, clipped to the window;
  - the MQTT reader and FOFS read the next date's files;
  - satellite lists the next morning's hours;
  - hazards already work by time.
- Once vehicles and MQTT have loaded, the window calls `set_window_end(activity_end(...))`.

Tests: port the fork's `tests/test_archive_session_window.py`, plus window tests for the radar index and time controller.
**Status:** done 2026-11-11 (uncommitted; full suite 631 passed). Changes from the fork's version:
- `archive/session.py` gained `session_bounds`, `last_moving_time` and `activity_end` as in the fork, plus a small `session_days` (the two UTC midnights to read). `ArchiveSession` keeps `radar_station` and `date_compact`, which the fork dropped but this repo still uses.
- `TimeController`: `window`, `set_window_end`, `window_changed`, clamping, `window_seconds` and `seconds_since_start` / `set_seconds_since_start` (the midnight pair is gone; nothing called it). Playback pauses at the window's end, also when already sitting there. `step_forward` / `step_backward` / `STEP_SECONDS` kept. No `perf_log` events (session 12 territory).
- Radar index: both days' S3 prefixes, clipped to `session_bounds`, keeping this repo's `{time: key}` listing and `_index_pos`. `next_scan_after` is left for session 6.
- MQTT reader: reads the next date's files alongside the session day's, all topics at once (10 requests). Unlike the fork, the session day's file isn't clipped to 00Z, so records before it (e.g. an annotation still standing) stay. The next day is clipped to the cap, and its failures only log. Added the `loaded` signal and `activity_times`. The startup poll still drives the mixin; session 6 replaces the polls.
- One-second FOFS: `days` includes the next day (rows up to the 06Z cap), `available_vehicles` checks both days' candidate files, and `activity_times` was added.
- Satellite lists every hour from 00Z to the 06Z cap (30 listings), crossing into a new year where needed. Hazards needed nothing.
- `MainWindowArchiveMixin._update_archive_session_end` sizes the window once MQTT has loaded, and waits for the one-second tracks when they're loading. With no activity from 12Z on, the window is the full UTC day.
- Tests: the fork's `tests/test_archive_session_window.py`, plus pausing at the end, clamped jumps, the two-day radar index, the MQTT next morning (clipping, a failed next-day file, activity), satellite hours across a new year, and the mixin's wait and its fallback to the full day. The vehicle UTC-day test became the fork's next-morning test, with an `activity_times` test. Two test hosts gained `_update_archive_session_end`.
- Checked live: KOAX on 2024-04-27 indexed 304 scans, from 00:04Z to 05:56Z on the 28th (79 from the next morning).
- Not verified: the app itself (playing past 00Z, the window shrinking after load), the MQTT and FOFS next-day reads against real THREDDS, and the satellite listing live.

## Session 6: Archive time slider, non-blocking loading (B13, B15)

Files:
- `ui/controls/archive_controls.py`;
- new `ui/widgets/archive_loading_indicator.py`;
- `ui/dialogs/archive_loading_dialog.py` (removed);
- `ui/app/main_window_archive.py`.

- **B13.** Add a slider over the session window to the archive bar:
  - hourly ticks and labels;
  - radar scan ticks (`set_available_scan_times`) and coverage spans (`set_coverage`, used later by lidar);
  - preview while dragging, load on release;
  - previous / next radar scan buttons (`next_scan_after` on the radar fetcher).

  Keep this repo's bar: the step buttons and precision mode, speed, JUMP, the status labels and shortcuts. Style it with `P.*` tokens.
- **B15.** Replace the modal loading dialog with the fork's small indicator. It shows the tasks still running and stays out of the way of the map, and the map is usable while data loads. The startup polls become indicator updates. After a seek, the wanted radar scan loads first (session 1 did the fetcher side).
- Check the layout in the overlay code (`_layout_overlays`) at narrow and wide window sizes.

Tests: port from the fork's `tests/test_archive_controls.py` and `tests/test_archive_loading_indicator.py`.
**Status:** done 2026-11-11 (uncommitted; full suite 650 passed). Changes from the fork's version:
- B13: this repo's bar keeps its rows, buttons, tooltips and keys. The slider (`archiveTimeSlider`, the existing `#archiveControls QSlider` theme), the coverage strip and the hour axis sit between the header and the buttons. Hour axis and strip are drawn with `P.*` tokens, and the groove inset is 6 px to match the theme's 12 px handle (the fork assumed 8). Unlike the fork, the axis also draws the radar scans as short ticks along its top. Following the clock doesn't move the handle while it's being dragged.
- Previous / next radar scan are two new buttons (⏮ ⏭) after the step buttons, disabled until the station's index arrives and cleared when the station changes. The fork replaced the ±10 s buttons with them; here ±10 s / ±1 s, precision mode and the Left / `[` / A keys are unchanged. The fork's `,` `.` `<` `>` scan keys aren't ported (they'd belong in `ui/shortcuts.py`). The buttons use the bar's own index list (`set_available_scan_times`, wired to the radar fetcher's `index_loaded`); `ArchiveRadarFetcher.next_scan_after` was added as well, for session 7.
- B15: `ArchiveLoadingIndicator` is the fork's, restyled as a small pill (`P.*` tokens) and with lower-case task names ("Loading vehicle tracks…"), in the order they tend to finish: hazards, satellite, vehicle tracks, radar. It sits top-left, or under the toolbar's left end when the toolbar is too wide to leave room. The dialog's `accepted` re-seek became `all_done` → `_renudge_archive_clock`. The dialog's Abort (and the never-connected `session_aborted` signal) are gone; closing the window is the way out (session 3's safe close). `begin` / `end` for later background work are ported for the lidar sessions.
- The startup polls still run (`_later` loops), but they now only update the indicator, so the map is usable meanwhile. Session 1 already loads the wanted scan first after a seek.
- Also fixed: the global `QComboBox { min-width: 120px }` stretched the bar's 54 px speed box to 134 px; `#archiveControls QComboBox` now sets its own minimum.
- Tests: `tests/test_archive_controls.py` (scan buttons with and without an index, precision mode, slider follow / preview / release, window resize, coverage strip, painting at 360 / 680 / 1400 px), `tests/test_archive_loading_indicator.py` (the fork's cases), and `next_scan_after` in `tests/test_radar_archive_fetcher.py`.
- Checked offscreen: the bar rendered at 680 and 400 px with a 29.5 h window, scan ticks and a coverage span. Not verified: the app itself (the indicator's placement next to the toolbar at narrow and wide windows, dragging the slider on a live session).

## Session 7: Sharp radar when paused (B16)

Files: `ui/map/radar_overlay.py`, new `ui/app/radar_superres_cache.py`, `ui/app/main_window_archive.py`.

- After the clock has been still for 500 ms, render the frame on screen at 4096 px over a square holding every gate out to 460 km, the far edge of reflectivity (`superres_crop_radius_m`, `render_scan_to_png(..., crop_radius_m=)`). Step down to 3072 / 2048 px when a render takes more than 2.5 s of CPU. Skip it while playing when the next scan would arrive first (`_sharp_render_would_be_stale`). Re-render on pause. Cache the 12 most recent sharp renders (LRU).
- Changes from the fork:
  - the memory guard checks available system memory (`mem_probe.system_memory_mb`) before each sharp render and skips that render when it's low. It doesn't compare peak RSS with a hard-coded 6 GB, and it doesn't switch off for the rest of the session;
  - the pixel-index cache counts entries by size: one 4096 px index is about 67 MB, so keep at most one sharp-size index beside the normal ones;
  - the crop radius is part of the index key.
- Live mode's adaptive grid is untouched.

Tests: port from the fork's `tests/test_radar_archive_superres.py`; add the memory-guard and cache-size tests.
**Status:** done 2026-11-11 (uncommitted; full suite 681 passed). Changes from the fork's version:
- `render_scan_to_png(..., crop_radius_m=)` goes through this repo's pixel index, not the fork's per-render sampler. The index key holds the crop radius, and a cropped index leaves out the scan's reach, so reflectivity and velocity share one. Cropped renders skip the normal frame cache (they have `RadarSuperresCache`, 12 entries, the fork's).
- Index cache: at most one index of 2048 px or more (`_SHARP_INDEX_CACHE_MAX`) beside the 4 normal ones.
- Strips: both the index build and the render work in strips of `RENDER_STRIP_PIXELS` (2¹⁷, 32 rows at 4096 px), live renders included (same image, tested). The fork used 256 rows. Measured on this M2 with a full 720 × 1832 sweep, 256-row strips left the process ~550 MB bigger (macOS keeps the freed mid-size blocks) and 32 rows were no slower. Peak RSS over 4096 / 3072 / 2048 renders went from 1021 MB to 446 MB.
- Timing: building the 4096 px index takes ~5 s of CPU (pyproj's WGS84 inverse on 16.7 M pixels); after that a render takes 0.8 s (the fork's ~1.8 s every time). The worker builds the index first (`ensure_pixel_index`) and times only the render, so the step-down to 3072 / 2048 px doesn't fire because of the one-off build. The first pause per station therefore waits ~6 s for the sharp frame, longer on a slow machine.
- Memory guard: `sharp_render_memory_ok()` checks `mem_probe.system_memory_mb` before each sharp render and skips it below 1024 MB free; the next pause tries again. **On macOS `system_memory_mb` has no "available" figure, so the guard never skips there**; it works on Windows and Linux.
- A sharp render that finishes after the clock moved on is still cached (the fork dropped it), so scrubbing back shows it at once. Pausing restarts the 500 ms wait (`playing_changed`). The fork's `_radar_settled_scan` isn't ported (nothing here reads it).
- Closing the window now also shuts down both archive render pools (the existing one wasn't).
- Tests: `tests/test_radar_archive_superres.py` (crop bounds, frame cache bypass, strips vs one pass with and without crop and mask, index key and size limits, step-down, memory guard, the cache's LRU, and the window's timer / stale-while-playing / pause / ready / step-down paths with stubs). Not ported: the fork's parse-cache tests (session 1's ground).
- Not verified: the app itself (pausing a live archive session and seeing the sharp frame replace the normal one, playback at 120× skipping sharp renders).

## Session 8: Storm tracks, model and map editing (D25)

Files:
- new `core/storm_track.py`, `core/storm_motion.py` and `ui/controls/track_controls.py`;
- the TRACK drawer;
- `ui/map/map_template.html` and `ui/map/bridge.py`;
- a new `ui/app/main_window_track.py` mixin.

- One point per radar frame: place, drag, delete, a reference marker, and undo / redo. Fork tests: `tests/test_storm_track.py` and `tests/test_storm_track_editing.py`.
- The TRACK drawer goes through `DrawerManager` and the shortcuts table, like the other drawers.
- Archive only.

**Status:** done 2026-11-11 (uncommitted; full suite 725 passed). Changes from the fork's version:
- The edit model is a Qt-free `StormTrack` class in `core/storm_track.py` (points, selection, undo / redo, `place` / `move` / `delete` / `clear`, the map's GeoJSON), so `ui/app/main_window_track.py` stays thin. The fork kept all of it as MainWindow state. `TrackPoint` is the fork's, without the file readers and writers (session 9's CSV).
- `core/storm_motion.py` is the fork's file unchanged, with its tests (`tests/test_storm_motion.py`). The drawer already shows mean and current motion, so session 9 only has the table, files and the sounding viewer's motion left.
- TRACK is a click mode (`ui/click_mode.py`): opening the drawer enters it and shows the usual banner with the gestures; Esc or another tool leaves it and closes the drawer. It goes through `DrawerManager`, the overlay stack and the session-restore skip list like SOUNDING, and has a toolbar icon and a palette entry.
- Right-click while editing uses this repo's Qt menu (`map_context_menu.build_track_menu`), not the fork's page menu: add / move / rename / remove the marker, select / delete the point under the cursor.
- Keys from the shortcuts table, not ad hoc QShortcuts: T opens TRACK, P adds a point at the marker, Delete / Backspace delete the selected point, Ctrl+Z / Ctrl+Shift+Z undo / redo. The fork's A and D were taken over from frame stepping while TRACK was open; here A / D keep stepping (no key is bound twice), so the marker key is P and D doesn't delete. The editing keys and the drawer's buttons do nothing while TRACK is closed.
- Left out for session 9: Load / Reset / Export / Table / Open folder, workspaces, autosave and the save notice. **Until session 9 the track exists only for the session; closing the window loses it.** Also left out: the R/V product flip (the fork's `_toggle_radar_product_shortcut`; R and V are layer keys here) and the fork's red edit banner in the page (the click-mode banner replaces it).
- The hover label reuses the page's hazard tooltip (one line: time │ point n of m │ radar).
- Tests: `tests/test_storm_track.py` (the model, rewritten for `StormTrack`), `tests/test_storm_track_editing.py` (the fork's editing tests that don't need files, on the mixin with stubs: place, move at the same frame, drag retime / Alt, delete / undo / redo, edits ignored while closed, clear, marker, rename, right-click menu, motion readout, the current point following the clock, visibility and Keep on map), plus track cases in the context-menu, map-widget, shortcut and palette tests.
- Not verified: the app itself (clicking, dragging and right-clicking points on a live archive session, the hover label, snapping to the marker).

## Session 9: Storm tracks, table, files, motion (D25)

Files: new `ui/dialogs/track_table_dialog.py`, a simplified `core/workspace.py`, `ui/sounding/dialog.py`.

- A table of exact times and positions (fork test `tests/test_track_table.py`). Storm motion from the track came in session 8 (`core/storm_motion.py`, drawer readout).
- Tracks are saved as CSV in MESO-VIEW's columns, one file per case date in a local `storm_tracks/` folder, git-ignored. TRACK opens the date's saved track. There are no named workspaces, no Excel, and no shipped tracks.
- The sounding viewer shows the observed storm motion (fork test `tests/test_sounding_observed_motion.py`).

**Status:** done 2026-11-11 (uncommitted; full suite 746 passed). Changes from the fork's version:
- Files: `core/workspace.py` is down to `storm_tracks/storm_YYYYMMDD_track.csv` (next to main.py, git-ignored), keyed by the archive session's UTC date, plus `atomic_write_text` and `unused_path`. No workspaces, manifest, shared tracks, Excel or first-point-time file names. `core/storm_track.py` gains `read_track_csv` / `write_track_csv` with the stdlib `csv` module (the fork used pandas): the fork's columns (point_id, time, lat, lon first, as MESO-VIEW writes them), MESO-VIEW's aliases (datetime / latitude / …), other columns ignored.
- Every edit (place, move, delete, clear, undo / redo, table, import) autosaves. The archive startup opens the date's file (`_open_session_track`, from `_begin_archive_startup`) without an undo step, so the motion is there before TRACK is opened; the track stays hidden until TRACK or Keep on map. A file that can't be read is left alone and edits save beside it (`_2.csv`). The first save says where in the status bar; the drawer shows "Saved to storm_tracks/…". The fork's modal "track saved" notice isn't ported.
- Drawer: TABLE, IMPORT (replace the track with any CSV, undoable), EXPORT (a copy elsewhere), FOLDER. No Load / Reset to Original (undo covers it within a session).
- Table: `ui/dialogs/track_table_dialog.py` is the fork's, restyled with `ui.palette` tokens and with tooltips. It follows map edits, undo and the map's selection; double-click pauses and moves the clock; closing TRACK hides it.
- Sounding viewer: the fork's OBS marker on the hodograph and the "SRH obs" column (0–500 m, 0–1 km, 0–3 km), fed by the track's mean motion when a sounding loads and whenever the track changes. Bunkers values unchanged.
- Tests: `tests/test_track_table.py` and `tests/test_sounding_observed_motion.py` (the fork's, unchanged), new `tests/test_storm_track_files.py` (round trip, MESO-VIEW columns and bad rows, missing columns, atomic write, naming), and file / table / import / jump / sounding cases added to `tests/test_storm_track_editing.py` (its host is now a QWidget so the table can parent to it).
- Not verified: the app itself (the drawer's width with the four new buttons, the table on a live session, the OBS marker drawn on a real hodograph, reopening a case and seeing its track come back).

## Session 10: Velocity dealiasing (D26)

Files:
- `archive/fetchers/radar_archive_fetcher.py`;
- new `core/velocity_processing.py`;
- `ui/controls/radar_controls.py`.

- Confirm the dealiasing approach with the user first (see Decisions).
- Port `VelocityOptions` (dealias only; no storm motion), make it part of the decode cache key, and add `set_velocity_options`.
- `sweep_nyquist` reads the Nyquist velocity from the sweep. With none recorded, or if dealiasing fails, show raw velocity and say so: a raw field is never labeled as dealiased.
- Add a DEALIAS toggle in the radar drawer (archive, velocity only), with a status tag on the archive bar.
- Write the `scipy.ndimage` region-based unfolding and test it on synthetic folded fields (a known wind field folded at a known Nyquist must come back). Compare it with Py-ART offline, in the `dissertation` env.
- Port the fork's `tests/test_velocity_processing.py`, minus storm-relative.

**Status:** done 2026-11-11 (uncommitted; full suite 767 passed). The user confirmed `scipy.ndimage` over Py-ART. Changes from the fork's version:
- `core/velocity_processing.py`: `dealias(vel_ms, nyquist_ms)` is Py-ART's region-based method rewritten with `scipy.ndimage` and `heapq`, using Py-ART's defaults (3 bands, gaps of up to 100 gates along and between rays bridged, centered, rays wrap across north). It doesn't need the azimuths, ranges or elevation the fork passed to build a Py-ART radar object. `sweep_nyquist` is the fork's. No storm-relative.
- Checked against Py-ART 2.1.1 (`dissertation` env, nothing shipped) on 16 sweeps from KFDR 2019-05-20 21:04Z, KICT 2024-05-06 02:04Z, KTLX 2013-05-20 20:03Z and 2013-05-31 23:14Z (up to 20% of gates unfolded): 14 sweeps agree on ≥ 99.98% of gates, the other two on 98.2% and 98.9%. There the differences are small isolated echo patches far out (95–300 km), and both results are equally smooth (993 vs 996, and 74 vs 73 neighboring gates more than Nyquist apart), so it's a guess either way. It runs 0.02–0.16 s a sweep against Py-ART's 0.07–1.3 s.
- Fetcher: `VelocityOptions(dealias=False)` is the 4th part of the decode key, `None` unless velocity is up with dealiasing on, so reflectivity and raw velocity keep their cache entries. `set_velocity_options` redisplays only when velocity is on screen. Every decoded scan has `velocity_processing`: "" (nothing asked), "dealiased (region-based, Nyquist n m/s)", "raw: no Nyquist velocity recorded, not dealiased" or "raw: dealiasing failed". The sharp-render cache already had it in its key (session 7).
- Drawer: a checkable DEALIAS button after the tilt picker (`dealias_toggled`), shown only in archive mode with velocity selected, and saved with the session's radar state. The fork used a checkbox row under the drawer; a button in the existing row keeps the drawer's height. Archive bar: "Radar: velocity 0.5deg 21:04Z · dealiased" or "· not dealiased"; a new fetcher (station switch) gets the drawer's choice.
- Tests: `tests/test_velocity_processing.py` (the fork's fold and Nyquist tests, plus a noisy, gappy full-size sweep with a double fold, an unaliased field, a fold next to north, bad input), dealias cases in `tests/test_radar_archive_fetcher.py` (key, redisplay, label and values when dealiased, no Nyquist, failure, raw), and `tests/test_archive_dealias.py` (button visibility, signal and saved state, the status tag, the window's wiring).
- Dealiased speeds above 75 kt sit at the end of the velocity color scale (±75 kt, unchanged).
- Not verified: the app itself (the button appearing on switching to velocity, the frame redrawing dealiased, the archive bar tag).

## Session 11: Observation trails (D27)

Files:
- new `core/derived.py`, `core/trails.py` and `ui/controls/trail_controls.py`;
- the map trail layer;
- a mixin method for refreshing.

- Each platform's recent path, colored by a measured or derived quantity (θ, θv, θe, θw, mixing ratio, u/v, radial and tangential wind). Optional wind barbs. Storm-relative barbs and time-to-space use the session 9 motion. Rebuild at most every 250 ms while playing.
- Use the fork's violet and diverging scales, which avoid the road colors.
- Fork tests: `tests/test_derived_and_trails.py`.

**Status:** done 2026-11-11 (uncommitted; full suite 793 passed). Changes from the fork's version:
- `core/derived.py` and `core/trails.py` are the fork's, unchanged (MetPy and matplotlib were already shipped). Its colors aren't the violet / diverging scales this plan named: the fork's final version uses viridis-family and ColorBrewer maps (plasma, viridis, RdYlBu_r, cividis, magma, RdBu_r), trimmed so every color keeps 3:1 contrast with the map, banded at round values, with a dark casing under the trails so they stand apart from roads whatever their hue. Kept as the fork has it; its contrast test passes.
- Map: the fork's `obs-trails` source (rings, casing, line, storm center, barb icons drawn on a canvas, barb layer) and the hover readouts, added under the storm track. `stormSetTrails` keeps the data and colors on `window`. The unused `.storm-hover-tooltip` popup style gained `.title` / `.muted` and no pointer events. `MapWidget.set_trails`.
- Controls: `ui/controls/trail_controls.py` is the bottom of the VEHICLES panel (archive only), as in the fork, but laid out for the panel's width and styled with `ui.palette`: the "show trails" switch, then (only while on) quantity and window, time-to-space / wind barbs / storm-relative, a "Storm-relative needs a track ▸" link (opens TRACK), the color bar with units, and an empty-state line. The fork's single wide floating-drawer row and its animation aren't used.
- Window: new `ui/app/main_window_trails.py` (`MainWindowTrailsMixin`). Sources are each vehicle's whole day: the one-second FOFS data, else the recorded MQTT history (new `observations_by_vehicle()` and `vehicle_histories()` on the two readers, instead of re-parsing MQTT on every tick). Rebuilds go through a 250 ms single-shot timer: the clock, settings, MQTT and one-second loads, and the track's motion (`_refresh_track_motion`) schedule one. Left out: CLAMPS surface (session 18) and ASOS in time-to-space (session 16; `no_station_pressure` is there for it), and the fork's Video Studio "trails pending" check (session 13).
- Trail settings aren't saved with the session.
- Tests: `tests/test_derived_and_trails.py` (the fork's, unchanged) and new `tests/test_archive_trails.py` (controls: off by default, track-gated options and the reset when the track goes, the link, scale and status; window: source preference, clearing / drawing / empty states, storm motion reaching controls and builder, time-to-space rings, coalesced rebuilds, the panel shrinking back, the readers' accessors, layer order).
- Checked offscreen: the controls in the panel's style at 340 px minimum (about 460 px wide with trails on, the quantity box elides long names). Not verified: the app itself (trails and barbs drawn on a live archive session, hover readouts, playback smoothness at high speed, the vehicle panel's placement with the extra rows).

## Session 12: Case packages and menu bar (D28, D31)

Files:
- new `core/package_sources.py`, `core/provenance.py` and `core/case_package.py`;
- new `ui/dialogs/case_export_dialog.py` and `ui/dialogs/task_progress_dialog.py`;
- every archive fetcher's download calls;
- the menu bar in `ui/app/main_window.py`.

- Every archive download goes through `package_sources` (an opened package's copy first, no network) and records provenance (URL, SHA-256).
- Export writes a package limited to a chosen UTC time frame: tracks, case settings, provenance and, optionally, the data, including scans the catalogs list for that frame. Open restores it offline. Packages go in `data/case_packages/<date>/`, git-ignored.
- Fix the fork's export bugs while porting:
  - a finished export cancelled itself;
  - the progress window stuck on "Canceling…";
  - opening took minutes after long sessions, because each scan re-checked the others.
- **Menu bar:**
  - File (open / export / show case packages, screenshot);
  - Edit (track undo / redo);
  - View (error log, debug panel);
  - Help (user guide, About).

  Archive-only entries are hidden in live modes, and every entry is added to the command palette.
- Fork tests: `tests/test_case_package.py`, `tests/test_case_export_dialog.py`, `tests/test_task_progress_dialog.py`. Port `scripts/verify_offline_case.py` as the manual check.

**Status:** done 2026-11-11 (uncommitted; full suite 856 passed). Changes from the fork's version:
- `core/case_package.py` is the fork's (its lru-cached file times and once-per-update time-frame check are the "minutes to open" fix), without NOXP. `build` also re-raises a cancel from `fetch` and checks Cancel once more after writing the index, so a package never appears after "Canceling…".
- `core/provenance.py`: recording is on only in ARCHIVE windows (`reset(active=…)`), since live modes share the sounding fetchers; kept small files are capped at 256 MB in total (hazard queries repeat on every clock step).
- `core/package_sources.py`: the package's ZIP is opened once at `activate`, not per file; `requests_get` passes `params` only when given.
- Fetchers routed through it: radar (index and volumes, package first), satellite (listings and frames), hazards (all six calls), MQTT history, one-second mesonet files and the FOFS catalog XML, and the archive soundings' downloads in `data/fetchers/obs_sounding_fetcher.py` and `clamps_sounding_fetcher.py` (plain downloads in live modes).
- Window: new `ui/app/main_window_case.py` (`MainWindowCaseMixin`): menu bar, export, open. Export runs `case_package.build` on a thread polled by a QTimer instead of the fork's `processEvents` busy loop; the progress window closes through `finish()`; the export's downloads read in 1 MB pieces with a 30 s socket timeout, so Cancel stops mid-file. Closing the window cancels a running export. The success notice is a status-bar line (no modal notice, as in session 9).
- Open: a package for another date isn't opened (a message says to open it from that date's session or with `--case`); session 15's change-day loop can reopen automatically. Same date: the package's track replaces the date's track as one undoable edit, asking first if the date already has a different one (one track per date here, no workspaces); radar station (taken by `_apply_radar_station`, skipping the wait for vehicles), product and tilt (on the station's first scan), dealias, clock; the packed files are served from then on.
- `main.py --case PACKAGE.zip` opens a package at launch, skipping the launch window, with its files served before any fetcher starts (the offline path; session 15 adds *Open case…* to the launch window).
- Menu bar: File (Open / Export / Show Case Packages Folder, ARCHIVE only; Save Map Screenshot), Edit (Undo / Redo Track Edit, hidden in live modes), View (Error Log, Debug Panel), Help (User Guide, About). Keys stay in `ui/shortcuts.py` (new `case_open` Ctrl+O, `case_export` Ctrl+Shift+E; screenshot stays Ctrl+S); `ShortcutRegistry.bind(..., menu_action)` puts a key on its menu entry instead of a second QShortcut, which Qt would treat as ambiguous. Unkeyed entries reach the palette through a "menu" provider. `QMenuBar` styled in `ui/theme.py` for Windows and Linux.
- Dialogs are the fork's, restyled with `P.*` tokens.
- `core/storm_track.py` gained `parse_track_csv` (a package's track bytes). Guide: a "Menu Bar and Case Packages" section and the new keys.
- Tests: the fork's three files (plus cancel-mid-download and cancel-after-index), new `tests/test_package_sources.py` (live modes record nothing, query strings, package-then-network, broken package, keep cap, threads, the API key only to the API, cancel mid-download, the radar fetcher served from a package and recording downloads) and `tests/test_case_window.py` (menus in both modes, keys on menu entries, palette, case settings, default frame, opening: other date, track / radar / clock / data, station switch, track replace prompt, launch package, no wait for vehicles; export: background run, cancel, close cancels, one at a time).
- Offline, a topic that had no file when the package was made (a 404 isn't recorded) logs a fetch error instead of "no file".
- Checked with `scripts/verify_offline_case.py` (2024-04-27, KFDR, a 20-minute frame; it has to run on the normal macOS platform: under `offscreen` QtWebEngine's GPU process crashed): built an 81 MB package (222 files: radar, satellite, mesonet, hazards), then reopened it in a process with Python's network blocked. Radar rendered from the package, observations loaded, and a seek within the frame showed the next scan (served: 150 satellite, 13 hazards, 7 radar, 9 mesonet). Expected misses offline: no MQTT history exists for that date (the API is down, session 0), and radar scans outside the frame. Seen but not caused here: the archive bar kept saying "Radar: loading KFDR…" while the prefetch failed on out-of-frame scans, and the satellite fetcher asked for meso frames at the window's end (05:59Z next day).
- Not verified: the menu bar in the running app (macOS system menu bar, the Windows / Linux row and its height next to the toolbar), the export and open dialogs by hand, and Cmd+Z reaching track undo through the menu entry while another STORM window (sounding, VAD) is in front.

## Session 13: Video Studio, model and export (D29)

Files: new `core/studio.py`, `ui/studio/capture.py`, `ui/studio/exporter.py`, `ui/studio/writer.py`.

- Keyframes:
  - case time;
  - view;
  - radar (station, product, tilt);
  - hold, length / speed and easing.
- Clock steps: every minute, every 5 minutes or each radar scan.
- Exact frame-by-frame export to MP4 (H.264 through Qt's `QMediaRecorder`; `PyQt6.QtMultimedia` is in the environment), GIF or PNG frames, with time, data-line and legend captions. Wait for the archive radar before capturing each frame.
- Fork tests: `tests/test_studio.py`, `tests/test_studio_writer.py`.

**Status:** done 2026-11-12 (uncommitted; full suite 893 passed). Changes from the fork's version:
- `core/studio.py` is the fork's (keyframes in movie time with case time, view, optional radar, hold and easing; ripple edits; clock steps smooth / 1 min / 5 min / radar scans; project JSON v2 with the v1 reader), minus a stray import.
- `ui/studio/writer.py` is the fork's (MP4 through `QMediaRecorder` + `QVideoFrameInput`, GIF through Pillow, PNG frames), with one fix: Cancel deleted the MP4 before the recorder had stopped, so the file could come back; it's now deleted once the recorder reports it stopped.
- `ui/studio/capture.py`: the fork's off-screen view at export size (zoom raised by log2 of the width ratio) and its "every tile loaded, camera still" wait. The legend is read from `#legend-body.visible` (this repo's legend), caption colors come from `ui.palette`, and the data line from the window (`studio_data_line`: station, product, tilt, scan time, dealiased) instead of reading the archive bar's labels. No lidar line (session 19).
- `ui/studio/exporter.py` is the fork's loop (identical frames reuse the last image; radar switched per keyframe; clock, radar and view restored at the end or on Cancel); scan times come from `studio_scan_times()` instead of the archive bar's private list.
- `MapWidget.map_page()`: the page stays reachable while it's in the off-screen view; every `runJavaScript` goes through it.
- Window: new `ui/app/main_window_studio.py` (`MainWindowStudioMixin`): `studio_frame_pending`, `studio_scan_times`, `studio_radar_state` / `studio_apply_radar` (a new station takes product and tilt on its first scan, through `_package_radar_view` as case packages do), `studio_data_line`, and `_start_studio_export(project, path)` (progress window with Cancel, one export at a time; closing the window cancels it). Session 14's panel calls it; no menu entry yet.
- "Wait for the archive radar": a frame waits for the radar index, the clock's scan in the chosen product, its normal render, and then its sharp render (this repo's session 7 render, in place of the fork's "full resolution" step). New `_radar_settled_scan` is set when the sharp image is shown or cached, or skipped for good (low memory, a failed render, which now reports back instead of just logging), so a skipped render can't stall the export until the 25 s timeout. Also waits for the trails rebuild timer (the fork's "trails pending" check left out of session 11) and pending satellite frames. `ArchiveRadarFetcher` gained `index_loading` and `scan_times()`.
- Tests: the fork's two files (writer tests made to run alone, plus a canceled MP4 leaving no file) and new `tests/test_studio_window.py` (each stage of frame readiness, keyframe radar incl. a station switch, data line and captions, the camera and zoom offset, the sharp render settling its scan in all four ways, the fetcher's listing accessors, the export loop end to end with a fake capture: distinct frames drawn once, every frame written, even size, radar switched and restored, clock restored, no-keyframes and cancel; one export at a time and close cancels).
- Not verified: a real export from a running archive session (the off-screen view at 1080p / 4K, the tile wait, the sharp render's cost per new scan, which makes "smooth" steps slow, MP4 playback in QuickTime / VLC), and GIF size on a long movie. These can only be tried by hand once session 14's panel exists.

## Session 14: Video Studio, panel (D29)

Files: new `ui/studio/panel.py` (1,691 lines in the fork); File > Video Studio.

- The editor layout:
  - a case-clock strip;
  - a movie timeline with clips, holds and draggable keyframes;
  - an inspector;
  - undo / redo, preview and loop;
  - Save Frame, and saving / reopening projects.
- Esc or Close Studio leaves it.
- Don't change a field on scroll unless it has focus (a fork fix).
- Restyle it with `P.*` tokens.

**Status:** done 2026-11-12 (uncommitted; full suite 912 passed). Changes from the fork's version:
- `ui/studio/panel.py` is the fork's editor (case-clock strip, movie timeline with clips, holds and draggable keyframes, inspector, undo / redo with coalesced typing, preview and loop, projects, the wheel guard), restyled: colors from `ui.palette` (keyframes `MODE_ARCHIVE`, clock `ACCENT`, playhead `ERROR_SOFT`, backward moves `MODE_VIEWER`), its own `#studioPanel` stylesheet in place of borrowing `#archiveControls`, the app's checkbox indicators, and key names through `display_key`. It reaches the window only through hooks (`studio_scan_times`, `studio_radar_state` / `studio_apply_radar`, `_studio_save_frame`, `ArchiveControls.next_scan` / `prev_scan` / new `local_time_text()`), not the archive bar's private fields.
- Fit at the window's minimum width (1024 px, so a 1000 px panel); the fork's layout needed about 1,140 px. The fork's `min-width: 0` on combo boxes silently undid every `setFixedSize` (a stylesheet width wins), so combo widths are set in the stylesheet (`COMBO_WIDTHS`), as the archive bar does. Shorter labels with the detail in tooltips: ½× / 2× (no "speed"), no "Zoom" or "clock" label (the step box reads "clock: 1 min"), "Retake" (was "Retake from clock + map"), "Use current" (radar), resolution shown as "1080p" (the full name stays the stored value), format MP4 / GIF / PNG; smaller thumbnail; the radar line and the move note elide.
- Window (`MainWindowStudioMixin`): File > Video Studio (Ctrl+Shift+V, new `studio` entry in `ui/shortcuts.py`, so it's in the palette and the ? list), checkable. Open: the panel replaces the archive bar at full width (`_layout_overlays`); the table's bindings of the studio's keys (play, step back / forward, skip start / end, track delete, K sounding) are suspended, as the fork disabled the archive bar's, through new `ShortcutRegistry.suspend` / `resume` (suspended keys stay off when focus leaves a text box). The panel's own keys go quiet while a field has focus. Edit > Undo / Redo read "Studio Edit" while it's open and undo the studio (`_edit_undo`). Esc closes it after the shortcut list, settings and click modes. Export Movie… asks for the file (or the folder for PNG frames), then `_start_studio_export`; results flash in the panel.
- Save Frame is the studio's own (`_studio_save_frame`: the map once complete, with the project's captions, to `data/studio/<date>/`). The fork also turned the general screenshot (Ctrl+S) in ARCHIVE into this capture; not ported, Ctrl+S is unchanged. Projects go in `data/studio/<date>/`, git-ignored.
- Guide: a "Video Studio" section under Archive Mode, plus the menu table and the keys.
- Tests: new `tests/test_studio_panel.py` (keyframes from clock and map: append, no-change, split, retake with radar; delete and undo / redo; speed, coalesced hold, easing; retiming; the wheel guard; scrubbing with radar switches and clock steps; preview and close; case-clock buttons and Save Frame; save / reopen, unsaved-changes prompt; export enabled at two keyframes; studio keys off while typing; the registry keeping suspended keys off; the window: archive bar swap, suspended keys, menu check and Edit texts, Esc, the Close button and toggle, undo routing, export file choice, Save Frame writing a PNG). `test_case_window.py` and `test_main_window_shortcuts.py` hosts gained the mixin and the new menu entry.
- Checked offscreen: the panel at 1000 px and 1400 px. Not verified: the panel in the running app (its place over the map, the off-screen 1080p / 4K export from session 13, preview smoothness, thumbnails, keyboard focus moving between the map and the panel's fields, the macOS menu bar's Edit texts).

## Session 15: Launch window case finder, change day (D30)

Files: new `ui/launch/date_finder.py` and `ui/launch/availability.py`, `ui/launch/dialog.py`, `main.py`.

- A calendar button and year grid next to the archive date.
- *Browse available cases* finds dates with data in the background, newest first, and shades the calendar. *Surprise me* picks a random date with data. *Open case…* opens a case package from the launch window.
- Fit them into this repo's scrolling launch form (phase 1).
- **Change day** (File menu):
  - `MainWindow.restart_with` and a session loop in `main.py` reopen the launch flow without quitting;
  - port the fork's teardown fixes with it (stop the map's scheme handler, disconnect archive workers), and use `scripts/stress_archive_sessions.py` as the manual check.
- Fork tests: `tests/test_date_finder.py`, the archive parts of `tests/test_launch_dialog.py`, `tests/test_archive_availability_worker.py`.

**Status:** done 2026-11-12 (uncommitted; full suite 996 passed). Changes from the fork's version:
- `archive/catalog.py` is the fork's availability index (background scan newest sources first, 24 h / 1 h disk cache in `~/.cache/storm/archive-dates.json`, a failed check stays unknown), cut to the sources this repo reads: the 16 FOFS mesonet vehicles plus "other vehicles", from the crawled FOFS index. The flat THREDDS listing scan (`CatalogSpec`) stays for sessions 18–19 to register CLAMPS and lidar; NOXP, CopterSonde and the multi-family sites are left out. URLs come from `data/thredds_paths.py`; no `package_sources` (the launch window has no package). A failed check is retried next launch while its old dates still shade (the fork kept budget-limited NOXP records the same way).
- `ui/launch/availability.py` is the fork's worker, except an unexpected scan error is logged instead of re-raised: re-raising killed its thread for good, so later requests queued forever.
- `ui/launch/date_finder.py` (the fork's "Find dates…") is the *Browse available cases…* window, restyled with `P.*` tokens (new `P.AVAILABLE*` colors; the month grid ramps from `AVAILABLE` to `ACCENT`). "Instruments" read "Vehicles", since only mesonet vehicles are registered yet.
- `ui/launch/dialog.py`: the calendar button and progress ring beside the archive date, `_AvailabilityCalendar` (shaded days, no future days) on both the field's calendar and the button's, the year grid on both calendars' year button (Qt's spin box locked down), and three links under the date: Browse available cases…, Surprise me, Open case… (a case label with Clear; another date drops it). Styles in `ui/launch/styles.py` (`_YEAR_GRID_STYLE`, `_LINK_BTN_STYLE`, `_CAL_BTN_STYLE`, `_FINDER_STYLE`). The scan only runs while ARCHIVE is selected and the window shows, restarting (debounced) on date changes. `LaunchDialog(mode=, archive_time=, verified_modes=)` lets Change Day reopen it in ARCHIVE on the last date without asking the archive / admin passphrase again.
- Change Day: *File > Change Day…* (ARCHIVE only, in the palette as "Change day"; the fork's archive-bar DAY button isn't ported). It asks, then closes the window with `restart_with = SessionRestart(clock time)`. Opening a case package for another date now offers to reopen STORM on that date (`SessionRestart(..., launch_window=False)`) instead of the session 12 message. `main._run_sessions` is the loop: `--case` the first time only, the launch window or the package's date after a restart, other top-level windows (soundings, VAD) closed, the old window deleted before the next is built (`sendPostedEvents(DeferredDelete)`, since no event loop runs there). `closeEvent` still quits the event loop either way; main reads `restart_with`. No `session_aborted` signal.
- Teardown: `MapWidget.shutdown()` removes its storm:// handler from the process-wide profile (closeEvent), so the next window's map loads; the other fork fixes (`_silence_archive_workers`, `_later`, executor shutdown) were already here. The stress script found more, fixed here: four delayed lambdas (`QTimer.singleShot` in the vehicles, overlays, radar and sounding mixins) ran against a deleted window (a native abort on `_chk_station_plots`), now through `_later`; and download threads emitting on fetchers deleted with their window raised "wrapped C/C++ object has been deleted" and logged errors into the next session. New `core/qt_signals.emit_alive` drops those signals; used at the thread-side emits of the satellite, hazard, sounding, vehicle-obs and MQTT readers and `RadarControls`' product probe. The radar fetcher's `_emit` now takes the signal by name, since reading `self.index_loaded` off a deleted fetcher already raised.
- `scripts/stress_archive_sessions.py` is the fork's, with settings and tracks in a scratch folder (every module's `QSettings` swapped), and each window closed as Change Day closes it. Run on this Mac: 8, 12 and 16 cycles, ending clean with "STRESS DONE" after the fixes (before them: an abort in cycle 2).
- Guide: the archive config section (calendar, shading, the three links), Change Day and other-date packages under Menu Bar and Case Packages.
- Tests: the fork's catalog tests without NOXP / CLAMPS (`tests/test_archive_catalog.py`, plus sites, URLs and failed-check retry), worker (`tests/test_archive_availability_worker.py`, plus an error not killing the thread), finder (`tests/test_date_finder.py`), the archive parts of the launch tests (`tests/test_launch_dialog.py`: calendars, year grid, scan scheduling and late results, progress ring, Surprise me, Open case, Change Day presets and remembered passphrases, links fit the form), new `tests/test_session_loop.py` (normal quit, Change Day, quitting from the second launch window, other-date package, `--case` once, old window deleted and stray windows closed, map shutdown) and `tests/test_qt_signals.py`; `test_case_window.py` covers the menu entry, Change Day and the other-date reopen. `tests/conftest.py` keeps the date cache out of `~/.cache` and has the fork's `listed_platform` fixture.
- Checked offscreen: the launch window at its 380 px minimum, the shaded calendar and the finder. Not verified: Change Day in the running app (the launch window reappearing, the second session's map and fetchers, the macOS menu entry), the scan against the live FOFS index (the index crawl takes a while on first run), and Surprise me / Browse with real data.

## Session 16: Damage paths and ASOS replay (C23, C22)

Files: new `archive/fetchers/damage_paths_archive_fetcher.py` and `archive/fetchers/asos_archive_fetcher.py`, `ui/controls/archive_surface_controls.py`, map layers.

- **Damage paths:** NWS DAT damage lines and NCEI Storm Events, with hover details.
- **ASOS:** IEM history replaying on the archive clock, with stations picked by drawing a box. Reuse phase 1's ASOS box (cancel handling included).
- Neither source needs NSSL.
- Fork tests: `tests/test_damage_paths_archive_fetcher.py`, `tests/test_asos_archive_fetcher.py`.

**Status:** done 2026-11-12 (uncommitted; full suite 1064 passed). Changes from the fork's version:
- No feature flags (the fork gated both behind `archive_asos` / `damage_paths`); both are always there in ARCHIVE. New endpoints `IEM_ASOS_HISTORY`, `DAT_DAMAGE_LINES`, `NCEI_STORM_EVENTS_CSV` in `data/endpoints.py`, with probes. All three checked live 2026-11-12.
- `archive/fetchers/damage_paths_archive_fetcher.py`: the DAT query now filters dates on the server (`where starttime` within the day ± 1, or `stormdate` when `starttime` is empty). The fork filtered dates only afterwards, and the layer returns at most 2000 features (345 lines in central Oklahoma alone), so a busy box could miss the case's paths. NCEI's yearly CSV (tens of MB) is read once per run and kept in memory, not downloaded again on every redraw. Thread-side emits use `emit_alive`; `cancel()` drops a load in progress. Checked live: DAT has no paths for 2013-05-31 around OKC, so the NCEI fallback found 12 (El Reno and the rest).
- `archive/fetchers/asos_archive_fetcher.py`: the results reach the UI thread through a queued signal (the fork assigned them from the worker thread). Stepping the clock back removes only the stations with no report yet (`station_removed`) and steps the others back, instead of clearing every plot (flicker). A failure for an old box isn't reported. New `load_started`, `observations_by_station()`, `has_stations()`, `is_loading()`. `load_asos_station_roster()` moved out of `SurfaceFetcher` into a module function in `data/fetchers/surface_fetcher.py`, which live ASOS still uses.
- No `ui/controls/archive_surface_controls.py`: SURFACE in ARCHIVE reuses the live drawer (`SurfaceControls.configure_for_archive()`: mesonet buttons hidden, ASOS with its "new ASOS bbox" link, show plots, status line, an ASOS-only age legend). The fork's drawer hint lines aren't ported (the click-mode banner says to drag a box). HAZARDS gains DAMAGE PATHS after NWS CWA (`HazardControls.add_damage_paths_button`).
- Window: new `ui/app/main_window_archive_surface.py` (`MainWindowArchiveSurfaceMixin`), set up from `_begin_archive_startup`. The box is phase 1's, Esc / right-click handling included. Each button has its own click mode (`asos_bbox`, re-registered for ARCHIVE, and `damage_box`), so the box goes to whichever asked, instead of the fork's two handlers both listening to the same signal. Backing out with nothing loaded turns that button off, and so does a box with no reports or paths, or a failure (the status line says why). "new ASOS bbox" keeps the stations shown until the new box is drawn. ASOS plots render off the UI thread (one render at a time, chunked), colored by report age at the archive clock (`_obs_age_color(reference_time=)`): the fork only redrew stations with a new report, so a station that stopped reporting never turned red. ASOS marks the Surface layer's health.
- Map: the fork's damage-path layers (hit line, casing, line, start / end dots), kept on `window` so a basemap switch redraws them; a multi-line hover readout (`#damage-path-tooltip`: rating and source, start → end UTC, length / width / wind, position), checked before the hazard hover; `MapWidget.set_damage_paths(fc | None)`; "Damage Paths" in the layer order.
- Trails: ASOS stations join time-to-space (session 11 left them out), with `no_station_pressure` for them.
- Guide: *ASOS Replay and Damage Paths* under Archive Mode, the HAZARDS table row, the archive line under Surface Obs, two rows in How Archive Data Works.
- Tests: the fork's fetcher tests, adapted and extended (`tests/test_asos_archive_fetcher.py`: the session span, merged batches, case-package kind, rewind, results at the clock, the cap, stale failures; `tests/test_damage_paths_archive_fetcher.py`: the server-side date filter, DAT normalizing, NCEI rows / newest CSV / once per year / two years at New Year, cancel, DAT failure falling back, both failing). New `tests/test_archive_surface_window.py` (the two click modes, box routing, backing out, new box, replay and rewind on the map, age at the clock, off / empty / error, damage found / none / error / late result, switching boxes, the drawers, the widget call, the template, the layer order); `tests/test_archive_trails.py` covers ASOS in time-to-space.
- Checked in a real ARCHIVE window (2013-05-31, normal Qt platform, window off-screen): 11 ASOS stations replayed and seeking worked; 12 NCEI paths drawn around OKC in EF colors. Not verified: dragging the box by hand, the hover readout, a DAT case (needs a post-2015 date), and case packages carrying ASOS / damage paths (a package doesn't redraw the boxes). Under `QT_QPA_PLATFORM=offscreen`, WebEngine lost its GPU context and crashed; not caused by this session.


## Session 17: Infrared satellite (C24)

Files: `archive/fetchers/satellite_archive_fetcher.py`, `ui/controls/satellite_controls.py`.

- Add a VIS / IR choice in this repo's own fetcher. IR lists band 13 (`C13`) keys instead of `C02`.
- Render brightness temperature with an IR color table, not the reflectance path. That's the fork's "all white" bug: kelvins drawn as reflectance.
- Index only once SATELLITE is turned on, not at startup (today all three modes list 24 hours each at startup).
- Before 2017 (no GOES-16), show "none" instead of a red error.
- Look at shrinking visible (band 2) cost, which the user flagged.
  - Memory: keep the raw `uint16` counts and apply scale / offset only to the pixels drawn, instead of a full `float32` copy (half the size).
  - Download: the 62 MB comes from the file itself. The lever there is reading only the chunks covering the map view (HTTP range requests into the chunked netCDF/HDF5), if the files' chunking allows it.
- Port the fork's `tests/test_goes_archive_render.py` idea for the IR path.

**Status:** done 2026-11-12 (uncommitted; full suite 1096 passed). Changes from the fork's version:
- The fork's rewrite isn't used; this repo's fetcher gains the band. One S3 listing per hour now indexes both `C02` and `C13` (matched on the `-M<n>C<nn>_` part of the key, not a bare substring), so IR costs no extra listing. Frames are cached per (sector, band, time); `frame_id` carries the band, so the map never shows a cached VIS image for IR. Bucket URLs come from `data/endpoints.py` (`s3_bucket_url`, `GOES16_BUCKET`, `GOES19_BUCKET`).
- IR rendering: new `core/goes_ir.py`, an enhanced IR table (grey from 50 °C to -30 °C, then blue / green / yellow / red / purple, white below -90 °C) encoded as RGBA. The fork only inverted kelvins to grey; the plan asked for a color table. VIS is unchanged (grey, gamma 0.5).
- Lazy indexing: `set_active(bool)` follows the SATELLITE button in ARCHIVE. Nothing is listed or downloaded until it's on, and off fetches nothing more. "satellite" is no longer an archive startup loading task, and **Sat:** reads `off` until then. Turning it on indexes all three sectors (the MESO hover boxes need theirs), and the MESO box comes from the day's last IR frame (about 0.5 MB, not the visible file).
- No imagery: sessions ending before 2017-02-28 (the first GOES-16 CMIP day on AWS, checked live) report `unavailable` without a request, and a day whose CONUS listing is empty does too. The drawer disables its sector and band buttons with the reason as tooltip and a note line; **Sat:** reads `none` (tooltip says why); the layer badge goes off, not red. Listing skips hours before the first data.
- Band 2 memory: the file's raw counts are kept (no `float32` copy). Box averaging runs on the counts in strips, skipping fill, and scale / offset apply only to the sampled output pixels. Measured on a real CONUS band 2 file: peak 567 → 106 MB and 0.63 → 0.22 s per frame. The output is pixel-identical to the old renderer, because the window starts on a box-average block boundary of the whole grid.
- Band 2 download: the files are chunked 250×250 (gzip), so range reads work. CONUS visible frames read only the source window covering the fixed CONUS bbox: `_RangeFile` (64 KB blocks, h5py over HTTP Range), with the window's chunks listed via `chunk_iter` and fetched in a few merged, parallel requests. Measured: 29.9 of 66.7 MB in about 30 requests. Trade-off: walking the chunk index takes about 25 small sequential requests, so a frame took about 4 s against 2 s for the whole file on this Mac's fast connection. It wins on slower links and data caps. MESO and IR files are read whole (no crop; IR is only 3.9 MB). A case package's copy is read whole. A server ignoring Range, or any range failure, falls back to the whole file. Provenance records a range read by size only (`source="network (ranges)"`), so an export downloads the full file.
- Drawer: VIS / IR buttons after the sectors (archive only, exclusive), the IR color bar under the row while IR is on, `set_unavailable()`, and the band saved in session state. Command palette: "Satellite infrared (IR)" / "Satellite visible (VIS)" in ARCHIVE. **Sat:** shows the band ("Sat: AWS CONUS IR 20:26Z").
- Guide: VIS / IR under Satellite, the archive GOES row (it still said IEM WMS) and the **Sat:** indicator.
- Tests: new `tests/test_archive_satellite.py` (31 tests), built on synthetic GOES-16 CMI files written with h5py. They cover: the IR table; IR through the table rather than reflectance (the fork's bug); VIS grey and gamma; fill and unsigned counts; the counts box average and its strips; the window render matching a full read; block-aligned windows; range reads (window only, merged requests, server ignoring Range, failure fallback, packaged copy, provenance); one listing for both bands; band switching and its cache; ranges only for CONUS VIS; nothing fetched until on or after off; pre-2017 and empty days; MESO boxes from IR; the drawer's buttons, legend and unavailable state; and the window handlers. `tests/test_session_state.py` covers the band round trip.
- Checked in a real ARCHIVE window (2024-05-06, window off-screen): nothing indexed before SATELLITE was on; the first VIS frame in about 21 s (most of it listing three sectors × 30 hours); IR in 4 s; IR registered on the state borders; range-read provenance as above; 2016-05-24 showed "Sat: none" with the reason and a disabled drawer. Not verified: MESO IR on the map, VIS / IR across a GOES-19 date, and the live viewer (it stays visible-only; IEM's WMS has IR layers if wanted later).

## Session 18: CLAMPS and CopterSonde (C18, C21)

Files:
- new `archive/fetchers/clamps_wind_archive_fetcher.py`, `clamps_tropoe_archive_fetcher.py`, `clamps_sonde_archive_fetcher.py`, `clamps_surface_archive_fetcher.py`, `clamps_surface_playback.py` and `coptersonde_archive_fetcher.py`;
- `archive/fetchers/sounding_archive_fetcher.py` (the provider chain);
- the VAD window.

- **CLAMPS wind profiles** in the VAD window. Phase 1's time-height view fits these well; restore the fork's archive-only VAD hooks (`preloaded_set`, `start_time`) that phase 1 left out.
- **TROPoe retrievals and mobile sondes** in the sounding viewer. Providers are tried in turn, each limited to the session day and clock.
- **Trailer surface observations:** the met tower first, then the MWR's surface fields. Kelvin values stored under a °C label are converted. MWR files are found by listing the folder, not by guessing the file name.
- **PERiLS CopterSonde profiles** in the sounding viewer.
- All of these need NSSL THREDDS (session 0).
- Fork tests: `tests/test_clamps_*`, `tests/test_coptersonde_archive_fetcher.py`, `tests/test_archive_sounding_providers.py`.

**Status:** done 2026-11-13 (uncommitted; full suite 1166 passed). Changes from the fork's version:
- Paths: the fork's CLAMPS, sonde and PERiLS names went into `data/thredds_paths.py` (`CLAMPS_WIND_STREAMS`, `CLAMPS_TROPOE_*`, `CLAMPS_SURFACE_STREAMS`, `SONDE_*`, `PERILS_IOPS`), with checker entries; raw lidar and NOXP aren't there (sessions 19 and out of scope). `scripts/check_data_sources.py` imported the `_ssl_context` session 4 removed; it now uses `config.NSSL_SSL_CONTEXT`. Checked live 2026-11-12: all 30 folders answer except the optional CLAMPS2 `aeri_mwr.v2`.
- New `archive/fetchers/clamps_common.py` replaces the five copies of the fork's unverified-SSL `urlopen` / retry: `read()` goes through `package_sources` with STORM's NSSL SSL context, User-Agent and retries (session 4's helper), and `list_folder()` reads a catalog page once per run (404 lists empty; other failures raise, so "none" and "couldn't ask" stay apart). Every source finds files by listing, the wind profiles too (the fork guessed `.000000.cdf`). Listings matter: folders hold other streams' files (`clamps10tropoe2bellC1…` in the `aeri_mwr.v1.C1` folder, `clampsmwrpC2…` beside the MWR), so names match on the datastream prefix.
- **Wind profiles** (`clamps_wind_archive_fetcher.py`): the fetcher keeps the session's sets, so pressing VAD again doesn't download again (CSM files run to ~100 MB). New CSM filter: on the truck's DL2 2022-05-19 file, 38 % of cells passing the fork's checks were over 100 kt with r_sq near 1, up to 11 km (fits to noise). CSM files (they carry `wspd_sigma`) now also need intensity ≥ 1.01 and sigma ≤ 2 m/s; VAD files aren't filtered on intensity (CLAMPS2's good VAD winds sit at 1.002). `VADSet.hodograph_bound_kt` takes the 99.5th percentile of winds instead of the maximum, and Bunkers on at most ~200 spaced profiles (a 3,147-profile day: 1.2 s → 0.04 s, bound 270 → 130 kt). The VAD window builds only the spaced tick labels past 48 profiles (one label per profile meant thousands of widgets). `preloaded_set` / `start_time` restored as in the fork; RADAR > VAD in ARCHIVE loads the lidar winds (picker shows each source's profile count), with an archive tooltip.
- **Sounding providers**: the chain is the fork's (THREDDS sonde, NSSL API, TROPoe; each limited to the clock's UTC day up to the clock). The error names the providers that had nothing as well as those that failed. Sonde launches are placed from the truck's one-second FOFS track, else its MQTT track, through a lookup the window passes (`set_truck_position`, `nearest_fix` within 60 s), instead of the fork's `load_dltruck_track` cache (session 19). `Sounding` gains `location_source` / `location_time`; `SoundingSet` gains `is_clamps_tropoe`, `is_coptersonde`, `is_time_series`. TROPoe takes all of the day's files for the first variant with any (the fork took the first file) and silences xarray's duplicate-dimension warning.
- **Sounding viewer**: a TROPoe profile crashed the hodograph inset (no wind levels); it now reads "no wind". Titles and headers for TROPoe and CopterSonde, "launch position unknown" for unplaced sondes, and theme entries for both sources. Past 8 soundings only about five spaced tick labels are built (TROPoe has a retrieval every few minutes; the row is ~180 px). Day numbers now show only when the soundings span two days (the old test counted soundings against distinct hours, so many in one hour added day numbers).
- **CopterSonde** (`coptersonde_archive_fetcher.py`): limited to ascents started by the clock (later ones aren't downloaded), and no request outside 2022 / 2023. Drawer: COPTERSONDE (archive only) with a site list after NSSL (`set_coptersonde_available`, `set_coptersondes` keeps the pick on refresh).
- **Trailer surface** (`clamps_surface_archive_fetcher.py`, `clamps_surface_playback.py`): the MWR's wind direction is wrapped to 0-360 (CLAMPS1 2021-07-29 reads 360-718°: heading added without wrapping). Fetch failures raise when nothing was found. The playback uses `emit_alive` and `session_days`.
- Window: new `ui/app/main_window_clamps.py` (`MainWindowClampsMixin`), set up from `_begin_archive_startup`; trailers join vehicle history and trails (between one-second data and MQTT, as in the fork). `_archive_clamps_*` workers are silenced on close with the others.
- Guide: *CLAMPS and CopterSonde* under Archive Mode, the VAD button, COPTERSONDE and the archive NSSL chain under Soundings, rows in How Archive Data Works (whose Soundings row still said open-meteo HRRR).
- Tests (new): `tests/clamps_fakes.py` (a fake THREDDS and synthetic CLAMPS files); `test_clamps_wind_archive_fetcher.py` (listing, `read`, parsing quirks, the CSM filter, the session span, the kept sets), `test_clamps_sounding_sources.py` (TROPoe, sondes and truck placement, `nearest_fix`, CopterSonde, the provider chain), `test_clamps_surface_archive_fetcher.py` (fields, fills, wrapping, kelvin, met tower, sources, restarts, playback), `test_archive_clamps_window.py` (drawer, viewer, VAD window, the mixin's handlers).
- Checked live: each source against real days (sonde 2022-05-13, TROPoe 2022-03-01, CopterSonde 2023-03-03 with 3 sites, MWR 2021-07-29 and 2023-06-09 kelvin file, CSM winds 2022-05-19). In a real ARCHIVE window (off-screen): 2023-06-16 offered three wind sources and opened the truck's CSM winds at the clock; NSSL found nothing (no sondes or TROPoe that day, API 404); COPTERSONDE showed and reported none; that day's CLAMPS2 MWR file has no sensor data (all −1272.15), so no trailer. 2023-06-09: CLAMPS1 replayed on the map with history and trails, and left after its last report. Not verified: a sonde or TROPoe through the window's NSSL button, CopterSonde picked in the window, a case package carrying these, and the CLAMPS2 met tower on a day it has data.

## Session 19: Lidar (C20)

Files:
- new `core/lidar_scans.py`, `archive/fetchers/raw_lidar_archive_fetcher.py`, `archive/fetchers/raw_lidar_quicklook_fetcher.py`, `ui/controls/raw_lidar_controls.py` and `ui/map/lidar_overlay.py`;
- `archive/positions.py`.

- Lidar truck and CLAMPS trailer Doppler lidars, picked on the map like a radar station.
- PPI and CSM sector scans are drawn as the clock reaches them, with scanning periods shown on the slider (session 6's `set_coverage`) and a one-row panel.
- Truck scans are rotated by the recorded heading. When it's missing, the heading is estimated from the truck's compass and flagged ⚠.
- Read 2017–2020 files whose range is labeled "km AGL".
- Keep fields in float32, don't keep scan-less files, and reuse the truck's FOFS track.
- Fork tests: `tests/test_lidar_scans.py`, `tests/test_raw_lidar_archive.py`, `tests/test_lidar_overlay.py`.

**Status:** done 2026-11-13 (uncommitted; full suite 1239 passed). Changes from the fork's version:
- Paths: `RAW_LIDAR_PRODUCTS`, `RAW_LIDAR_PLATFORMS` and `raw_lidar_stream` in `data/thredds_paths.py`, with checker entries (8 folders). `core/lidar_scans.py` is the fork's file (imports hoisted).
- Downloads: no NOXP `download_asset` (out of scope). `clamps_common` gained `download_to_cache` (a case package's copy first, else streamed to `~/.cache/storm/raw_lidar` through session 4's retrying NSSL opener, never left half-written, hash-checked on reuse, recorded in provenance as *raw lidar*, which session 12 already listed), `is_cached` and `prune_cache` (3 GB, run before each session's downloads; `.part` files only once an hour old). Cap 768 MB (the fork's 512 MB): checked live, CLAMPS2's CSM days run 0.3–0.8 GB and CLAMPS1's July 2021 ones 0.8–1.8 GB, so some can't be loaded; a picked lidar's refused file says so in the status bar. Discovery lists the folder (`clamps_common.list_folder`) instead of the fork's catalog parser. `read_site` keeps the fork's range-request header read, with STORM's User-Agent.
- Truck track: no `load_dltruck_track` / `TruckFix` cache. The window passes the truck's session track (one-second FOFS, else MQTT: `_lidar_truck_track`), built once per session into `archive/positions.PositionTrack`, which now matches all rays at once with `searchsorted` (the fork looped per ray in Python) and keeps the compass series. `Observation` gained `heading_deg`; the FOFS reader parses `compass_dir`. `RawLidarRays` dropped `housekeeping`, `position_time_epoch`, `summary()` and the metadata dump (nothing read them) and gained `scans`; `ground_geometry_valid` takes `north_referenced` directly instead of parsing the azimuth comment.
- Fetcher (`raw_lidar_quicklook_fetcher.py`, class `ArchiveRawLidarFetcher`): the fork's survey / held-back large files / scan-less files dropped, plus a cancel event (closing the window stops downloads) and errors prefixed with the instrument. Overlay: the fork's renderer with this repo's velocity colormap; no `style` argument.
- Map: no platform markers here, so new lidar layers in the map page: purple location dots with labels (colliding labels hide; the truck's stops sit a few km apart), the picked one ringed, hover text in the hazard tooltip, click through a new bridge slot (`lidar_location_clicked`); the sweep is an image layer served by the scheme handler (`/lidar/<n>.png`, like satellite frames), moved under the road layers above the radar on each update.
- Window: `ui/app/main_window_lidar.py` (`MainWindowLidarMixin`); LIDAR (new toolbar icon, overflowable) next to RADAR, archive only, with a drawer (`RawLidarControls` on `Drawer`, palette tokens), in the drawer manager, overlay stack, palette names and the session-restore skip list. Left out: the fork's "radar under the lidar" switch (RADAR's own controls do it) and its feature flag. Picking a lidar now moves the clock before filling the field list (the fork's order drew a "no scan" frame first). The fork's `perf_log` events and AST-based callback test aren't ported.
- Tests: the fork's three files adapted, plus `test_archive_positions.py` (lookup, vectorized match, compass, `compass_dir`), `test_raw_lidar_loading.py` (cache reuse / damage / package / cancel / cap, pruning, discovery, survey hold-back and drops, track built once, cancel) and `test_archive_lidar_window.py` (drawer, map widget, scheme route, the mixin's handlers). That last file tears its widgets down explicitly: left to the cyclic GC they were destroyed during `test_loop_timeline`'s paint and crashed the suite (SIGBUS).
- Checked live: discovery and header sites on 2023-06-09/16 and 2022-05-19; most PPI/CSM files that season hold only 60–70° VAD rings (0 scans, correctly dropped). Real sweeps: truck CSM 2024-05-01 (recorded heading 19.3°, placed from its FOFS track, 5 stops) and CLAMPS2 CSM 2023-11-13 (83 sweeps at its site), ~0.1 s per render; the intensity filter clears the far noise; a 2017 "km" file reads. 2025-04-29: the truck's FOFS track covers only 23:52–01:01Z, so its scans stay unoriented (red ⚠). In a real ARCHIVE window (2024-05-01, macOS platform, off-screen): the survey finished in 8 s with 5 stops, picking one paused at its first scan, drew it with the field list and slider strip, and stepping drew the next. Not verified: the dots and hover in the window by eye, a truck file with the heading estimated from the compass in the window, a held-back (>150 MB) file loading after a pick, and a case package carrying lidar files.
