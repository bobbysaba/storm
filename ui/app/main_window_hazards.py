"""SPC outlooks, watches, MDs, NWS warnings, the legend and outlook text."""

import json
import threading
from PyQt6.QtCore import QSettings
from data.fetchers.hazard_fetcher import HazardFetcher


class MainWindowHazardsMixin:

    def _init_hazards(self):
        self._hazard_fetcher = HazardFetcher(parent=self)
        self.hazard_controls.spc_day_changed.connect(self._on_spc_day_changed)
        self.hazard_controls.spc_mode_changed.connect(self._on_spc_mode_changed)
        self.hazard_controls.spc_watches_toggled.connect(self._on_spc_watches_toggled)
        self.hazard_controls.spc_mds_toggled.connect(self._on_spc_mds_toggled)
        self.hazard_controls.nws_warnings_toggled.connect(self._on_nws_warnings_toggled)
        self.hazard_controls.nws_filter_changed.connect(self._on_nws_filter_changed)
        self.hazard_controls.cwa_toggled.connect(self._on_cwa_toggled)
        self.hazard_controls.fetch_requested.connect(self._on_hazard_fetch_requested)

        self._hazard_fetcher.spc_received.connect(self._on_spc_received)
        self._hazard_fetcher.nws_received.connect(self._on_nws_received)
        self._hazard_fetcher.nws_raw_phenoms_received.connect(self._on_nws_raw_phenoms)
        self._hazard_fetcher.spc_watches_received.connect(self._on_spc_watches_received)
        self._hazard_fetcher.spc_mds_received.connect(self._on_spc_mds_received)
        self._hazard_fetcher.fetch_error.connect(self._on_hazard_error)
        self._hazard_fetcher.connectivity_changed.connect(self._on_hazard_connectivity)

        self.map_widget.feature_clicked.connect(self._on_spc_feature_clicked)

        # seed NWS bbox from MBTiles domain extent so warnings are filtered
        try:
            import sqlite3 as _sqlite3
            from ui.map.widget import TILES_PATH as _TILES_PATH
            _conn = _sqlite3.connect(_TILES_PATH)
            _row = _conn.execute(
                "SELECT value FROM metadata WHERE name='bounds'"
            ).fetchone()
            _conn.close()
            if _row:
                _lon_min, _lat_min, _lon_max, _lat_max = (
                    float(x) for x in _row[0].split(",")
                )
                self._hazard_fetcher.set_nws_bbox(
                    _lon_min, _lat_min, _lon_max, _lat_max
                )
        except Exception:
            pass

        # load persisted NWS filter selections from QSettings and apply.
        try:
            s = QSettings("NSSL", "STORM")
            stored = s.value("nws/filters", None, type=str)
            # stored is comma-separated string or None
            if stored:
                codes = {c.strip().upper() for c in stored.split(",") if c.strip()}
            else:
                codes = None
            # programmatically set controls and emit handler so fetcher/font updates.
            self.hazard_controls.set_nws_filter_selected(codes, emit=True)
        except Exception:
            pass

        self._hazard_fetcher.start()

    def _on_hazard_error(self, msg: str):
        self._layer_status["hazards"].mark_error(msg)

    def _on_hazard_connectivity(self, online: bool):
        self.hazard_indicator.setVisible(not online)
        self._layout_overlays()

    def _on_spc_received(
        self,
        cat_str: str,
        wind_str: str,
        hail_str: str,
        tor_str: str,
        prob_str: str = '{"type":"FeatureCollection","features":[]}',
        sig_str: str = '{"type":"FeatureCollection","features":[]}',
    ):
        self._on_hazard_data_received()
        self.map_widget.set_spc_geojson(cat_str, wind_str, hail_str, tor_str, prob_str, sig_str)

    def _on_nws_raw_phenoms(self, phenoms: set):
        """Update the legend phenom set from the raw (unfiltered) NWS data.

        Keeping this separate from _on_nws_received ensures that toggling a
        phenom off in the legend doesn't remove its button — the user needs
        the button to be able to re-enable it.
        """
        self._nws_active_phenoms = phenoms or set()
        self._update_hazard_legend()

    def _on_nws_received(self, warnings_str: str):
        self._on_hazard_data_received()
        self.map_widget.set_nws_warnings_geojson(warnings_str)

    def _on_spc_watches_received(self, watches_str: str):
        self._on_hazard_data_received()
        self.map_widget.set_spc_watches_geojson(watches_str)

    def _on_spc_mds_received(self, mds_str: str):
        self._on_hazard_data_received()
        self.map_widget.set_spc_mds_geojson(mds_str)

    def _on_hazard_data_received(self):
        self._layer_status["hazards"].mark_success()
        txt = self.status_msg_label.text()
        if txt.startswith("Fetching ") or txt.startswith("Refreshing "):
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _on_hazard_fetch_requested(self):
        self.status_msg_label.setText("Refreshing hazards…")
        self._layout_overlays()
        self._hazard_fetcher.fetch_now()

    def _on_nws_filter_changed(self, codes: set[str] | None):
        """Apply user-selected NWS phenom filter (codes: set of uppercase phenom strings).

        Also persist selection to QSettings so it survives restarts.
        """
        if not hasattr(self, "_hazard_fetcher"):
            return
        # none or empty set => no filter (show all)
        self._hazard_fetcher.set_nws_filter(codes if codes else None)
        # persist to settings as comma-separated string (or empty to clear)
        try:
            s = QSettings("NSSL", "STORM")
            if codes:
                s.setValue("nws/filters", ",".join(sorted(codes)))
            else:
                s.remove("nws/filters")
        except Exception:
            pass
        # re-emit or fetch to update the map immediately.
        if self._hazard_fetcher.is_nws_fresh():
            self._hazard_fetcher.emit_cached_nws()
        else:
            self._hazard_fetcher.fetch_now()

    def _on_spc_mds_toggled(self, enabled: bool):
        self._set_layer_active("spc_mds", enabled)
        self._hazard_fetcher.set_spc_mds_enabled(enabled)
        self.map_widget.set_spc_mds_visible(enabled)
        if enabled:
            if self._hazard_fetcher.is_mds_fresh():
                self._hazard_fetcher.emit_cached_mds()
            else:
                self.status_msg_label.setText("Fetching SPC MDs…")
                self._layout_overlays()
                self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _update_hazard_legend(self):
        """Recompute which hazard layers are active and update the pill legend."""
        if not hasattr(self, "_hazard_fetcher"):
            # archive mode: derive active layers from hazard_controls button states.
            hc = self.hazard_controls
            active = []
            if hc._btn_outlook.isChecked():
                active.append("spc-cat")
            for k in ("tor", "wind", "hail", "prob", "sig"):
                btn = getattr(hc, f"_btn_{k}")
                if btn.isChecked():
                    active.append(f"spc-{k}")
            if hc._btn_watches.isChecked():
                active.append("spc-watches")
            if hc._btn_nws_warnings.isChecked():
                active.append("nws-warnings")
            hc.update_legend(active, nws_phenoms=self._nws_active_phenoms)
            self._request_layout()
            return
        fc = self._hazard_fetcher
        active = []
        if any(fc._spc_categories.values()):
            active.append("spc-cat")
        for k in ("tor", "wind", "hail", "prob", "sig"):
            if fc._spc_products.get(k):
                active.append(f"spc-{k}")
        if fc._spc_watches_enabled:
            active.append("spc-watches")
        if fc._spc_mds_enabled:
            active.append("spc-mds")
        if fc._nws_enabled:
            active.append("nws-warnings")
        self.hazard_controls.update_legend(active, nws_phenoms=self._nws_active_phenoms)
        self._request_layout()

    def _on_spc_feature_clicked(self, payload: str):
        """Handle a click on one or more overlapping hazard polygons."""
        import re as _re
        try:
            raw = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return

        # js sends a list of all unique features under the click point.
        items = raw if isinstance(raw, list) else [raw]

        fetch_targets: list[tuple[str, str, str | None]] = []  # (title, kind, identifier)
        for data in items:
            source = data.get("source", "")
            props = data.get("properties", {})

            # for archive mode, encode the current archive time into identifiers
            _archive_ts = (
                self._time_ctrl.current_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                if self._archive and hasattr(self, "_time_ctrl") else None
            )

            if source in ("spc-cat", "spc-tor", "spc-wind", "spc-hail", "spc-prob", "spc-sig"):
                day = int(props.get("spc_day") or getattr(self.hazard_controls, "current_spc_day", lambda: 1)())
                title = "DAY 4-8 CONVECTIVE OUTLOOK" if day >= 4 else f"DAY {day} CONVECTIVE OUTLOOK"
                ident = f"{day}|{_archive_ts}" if _archive_ts else str(day)
                fetch_targets.append((title, "swo", ident))
            elif source == "spc-mds":
                name = str(props.get("name", "")).strip()
                _m = _re.search(r'\d+', name)
                num = _m.group().zfill(4) if _m else "0000"
                fetch_targets.append((f"MESOSCALE DISCUSSION {num}", "mcd", num))
            elif source == "spc-watches":
                watch_num = str(props.get("watch_num", "")).strip()
                if not watch_num:
                    continue
                event_label = str(props.get("event", "Watch")).upper()
                # append archive timestamp so text fetch targets the correct date.
                ident = f"{watch_num}|{_archive_ts}" if _archive_ts else watch_num
                fetch_targets.append((f"{event_label} {watch_num}", "watch", ident))
            elif source == "nws-warnings":
                warning_url = str(props.get("warning_url", "")).strip()
                if not warning_url:
                    continue
                prod_type = str(props.get("prod_type", "Warning")).title()
                wfo = str(props.get("wfo", "")).strip()
                title = f"{prod_type} — {wfo}" if wfo else prod_type
                fetch_targets.append((title, "warning", warning_url))

        if not fetch_targets:
            return

        self._fetch_generation += 1
        gen = self._fetch_generation
        self.outlook_panel.show_loading([t[0] for t in fetch_targets])
        self._layout_overlays()
        for title, kind, identifier in fetch_targets:
            threading.Thread(
                target=self._fetch_outlook_text,
                args=(gen, title, kind, identifier),
                daemon=True,
            ).start()

    def _fetch_outlook_text(self, generation: int, title: str, kind: str, identifier: str | None):
        """Fetch SPC discussion text in a background thread.

        Sources:
          SPC Outlooks: IEM Mesonet AFOS API (PIL: SWODY1/SWODY2/SWODY3/SWOD48)
          MDs:           SPC direct .txt URL    (https://www.spc.noaa.gov/products/md/md{nnnn}.txt)

        IEM rejects the SPCMCD{nnnn} PIL as too long, so MDs are fetched
        directly from SPC's own text product archive instead.
        """
        from urllib.request import Request, urlopen

        HEADERS = {
            "User-Agent": "STORM/1.0 (contact: support)",
            "Accept": "application/geo+json, application/ld+json, application/json, text/plain",
        }

        def _fetch(url: str) -> str:
            import time as _time
            last_exc = None
            for attempt in range(3):
                if attempt:
                    _time.sleep(0.8)
                try:
                    req = Request(url, headers=HEADERS)
                    with urlopen(req, timeout=15) as resp:
                        raw = resp.read().decode("utf-8", errors="replace")
                    return raw.strip("\x01\x02\x03\r\n").strip()
                except Exception as _e:
                    last_exc = _e
            raise last_exc

        try:
            if kind == "swo":
                day = 1
                archive_identifier = None
                if identifier:
                    parts = str(identifier).split("|", 1)
                    try:
                        day = int(parts[0])
                    except (TypeError, ValueError):
                        day = 1
                    archive_identifier = parts[1] if len(parts) > 1 else None
                pil = "SWOD48" if day >= 4 else f"SWODY{max(1, min(3, day))}"
                # archive_identifier is an ISO archive timestamp in archive mode.
                if archive_identifier:
                    from datetime import datetime as _dt2, timedelta as _td2
                    ts = _dt2.strptime(archive_identifier, "%Y-%m-%dT%H:%M:%SZ")
                    # iem AFOS ignores after/before in ISO format; sdate/edate (date-only)
                    sdate = ts.strftime("%Y-%m-%d")
                    edate = (ts + _td2(days=1)).strftime("%Y-%m-%d")
                    url = (
                        "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py"
                        f"?pil={pil}&limit=1&fmt=text&sdate={sdate}&edate={edate}"
                    )
                else:
                    url = f"https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?pil={pil}&limit=1&fmt=text"
                text = _fetch(url)
            elif kind == "mcd":
                url = f"https://www.spc.noaa.gov/products/md/md{identifier}.txt"
                text = _fetch(url)
            elif kind == "watch":
                # identifier is "NNNN" in live mode or "NNNN|ISO_TS" in archive mode.
                watch_num = identifier
                archive_ts = None
                if identifier and "|" in identifier:
                    watch_num, archive_ts = identifier.split("|", 1)
                sel_digit = str(int(watch_num) % 10)
                if archive_ts:
                    from datetime import datetime as _dt2, timedelta as _td2
                    ts = _dt2.strptime(archive_ts, "%Y-%m-%dT%H:%M:%SZ")
                    sdate = (ts - _td2(days=1)).strftime("%Y-%m-%d")
                    edate = (ts + _td2(days=1)).strftime("%Y-%m-%d")
                    url = (
                        "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py"
                        f"?pil=SEL{sel_digit}&limit=1&fmt=text&sdate={sdate}&edate={edate}"
                    )
                else:
                    url = f"https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?pil=SEL{sel_digit}&limit=1&fmt=text"
                text = _fetch(url)
            elif kind == "warning":
                if not identifier:
                    text = "(No warning URL available)"
                elif self._archive:
                    # archive mode: identifier is the IEM VTEC viewer URL with query params:
                    from urllib.parse import urlparse as _urlparse, parse_qs as _parse_qs
                    import json as _json
                    _p = _urlparse(identifier)
                    _q = _parse_qs(_p.query)
                    year     = (_q.get("year",         [None])[0])
                    wfo      = (_q.get("wfo",           [None])[0])
                    phenom   = (_q.get("phenomena",     [None])[0])
                    sig      = (_q.get("significance",  [None])[0])
                    etn_raw  = (_q.get("eventid",       [None])[0])
                    if year and wfo and phenom and sig and etn_raw:
                        etn_int = int(etn_raw.lstrip("0") or "0")
                        api_url = (
                            "https://mesonet.agron.iastate.edu/json/vtec_event.py"
                            f"?wfo={wfo}&year={year}&phenomena={phenom}"
                            f"&significance={sig}&etn={etn_int}"
                        )
                        raw = _fetch(api_url)
                        data = _json.loads(raw)
                        if data.get("event_exists"):
                            text = (data.get("report") or {}).get("text", "") or "(No text in archive)"
                        else:
                            text = "(Warning event not found in IEM archive)"
                    else:
                        text = "(Could not parse VTEC parameters from warning URL)"
                else:
                    import json as _json
                    raw = _fetch(identifier)
                    try:
                        raw_json = _json.loads(raw)
                        wp = raw_json.get("properties", {})
                        headline = wp.get("headline", "")
                        description = wp.get("description", "")
                        instruction = wp.get("instruction", "")
                        text = "\n\n".join(x for x in [headline, description, instruction] if x)
                    except _json.JSONDecodeError:
                        text = "(Warning text unavailable)"
            else:
                text = ""
            if not text:
                text = "(No discussion text found)"
        except Exception as exc:
            text = f"Failed to load discussion:\n{exc}"

        self._panel_text_ready.emit(generation, title, text)

    def _on_panel_text_ready(self, generation: int, title: str, text: str):
        if generation == self._fetch_generation:
            self.outlook_panel.show_text(title, text)

    def _on_spc_day_changed(self, day: int):
        self._hazard_fetcher.set_spc_day(day)

    def _on_spc_mode_changed(self, mode: str):
        self._set_layer_active("spc_outlook", mode == "outlook")
        self._set_layer_active("spc_tor",     mode == "tor")
        self._set_layer_active("spc_wind",    mode == "wind")
        self._set_layer_active("spc_hail",    mode == "hail")
        self._set_layer_active("spc_prob",    mode == "prob")
        self._set_layer_active("spc_sig",     mode == "sig")
        outlook_on = mode == "outlook"
        for key in ("MRGL", "SLGHT", "ENH", "MDT", "HIGH"):
            self._hazard_fetcher.set_spc_category_enabled(key, outlook_on)
            self.map_widget.set_spc_category_visible(key, outlook_on)

        for key in ("tor", "wind", "hail", "prob", "sig"):
            on = mode == key
            self._hazard_fetcher.set_spc_product_enabled(key, on)
            self.map_widget.set_spc_product_visible(key, on)

        if mode:
            _spc_labels = {
                "outlook": "SPC outlook", "tor": "SPC tornado",
                "wind": "SPC wind", "hail": "SPC hail",
                "prob": "SPC probability", "sig": "SPC significant",
            }
            _fetch_label = f"Fetching {_spc_labels.get(mode, 'SPC')}…"
            needs_refresh = False
            if mode == "outlook":
                needs_refresh = not self._hazard_fetcher.spc_category_cached()
            elif mode in ("tor", "wind", "hail", "prob", "sig"):
                needs_refresh = not self._hazard_fetcher.spc_product_cached(mode)
            if needs_refresh:
                self._hazard_fetcher.force_spc_refresh()
                self.status_msg_label.setText(_fetch_label)
                self._layout_overlays()
                self._hazard_fetcher.fetch_now()
            elif self._hazard_fetcher.is_spc_fresh():
                self._hazard_fetcher.emit_cached_spc()
            else:
                self.status_msg_label.setText(_fetch_label)
                self._layout_overlays()
                self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _on_spc_watches_toggled(self, enabled: bool):
        self._set_layer_active("spc_watches", enabled)
        self._hazard_fetcher.set_spc_watches_enabled(enabled)
        self.map_widget.set_spc_watches_visible(enabled)
        if enabled:
            if self._hazard_fetcher.is_watches_fresh():
                self._hazard_fetcher.emit_cached_watches()
            else:
                self.status_msg_label.setText("Fetching SPC watches…")
                self._layout_overlays()
                self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _on_nws_warnings_toggled(self, enabled: bool):
        self._set_layer_active("nws_warnings", enabled)
        self._hazard_fetcher.set_nws_enabled(enabled)
        self.map_widget.set_nws_warnings_visible(enabled)
        if enabled:
            if self._hazard_fetcher.is_nws_fresh():
                self._hazard_fetcher.emit_cached_nws()
            else:
                self.status_msg_label.setText("Fetching NWS warnings…")
                self._layout_overlays()
                self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _on_cwa_toggled(self, enabled: bool):
        """Toggle the CWA overlay; the map loads the static GeoJSON on first show."""
        self._set_layer_active("cwa", enabled)
        self.map_widget.set_cwa_visible(enabled)
