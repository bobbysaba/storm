# ui/routing_controls.py
# Collapsible toolbar drawer for turn-by-turn directions.
#
# Origin is always an editable QLineEdit — auto-fills with GPS when available,
# or the user can type an address / lat,lon, or pick a point on the map.
# Destination works the same way.  Both fields can be set without a GPS fix,
# making the drawer fully usable in monitor mode or with GPS outages.

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QToolButton,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QSizePolicy,
    QApplication,
)
from math import radians, sin, cos, sqrt, atan2

from PyQt6.QtCore import pyqtSignal, QPropertyAnimation, QEasingCurve, Qt, QTimer

from data.routing_fetcher import RoutingFetcher, RouteResult, fmt_distance, fmt_duration

# Remove a completed step from the list when the vehicle is within this distance
# of the next maneuver point.  300 m gives ~10 s of warning at highway speeds.
_ADVANCE_M = 300.0


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate great-circle distance in metres."""
    R = 6_371_000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))

# Text-colour styles applied inline on the origin QLineEdit
_STYLE_GPS    = "color: #39D98A;"   # green  — GPS-tracked position
_STYLE_MANUAL = "color: #EFF3FF;"   # white  — manually entered / picked


class RoutingControls(QWidget):
    """Floating drawer for route calculation and turn-by-turn display.

    Signals:
        enter_pick_mode()              — either pick button was activated
        cancel_pick_mode()             — pick mode was cancelled
        route_calculated(RouteResult)  — a route was successfully fetched
        route_cleared()                — user cleared the route
    """

    enter_pick_mode   = pyqtSignal()
    cancel_pick_mode  = pyqtSignal()
    route_calculated  = pyqtSignal(object)   # RouteResult
    route_cleared     = pyqtSignal()
    content_resized   = pyqtSignal()         # triggers layout pulse in main_window
    nav_updated       = pyqtSignal(str, str) # (step_text, summary_text) → NavPill

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation         = None
        self._fetcher           = RoutingFetcher()

        # Last GPS fix received (may differ from routing origin when overridden)
        self._gps_lat: float | None = None
        self._gps_lon: float | None = None

        # Resolved coordinates used for routing
        self._own_lat: float | None = None
        self._own_lon: float | None = None
        self._dest_lat: float | None = None
        self._dest_lon: float | None = None

        self._origin_manual     = False   # True while user has overridden GPS
        self._geocoding_origin  = False
        self._geocoding_dest    = False
        self._last_result: RouteResult | None = None
        self._route_steps: list = []      # full step list with location data
        self._first_unfinished  = 0       # index of the step at top of list

        # "origin" while picking start, "dest" while picking destination, "" = idle
        self._pick_mode: str = ""

        self._setup_ui()
        self._connect_fetcher()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(0)

        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("routingDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        # ── Origin row ────────────────────────────────────────────────────────
        origin_row = QWidget()
        ori = QHBoxLayout(origin_row)
        ori.setContentsMargins(0, 0, 0, 0)
        ori.setSpacing(4)

        ori_lbl = QLabel("FROM")
        ori_lbl.setFixedWidth(36)
        ori_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        ori_lbl.setStyleSheet("color: #6E7A8F; font-size: 9px; font-weight: 700; "
                              "letter-spacing: 0.8px; background: transparent;")

        # Always-visible editable field — GPS auto-fills it (green), manual overrides it (white)
        self._origin_input = QLineEdit()
        self._origin_input.setPlaceholderText("Address, lat,lon, or pick on map…")
        self._origin_input.returnPressed.connect(self._on_origin_enter)
        self._origin_input.textEdited.connect(self._on_origin_text_edited)
        self._origin_input.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        # Map-pick button for origin
        self._btn_origin_pick = QToolButton()
        self._btn_origin_pick.setText("⊕")
        self._btn_origin_pick.setToolTip("Click on map to set start point")
        self._btn_origin_pick.setCheckable(True)
        self._btn_origin_pick.setFixedSize(24, 24)
        self._btn_origin_pick.toggled.connect(self._on_origin_pick_toggled)

        # GPS-reset button — appears when user has overridden GPS and a fix is available
        self._btn_origin_gps = QToolButton()
        self._btn_origin_gps.setText("GPS")
        self._btn_origin_gps.setToolTip("Revert to GPS position")
        self._btn_origin_gps.setFixedSize(36, 24)
        self._btn_origin_gps.setVisible(False)
        self._btn_origin_gps.clicked.connect(self._reset_to_gps_origin)

        ori.addWidget(ori_lbl)
        ori.addWidget(self._origin_input)
        ori.addWidget(self._btn_origin_pick)
        ori.addWidget(self._btn_origin_gps)
        col.addWidget(origin_row)

        # ── Destination row ───────────────────────────────────────────────────
        dest_row = QWidget()
        dst = QHBoxLayout(dest_row)
        dst.setContentsMargins(0, 0, 0, 0)
        dst.setSpacing(4)

        dest_lbl = QLabel("TO")
        dest_lbl.setFixedWidth(36)
        dest_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        dest_lbl.setStyleSheet("color: #6E7A8F; font-size: 9px; font-weight: 700; "
                               "letter-spacing: 0.8px; background: transparent;")

        self._dest_input = QLineEdit()
        self._dest_input.setPlaceholderText("Address or city…")
        self._dest_input.returnPressed.connect(self._on_dest_enter)
        self._dest_input.textEdited.connect(self._on_dest_text_edited)
        self._dest_input.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        self._btn_dest_pick = QToolButton()
        self._btn_dest_pick.setText("⊕")
        self._btn_dest_pick.setToolTip("Click on map to set destination")
        self._btn_dest_pick.setCheckable(True)
        self._btn_dest_pick.setFixedSize(24, 24)
        self._btn_dest_pick.toggled.connect(self._on_dest_pick_toggled)

        dst.addWidget(dest_lbl)
        dst.addWidget(self._dest_input)
        dst.addWidget(self._btn_dest_pick)
        col.addWidget(dest_row)

        # ── Action row ────────────────────────────────────────────────────────
        action_row = QWidget()
        act = QHBoxLayout(action_row)
        act.setContentsMargins(0, 0, 0, 0)
        act.setSpacing(4)

        self._btn_go = QToolButton()
        self._btn_go.setText("GET DIRECTIONS")
        self._btn_go.setEnabled(False)
        self._btn_go.clicked.connect(self._on_get_directions)

        self._btn_clear = QToolButton()
        self._btn_clear.setText("CLEAR")
        self._btn_clear.setEnabled(False)
        self._btn_clear.clicked.connect(self._on_clear)

        # Copy ETA — useful for monitor operators to paste into comms
        self._btn_copy_eta = QToolButton()
        self._btn_copy_eta.setText("COPY ETA")
        self._btn_copy_eta.setToolTip("Copy route summary to clipboard")
        self._btn_copy_eta.setVisible(False)
        self._btn_copy_eta.clicked.connect(self._on_copy_eta)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(
            "color: #9DA6B8; font-size: 10px; background: transparent; padding: 0 4px;"
        )
        self._status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        act.addWidget(self._btn_go)
        act.addWidget(self._btn_clear)
        act.addWidget(self._btn_copy_eta)
        act.addWidget(self._status_label)
        act.addStretch(1)
        col.addWidget(action_row)

        # ── Summary bar ───────────────────────────────────────────────────────
        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet(
            "color: #4A9EFF; font-size: 11px; font-weight: 600; "
            "background: transparent; padding: 1px 40px 1px 40px;"
        )
        self._summary_label.setVisible(False)
        col.addWidget(self._summary_label)

        # ── Turn steps list ───────────────────────────────────────────────────
        self._steps_list = QListWidget()
        self._steps_list.setStyleSheet(
            "QListWidget { background: transparent; border: none; "
            "  padding: 0 0 0 36px; }"
            "QListWidget::item { color: #C8D0DE; font-size: 11px; "
            "  padding: 2px 2px 2px 0; background: transparent; border: none; }"
            "QListWidget::item:selected { background: rgba(74,158,255,0.12); "
            "  border-radius: 4px; }"
        )
        self._steps_list.setMaximumHeight(180)
        self._steps_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._steps_list.setVisible(False)
        col.addWidget(self._steps_list)

        outer.addWidget(self._drawer)

    def _connect_fetcher(self):
        self._fetcher.route_ready.connect(self._on_route_ready)
        self._fetcher.geocode_ready.connect(self._on_geocode_ready)
        self._fetcher.fetch_error.connect(self._on_fetch_error)

    # ── Collapse / expand ──────────────────────────────────────────────────────

    def toggle_drawer(self, checked: bool):
        if checked:
            self.setMaximumHeight(16777215)
            target = self.sizeHint().height()
            self.setMaximumHeight(0)
            current = 0
        else:
            current = self.height()
            self.setMaximumHeight(current)
            target = 0

        if self._animation:
            self._animation.stop()
        anim = QPropertyAnimation(self, b"maximumHeight")
        anim.setDuration(180)
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        if checked:
            anim.finished.connect(lambda: self.setMaximumHeight(16777215))
        anim.start()
        self._animation = anim

    # ── Public API ─────────────────────────────────────────────────────────────

    def update_own_position(self, lat: float, lon: float):
        """Called by MainWindow whenever the own vehicle's GPS position updates."""
        self._gps_lat = lat
        self._gps_lon = lon
        if not self._origin_manual:
            self._own_lat = lat
            self._own_lon = lon
            self._origin_input.setText(f"My Vehicle  ({lat:.4f}, {lon:.4f})")
            self._origin_input.setStyleSheet(_STYLE_GPS)
            self._update_go_enabled()
        else:
            # GPS is now available as a fallback — offer the reset button
            self._btn_origin_gps.setVisible(True)
        self._check_step_advance(lat, lon)

    def on_map_pick(self, lat: float, lon: float):
        """Called by MainWindow when the user clicks the map in pick mode.
        Routes to origin or destination depending on which pick is active."""
        if self._pick_mode == "origin":
            self._pick_mode = ""
            self._origin_manual = True
            self._own_lat = lat
            self._own_lon = lon
            self._origin_input.setText(f"{lat:.5f}, {lon:.5f}")
            self._origin_input.setStyleSheet(_STYLE_MANUAL)
            self._btn_origin_pick.blockSignals(True)
            self._btn_origin_pick.setChecked(False)
            self._btn_origin_pick.blockSignals(False)
            if self._gps_lat is not None:
                self._btn_origin_gps.setVisible(True)
            self._update_go_enabled()
            if self._have_dest():
                self._on_get_directions()

        elif self._pick_mode == "dest":
            self._pick_mode = ""
            self._dest_lat = lat
            self._dest_lon = lon
            self._dest_input.setText(f"{lat:.5f}, {lon:.5f}")
            self._btn_dest_pick.blockSignals(True)
            self._btn_dest_pick.setChecked(False)
            self._btn_dest_pick.blockSignals(False)
            self._update_go_enabled()
            if self._have_origin_text():
                self._on_get_directions()

    # ── Origin helpers ─────────────────────────────────────────────────────────

    def _on_origin_text_edited(self):
        """User manually typed in the origin field — switch to manual mode."""
        self._origin_manual = True
        self._own_lat = None
        self._own_lon = None
        self._origin_input.setStyleSheet(_STYLE_MANUAL)
        self._btn_origin_gps.setVisible(self._gps_lat is not None)
        self._update_go_enabled()

    def _on_origin_enter(self):
        text = self._origin_input.text().strip()
        if not text:
            return
        # Try "lat, lon" parse first
        try:
            parts = [p.strip() for p in text.split(",")]
            if len(parts) == 2:
                lat, lon = float(parts[0]), float(parts[1])
                self._apply_resolved_origin(text, lat, lon)
                return
        except ValueError:
            pass
        # Otherwise geocode
        self._set_status("Geocoding start…")
        self._geocoding_origin = True
        self._fetcher.geocode(text, is_origin=True)

    def _apply_resolved_origin(self, display: str, lat: float, lon: float):
        self._own_lat = lat
        self._own_lon = lon
        short = display[:50] + ("…" if len(display) > 50 else "")
        self._origin_input.blockSignals(True)
        self._origin_input.setText(short)
        self._origin_input.blockSignals(False)
        self._set_status("")
        self._update_go_enabled()

    def _reset_to_gps_origin(self):
        self._origin_manual = False
        self._own_lat = self._gps_lat
        self._own_lon = self._gps_lon
        self._btn_origin_gps.setVisible(False)
        if self._gps_lat is not None:
            self._origin_input.blockSignals(True)
            self._origin_input.setText(
                f"My Vehicle  ({self._gps_lat:.4f}, {self._gps_lon:.4f})"
            )
            self._origin_input.setStyleSheet(_STYLE_GPS)
            self._origin_input.blockSignals(False)
        else:
            self._origin_input.clear()
            self._origin_input.setStyleSheet(_STYLE_MANUAL)
        self._update_go_enabled()

    # ── Destination helpers ────────────────────────────────────────────────────

    def _on_dest_text_edited(self):
        """User manually typed in the destination field — clear resolved coords."""
        self._dest_lat = None
        self._dest_lon = None
        self._update_go_enabled()

    def _on_dest_enter(self):
        self._dest_lat = None
        self._dest_lon = None
        self._update_go_enabled()
        if self._dest_input.text().strip():
            self._on_get_directions()

    # ── Pick mode ──────────────────────────────────────────────────────────────

    def _on_origin_pick_toggled(self, checked: bool):
        if checked:
            # Cancel dest pick if active
            if self._pick_mode == "dest":
                self._btn_dest_pick.blockSignals(True)
                self._btn_dest_pick.setChecked(False)
                self._btn_dest_pick.blockSignals(False)
            self._pick_mode = "origin"
            self.enter_pick_mode.emit()
        else:
            if self._pick_mode == "origin":
                self._pick_mode = ""
                self.cancel_pick_mode.emit()

    def _on_dest_pick_toggled(self, checked: bool):
        if checked:
            # Cancel origin pick if active
            if self._pick_mode == "origin":
                self._btn_origin_pick.blockSignals(True)
                self._btn_origin_pick.setChecked(False)
                self._btn_origin_pick.blockSignals(False)
            self._pick_mode = "dest"
            self.enter_pick_mode.emit()
        else:
            if self._pick_mode == "dest":
                self._pick_mode = ""
                self.cancel_pick_mode.emit()

    # ── Route actions ──────────────────────────────────────────────────────────

    def _have_origin_text(self) -> bool:
        return bool(self._origin_input.text().strip())

    def _have_origin(self) -> bool:
        return self._own_lat is not None and self._own_lon is not None

    def _have_dest(self) -> bool:
        return (self._dest_lat is not None) or bool(self._dest_input.text().strip())

    def _update_go_enabled(self):
        has_origin = self._have_origin() or self._have_origin_text()
        has_dest   = self._have_dest()
        self._btn_go.setEnabled(has_origin and has_dest)

    def _on_get_directions(self):
        # ── Step 1: resolve origin ──
        if not self._have_origin():
            origin_text = self._origin_input.text().strip()
            if not origin_text:
                self._set_status("Enter a starting point")
                return
            # Try lat,lon parse
            try:
                parts = [p.strip() for p in origin_text.split(",")]
                if len(parts) == 2:
                    lat, lon = float(parts[0]), float(parts[1])
                    self._apply_resolved_origin(origin_text, lat, lon)
                    self._on_get_directions()   # recurse now that origin is resolved
                    return
            except ValueError:
                pass
            # Geocode origin; route will continue in _on_geocode_ready
            self._set_status("Geocoding start…")
            self._btn_go.setEnabled(False)
            self._geocoding_origin = True
            self._fetcher.geocode(origin_text, is_origin=True)
            return

        # ── Step 2: resolve destination ──
        address = self._dest_input.text().strip()
        if not address and self._dest_lat is None:
            self._set_status("Enter a destination")
            return

        if self._dest_lat is not None:
            self._start_route_fetch()
        else:
            self._set_status("Geocoding destination…")
            self._btn_go.setEnabled(False)
            self._geocoding_dest = True
            self._fetcher.geocode(address, is_origin=False)

    def _start_route_fetch(self):
        self._set_status("Fetching route…")
        self._btn_go.setEnabled(False)
        self._fetcher.fetch_route(
            self._own_lat, self._own_lon,
            self._dest_lat, self._dest_lon,
        )

    # ── Fetcher callbacks ──────────────────────────────────────────────────────

    def _on_geocode_ready(self, name: str, lat: float, lon: float, is_origin: bool):
        if is_origin and self._geocoding_origin:
            self._geocoding_origin = False
            self._apply_resolved_origin(name, lat, lon)
            # Continue: resolve destination then fetch
            if self._dest_lat is not None:
                self._start_route_fetch()
            else:
                dest_text = self._dest_input.text().strip()
                if dest_text:
                    self._set_status("Geocoding destination…")
                    self._geocoding_dest = True
                    self._fetcher.geocode(dest_text, is_origin=False)
        elif not is_origin and self._geocoding_dest:
            self._geocoding_dest = False
            self._dest_lat = lat
            self._dest_lon = lon
            self._dest_input.blockSignals(True)
            self._dest_input.setText(name[:60] + ("…" if len(name) > 60 else ""))
            self._dest_input.blockSignals(False)
            self._start_route_fetch()

    def _on_route_ready(self, result: RouteResult):
        self._last_result = result
        self._btn_go.setEnabled(True)
        self._set_status("")
        self._btn_clear.setEnabled(True)
        self._btn_copy_eta.setVisible(True)
        self._populate_steps(result)
        self.route_calculated.emit(result)
        self._emit_nav_state()
        self._resize_for_content()

    def _on_fetch_error(self, msg: str):
        self._geocoding_origin = False
        self._geocoding_dest   = False
        self._update_go_enabled()
        self._set_status(msg[:60])

    def _on_clear(self):
        self._dest_lat         = None
        self._dest_lon         = None
        self._last_result      = None
        self._route_steps      = []
        self._first_unfinished = 0
        self._dest_input.clear()
        self._summary_label.setVisible(False)
        self._steps_list.clear()
        self._steps_list.setVisible(False)
        self._btn_clear.setEnabled(False)
        self._btn_copy_eta.setVisible(False)
        # Cancel any active pick
        for btn in (self._btn_origin_pick, self._btn_dest_pick):
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)
        self._pick_mode = ""
        self._set_status("")
        self._update_go_enabled()
        self.route_cleared.emit()
        # Only animate the height change when the drawer is fully open.
        # If maximumHeight is mid-animation (closing), don't interfere — the
        # close animation is already running.  If it's 0 (hidden), there's
        # nothing to resize.  In both cases emit content_resized so the
        # layout pulse still runs and corrects geometry via _stack.
        if self.maximumHeight() == 16777215:
            self._resize_for_content()
        else:
            self.content_resized.emit()

    # ── Display ────────────────────────────────────────────────────────────────

    def _populate_steps(self, result: RouteResult):
        self._route_steps     = list(result.steps)
        self._first_unfinished = 0
        self._steps_list.clear()
        for step in result.steps:
            dist_str = fmt_distance(step.distance_m) if step.distance_m > 0 else ""
            text     = f"{step.icon}  {step.instruction}"
            if dist_str:
                text += f"  —  {dist_str}"
            self._steps_list.addItem(QListWidgetItem(text))

        total_dist = fmt_distance(result.distance_m)
        total_time = fmt_duration(result.duration_s)
        self._summary_label.setText(f"{total_dist}  ·  {total_time}")
        self._summary_label.setVisible(True)
        self._steps_list.setVisible(True)

    def _check_step_advance(self, lat: float, lon: float):
        """Remove completed steps as the vehicle approaches each maneuver point."""
        # Need at least two steps — never drop the final "Arrive" step
        if self._steps_list.count() <= 1 or not self._route_steps:
            return

        # The next maneuver point is the start of step (first_unfinished + 1)
        next_idx = self._first_unfinished + 1
        if next_idx >= len(self._route_steps):
            return

        next_loc = self._route_steps[next_idx].location
        if _haversine_m(lat, lon, next_loc[0], next_loc[1]) < _ADVANCE_M:
            self._steps_list.takeItem(0)
            self._first_unfinished += 1
            self._emit_nav_state()
            self._resize_for_content()

    def _on_copy_eta(self):
        if self._last_result is None:
            return
        dest = self._dest_input.text().strip()
        if not dest and self._dest_lat is not None:
            dest = f"{self._dest_lat:.4f}, {self._dest_lon:.4f}"
        summary  = self._summary_label.text()
        clip_text = f"Route to {dest}: {summary}" if dest else summary
        QApplication.clipboard().setText(clip_text)
        self._set_status("Copied!")
        QTimer.singleShot(2000, lambda: self._set_status(""))

    def _set_status(self, msg: str):
        self._status_label.setText(msg)

    def _emit_nav_state(self):
        """Push current step + summary to the NavPill via nav_updated signal."""
        if self._steps_list.count() == 0 or not self._summary_label.isVisible():
            return
        item = self._steps_list.item(0)
        step_text = item.text() if item else "Navigating…"
        self.nav_updated.emit(step_text, self._summary_label.text())

    def _resize_for_content(self):
        """Animate to new natural height after content changes."""
        if self.maximumHeight() <= 0:
            return
        self.setMaximumHeight(16777215)
        target  = self.sizeHint().height()
        current = self.height()
        if target != current:
            if self._animation:
                self._animation.stop()
            anim = QPropertyAnimation(self, b"maximumHeight")
            anim.setDuration(150)
            anim.setStartValue(current)
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
            anim.finished.connect(lambda: self.setMaximumHeight(16777215))
            anim.start()
            self._animation = anim
        # Always notify so _start_layout_pulse runs and _stack corrects geometry,
        # even when no animation is needed (e.g. adjustSize already handled it).
        self.content_resized.emit()
