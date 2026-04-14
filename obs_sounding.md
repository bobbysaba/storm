Obs Sounding Fetcher — Implementation Plan

Goal

Make obs_sounding_fetcher fast and reliable for both synoptic (00/12Z) and off‑synoptic/special launches by using IEM as the fast primary source and SPC as a fast fallback for special times; UWYO remains a secondary fallback for BUFR/CSV when needed.

Summary of approach

1. Reduce timestamp search window: instead of querying every hour across two full dates, only search hourly from the most recent 12Z (same UTC day if now>=12Z, otherwise previous day) up to the current UTC hour. This targets likely special launches while cutting requests roughly in half.

2. Primary source: IEM JSON (mesonet agron raob.py). Quick query per timestamp with short timeout (DEFAULT_IEM_TIMEOUT = 2s). Accept first non-empty profile set.

3. First fallback: SPC text (.txt) for the matching observed directory (format: YYMMDDHH_OBS). Query SPC directory for the timestamp and fetch STN.txt (e.g., OUN.txt). Timeout: 5s. Parse SPC tabular text into the Sounding format used by the app.

4. Secondary fallback: UWYO CSV via /wsgi/sounding?datetime=...&id=<stationid>&type=TEXT:CSV for format-rich output (timeout: 12s). For UWYO use sounding_json only to map stationid; cache that mapping.

5. Concurrency & early exit: For each station request, run the IEM queries across the reduced timestamp list in parallel (ThreadPoolExecutor). For each timestamp: try IEM; if IEM returns empty, concurrently start SPC fetch for that timestamp (and optionally UWYO if SPC fails). Use futures.wait with FIRST_COMPLETED to take the first valid result and cancel remaining futures.

6. Caching: in-memory (LRU) cache keyed by (station_id, valid_time, source) for configurable TTL (default 10 minutes). Also cache stationid mapping for UWYO (session lifetime).

7. Timeouts & workers: sensible defaults — max_workers=8 for timestamp-level parallelism; per-request timeouts applied at HTTP layer.

8. Error handling & metrics: instrument fetch timings and success/failure counts. Emit warnings on repeated empty results. Keep existing Qt signals (sounding_ready, fetch_error).

9. Parsing
- IEM JSON: unchanged (already supported). Keep existing _parse_profile.
- SPC text: implement small parser to read %RAW% block and produce per-level dicts similar to IEM profiles (pres,hght,tmpc,dwpc,drct,sknt).
- UWYO CSV: parse CSV rows into level lists; convert wind m/s to sknt if needed or adapt parser to accept m/s.

10. Backwards compatibility: keep public interface (ObsSoundingFetcher.fetch) and SoundingSet/Sounding outputs unchanged.

Display change: Local-time-day grouped soundings

Rationale

Operators want the list of available soundings shown per station to reflect the station's local calendar day rather than the full UTC day. This makes it easier to find "today's" launches as experienced locally while still preserving UTC launch timestamps for scientific accuracy.

Behavioral spec

- Define the station's "local day" by converting each sounding's UTC valid_time into the station's local timezone and selecting soundings whose local date equals the station's current local date.
- The UI should still display the UTC launch time prominently (e.g., "12Z Apr 13") but group/filter by local date.
- Example: At 02Z Apr 14 UTC, for a station in CDT (UTC-5 currently), the station's local date is Apr 13 — show soundings with local-date Apr 13 (which will include UTC 05Z+ launches that fall into Apr 13 local), but labels remain "05Z Apr 13" etc.

Implementation notes

1. Determining station timezone
- Preferred: use `zoneinfo` (Python stdlib) with a mapping from lat/lon to IANA timezone (via timezonefinder or tzwhere dependency). This yields correct DST handling and avoids longitude-only approximations.
- Fallback: if timezone lookup is unavailable, compute offset = round(lon/15) and use a fixed offset timezone (no DST). Document limitations.
- Cache zone lookups keyed by station_id for session lifetime.

2. Filtering logic
- For display, compute station_local_date = valid_time.astimezone(station_tz).date()
- Compute station_now_local_date = now_utc.astimezone(station_tz).date()
- Show soundings where station_local_date == station_now_local_date.
- Provide a UI control ("Show previous/next local day") to allow browsing adjacent local days.

3. UI and labels
- Keep existing labels like "12Z Apr 13" but add a secondary subtitle showing local date/time if desired (e.g., "Local: 07:00 CDT Apr 13").
- Ensure scrubber/order remains chronological by UTC time to keep plotting consistent.

4. Interaction with reduced timestamp window
- When generating candidate timestamps to query, use the station local date window: start from the most recent 12Z that falls within the station's local date through to the current UTC hour — but still attempt standard synoptic times (00/12Z) as appropriate.
- This combined approach reduces unnecessary hours while ensuring special local-day launches are included.

Tests & validation

- Unit test for timezone mapping fallback (lon→offset) and for zoneinfo-based conversion.
- Integration test: pick stations across multiple timezones (e.g., KOUN, KJAX, KSEA) and assert that the displayed soundings correspond to the station local date.
- Manual QA: verify at UTC boundary times (just after midnight UTC) that stations west of UTC show prior local-day soundings.

Todos (updated)

- todo: change-timestamps-window — implement reduced timestamp range generation; adapt per-station local-date window
- todo: iem-fast-path — run IEM queries in parallel with short timeout; return on first valid
- todo: spc-fallback — implement SPC directory -> {STN}.txt fetch + parser
- todo: uwyo-fallback — add UWYO CSV fetch & stationid cache
- todo: local-time-display — add timezone lookup, local-date filtering, and UI plumbing
- todo: parallel-or-first-success — coordinator to race sources/futures and cancel remaining
- todo: caching — add in-memory TTL cache for results and stationid mappings
- todo: metrics & logging — timing instrumentation for each source; expose for debugging
- todo: tests — unit tests for timezone conversion, SPC parser, UWYO CSV parser, and integration tests

Manual test plan (live)

- Test OUN 2026-04-13 12Z/20Z scenarios; verify IEM returns 12Z quickly and SPC returns 20Z; measure wall time and validate parsing into SoundingSet matches expected arrays.
- Test local-day display for stations in multiple timezones near UTC boundaries.

Notes and considerations

- SPC directory naming uses two-digit year prefix (e.g., 26041312_OBS). Careful date formatting required.
- SPC files are small and fast; parsing must tolerate -9999 placeholders.
- UWYO returns large HTML if requested without type=TEXT:CSV; always request CSV for programmatic fetch.
- Respect site politeness: add a small exponential backoff on repeated failures and consider rate limits if this fetcher is used in bulk.

When ready

Implement changes incrementally: update timestamp generation and per-station local-day window, then add the IEM fast path, then implement SPC parser/fallback, then UWYO fallback and caching. Run integration tests and a live test for OUN 12Z/20Z. Stop and review before coding if any approach preference differs from above.
