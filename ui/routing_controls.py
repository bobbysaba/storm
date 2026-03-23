# ui/routing_controls.py
# Collapsible toolbar drawer for turn-by-turn directions.
#
# Origin is auto-populated from the own vehicle's GPS position when available.
# Destination can be typed as an address or picked by clicking the map.

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QFrame, QToolButton,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal, QPropertyAnimation, QEasingCurve, Qt
from PyQt6.QtGui import QFont

from data.routing_fetcher import RoutingFetcher, RouteResult, fmt_distance, fmt_duration


class RoutingControls(QWidget):
    """Floating drawer for route calculation and turn-by-turn display.

    Signals:
        enter_pick_mode()              — user clicked "Pick on Map"
        route_calculated(RouteResult)  — a route was successfully fetched
        route_cleared()                — user cleared the route
    """

    enter_pick_mode   = pyqtSignal()
    route_calculated  = pyqtSignal(object)   # RouteResult
    route_cleared     = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation      = None
        self._fetcher        = RoutingFetcher()
        self._own_lat: float | None = None
        self._own_lon: float | None = None
        self._dest_lat: float | None = None
        self._dest_lon: float | None = None
        self._pick_pending   = False
        self._geocoding_dest = False
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

        self._origin_display = QLabel("No GPS fix")
        self._origin_display.setStyleSheet(
            "color: #6E7A8F; font-size: 11px; background: transparent; padding: 0 4px;"
        )
        self._origin_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        ori.addWidget(ori_lbl)
        ori.addWidget(self._origin_display)
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
        self._dest_input.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        self._btn_pick = QToolButton()
        self._btn_pick.setText("⊕")
        self._btn_pick.setToolTip("Click on map to set destination")
        self._btn_pick.setCheckable(True)
        self._btn_pick.toggled.connect(self._on_pick_toggled)

        dst.addWidget(dest_lbl)
        dst.addWidget(self._dest_input)
        dst.addWidget(self._btn_pick)
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

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(
            "color: #9DA6B8; font-size: 10px; background: transparent; padding: 0 4px;"
        )
        self._status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        act.addWidget(self._btn_go)
        act.addWidget(self._btn_clear)
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
        """Called by MainWindow whenever the own vehicle's position updates."""
        self._own_lat = lat
        self._own_lon = lon
        self._origin_display.setText(f"My Vehicle  ({lat:.4f}, {lon:.4f})")
        self._origin_display.setStyleSheet(
            "color: #39D98A; font-size: 11px; background: transparent; padding: 0 4px;"
        )
        self._update_go_enabled()

    def on_destination_picked(self, lat: float, lon: float):
        """Called by MapWidget when the user clicks the map in pick mode."""
        self._dest_lat = lat
        self._dest_lon = lon
        self._dest_input.setText(f"{lat:.5f}, {lon:.5f}")
        self._btn_pick.blockSignals(True)
        self._btn_pick.setChecked(False)
        self._btn_pick.blockSignals(False)
        self._pick_pending = False
        self._update_go_enabled()
        # Auto-fetch as soon as a point is picked
        if self._own_lat is not None:
            self._start_route_fetch()

    # ── Internal ───────────────────────────────────────────────────────────────

    def _update_go_enabled(self):
        has_origin = self._own_lat is not None
        has_dest   = (self._dest_lat is not None) or bool(self._dest_input.text().strip())
        self._btn_go.setEnabled(has_origin and has_dest)

    def _on_dest_enter(self):
        self._dest_lat = None
        self._dest_lon = None
        self._update_go_enabled()
        if self._own_lat is not None and self._dest_input.text().strip():
            self._on_get_directions()

    def _on_pick_toggled(self, checked: bool):
        self._pick_pending = checked
        if checked:
            self.enter_pick_mode.emit()
        # If unchecked by the user (not by on_destination_picked), cancel pick mode
        # by emitting enter_pick_mode(False) — handled in main_window via set_route_pick_mode

    def _on_get_directions(self):
        if self._own_lat is None:
            self._set_status("No GPS fix — cannot route")
            return
        address = self._dest_input.text().strip()
        if not address and self._dest_lat is None:
            self._set_status("Enter a destination")
            return

        if self._dest_lat is not None:
            # Coordinates already known (map pick or previous geocode)
            self._start_route_fetch()
        else:
            # Need to geocode the address first
            self._set_status("Geocoding…")
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

    def _on_geocode_ready(self, name: str, lat: float, lon: float, is_origin: bool):
        if not is_origin and self._geocoding_dest:
            self._geocoding_dest = False
            self._dest_lat = lat
            self._dest_lon = lon
            self._dest_input.setText(name[:60] + ("…" if len(name) > 60 else ""))
            self._start_route_fetch()

    def _on_route_ready(self, result: RouteResult):
        self._btn_go.setEnabled(True)
        self._set_status("")
        self._btn_clear.setEnabled(True)
        self._populate_steps(result)
        self.route_calculated.emit(result)
        self._resize_for_content()

    def _on_fetch_error(self, msg: str):
        self._btn_go.setEnabled(self._own_lat is not None)
        self._geocoding_dest = False
        self._set_status(msg[:60])

    def _on_clear(self):
        self._dest_lat = None
        self._dest_lon = None
        self._dest_input.clear()
        self._summary_label.setVisible(False)
        self._steps_list.clear()
        self._steps_list.setVisible(False)
        self._btn_clear.setEnabled(False)
        self._btn_pick.blockSignals(True)
        self._btn_pick.setChecked(False)
        self._btn_pick.blockSignals(False)
        self._set_status("")
        self._update_go_enabled()
        self.route_cleared.emit()
        self._resize_for_content()

    def _populate_steps(self, result: RouteResult):
        self._steps_list.clear()
        for step in result.steps:
            dist_str = fmt_distance(step.distance_m) if step.distance_m > 0 else ""
            text     = f"{step.icon}  {step.instruction}"
            if dist_str:
                text += f"  —  {dist_str}"
            item = QListWidgetItem(text)
            self._steps_list.addItem(item)

        total_dist = fmt_distance(result.distance_m)
        total_time = fmt_duration(result.duration_s)
        self._summary_label.setText(f"{total_dist}  ·  {total_time}")
        self._summary_label.setVisible(True)
        self._steps_list.setVisible(True)

    def _set_status(self, msg: str):
        self._status_label.setText(msg)

    def _resize_for_content(self):
        """Animate to new natural height after content changes."""
        if self.maximumHeight() <= 0:
            return
        self.setMaximumHeight(16777215)
        target  = self.sizeHint().height()
        current = self.height()
        if target == current:
            return
        anim = QPropertyAnimation(self, b"maximumHeight")
        anim.setDuration(120)
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.finished.connect(lambda: self.setMaximumHeight(16777215))
        anim.start()
        self._animation = anim
