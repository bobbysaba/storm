
import base64
import hashlib
import hmac
import logging
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QToolButton, QFileDialog, QFrame,
    QApplication, QMessageBox, QSizePolicy, QWidget,
    QDateTimeEdit, QSpinBox, QComboBox, QCheckBox, QScrollArea, QCalendarWidget,
)
from PyQt6.QtCore import (
    Qt, QSettings, QTimer, QSize, QDate, QDateTime,
)
from PyQt6.QtGui import QIcon

import config as _config
import settings as _settings
from data.update_checker import UpdateWorker
from ui.launch.date_widgets import _AvailabilityCalendar, _CircularProgress, _ClickConsumer, _YearGridPopup
from ui.launch.icons import _LEFT, _RIGHT, _calendar_glyph_icon, _svg_pixmap, _triangle_icon, combo_down_arrow_qss
from ui.launch.styles import (
    _CAL_BTN_STYLE, _DIALOG_STYLE, _ICON_SELECTED_STYLE, _LINK_BTN_STYLE, _LOG_BTN_STYLE,
    _MODE_BTN_SELECTED_STYLE, _MODE_BTN_STYLE, _UPD_AVAILABLE, _UPD_CHECKING, _UPD_CURRENT,
    _UPD_ERROR, _UPD_SUCCESS, _UPD_WARNING,
)
from ui.launch.availability import AvailabilityWorker
from ui.launch.date_finder import FindDatesDialog
from ui.launch.update_dialogs import _CondaUpdateDialog, _LogViewerDialog
from ui import palette as P

log = logging.getLogger(__name__)



_PBKDF2_ITERATIONS = 600_000

def _obs_sources():
    """The station_obs [[sources]] the launch window can turn on at launch."""
    return _settings.SETTINGS.sources_of("station_obs")


def _verify_pbkdf2(passphrase: str, stored: str) -> bool:
    """Verify *passphrase* against a ``base64(salt):base64(dk)`` hash."""
    try:
        salt_b64, dk_b64 = stored.split(":", 1)
        salt = base64.b64decode(salt_b64)
        expected_dk = base64.b64decode(dk_b64)
    except Exception:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, _PBKDF2_ITERATIONS)
    return hmac.compare_digest(dk, expected_dk)


class LaunchDialog(QDialog):
    """
    Pre-launch configuration dialog.  Reads previous settings from
    config.toml and writes them back on confirmation so the next launch
    is pre-populated automatically.
    """

    def __init__(self, parent=None, *, mode: str | None = None, archive_time: datetime | None = None,
                 verified_modes=()):
        """mode / archive_time preset the form (Change Day reopens it in
        ARCHIVE on the last date); verified_modes holds "admin" once its passphrase
        was already given in this STORM run, so it isn't asked again."""
        super().__init__(parent)
        self._verified_modes = frozenset(verified_modes)
        # _post_layout_adjust() runs after every content-size change
        # (switching modes, opening the data section, ...); these flags make
        # its re-centering happen until the user drags the window, not after
        self._user_moved_window = False
        self._recentering_window = False
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._post_layout_adjust)
        self.setWindowTitle("STORM")
        self.setMinimumWidth(380)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )
        self.setStyleSheet(_DIALOG_STYLE + combo_down_arrow_qss())

        s = QSettings()
        saved = {
            "vehicle_id":     s.value("launch/vehicle_id",     "",        type=str),
            "data_dir":       s.value("launch/data_dir",       "",        type=str),
            "obs_layout":     s.value("launch/obs_layout",     "",        type=str),
            "mode":           s.value("launch/mode",           "vehicle", type=str),
            "vehicle_icon":   s.value("launch/vehicle_icon",   "car",     type=str),
            "auto_spc":       s.value("launch/auto_spc",       False,     type=bool),
            "auto_nws":       s.value("launch/auto_nws",       False,     type=bool),
            "auto_radar":     s.value("launch/auto_radar",     False,     type=bool),
            "auto_satellite": s.value("launch/auto_satellite", "",        type=str),
            **{f"auto_obs_{src.id}": s.value(f"launch/auto_obs_{src.id}", False, type=bool)
               for src in _obs_sources()},
            "radar_resolution":  s.value("launch/radar_resolution",  -1,    type=int),
        }
        if mode:
            saved["mode"] = mode
        self._project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # archive dates: one background scan, restarted when the date changes
        self._availability = AvailabilityWorker()
        self._availability.updated.connect(self._on_availability_updated)
        self.destroyed.connect(self._availability.close)
        self._availability_generation = 0
        self._availability_snapshot = None
        self._availability_timer = QTimer(self)
        self._availability_timer.setSingleShot(True)
        self._availability_timer.setInterval(250)
        self._availability_timer.timeout.connect(self._request_availability)
        self._year_click_filters: list = []
        self._year_grid_popup: _YearGridPopup | None = None
        self._calendar_popup: _AvailabilityCalendar | None = None
        self._date_finder: FindDatesDialog | None = None
        self._case_package_path: Path | None = None
        self._case_package_day = None
        self._build_ui(saved)
        if archive_time is not None:
            self._archive_dt_edit.setDateTime(QDateTime(
                archive_time.year, archive_time.month, archive_time.day,
                archive_time.hour, archive_time.minute, archive_time.second))
        if "admin" in self._verified_modes:
            self._admin_check.setChecked(True)
        self._archive_dt_edit.dateChanged.connect(self._schedule_availability)
        self._start_update_check()


    def _build_ui(self, saved: dict):
        # the form scrolls on a short screen; the launch/update footer stays put
        frame = QVBoxLayout(self)
        frame.setContentsMargins(0, 0, 0, 0)
        frame.setSpacing(0)
        self._form_scroll = QScrollArea()
        self._form_scroll.setObjectName("launchFormScroll")
        self._form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._form_scroll.setWidgetResizable(True)
        self._form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._form = QWidget()
        self._form.setObjectName("launchForm")
        self._form.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._form_scroll.setWidget(self._form)
        frame.addWidget(self._form_scroll)
        root = QVBoxLayout(self._form)
        root.setContentsMargins(32, 32, 32, 12)
        root.setSpacing(0)

        # title
        title = QLabel("STORM")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        sub = QLabel("Severe Thunderstorm Observation and Reconnaissance Monitor")
        sub.setObjectName("subtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        root.addWidget(sub)
        root.addSpacing(16)

        # divider
        div = QFrame()
        div.setObjectName("divider")
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet(f"background-color: {P.BORDER_SUBTLE};")
        div.setFixedHeight(1)
        root.addWidget(div)
        root.addSpacing(16)

        self._vehicle_section = QWidget()
        vs = QVBoxLayout(self._vehicle_section)
        vs.setContentsMargins(0, 0, 0, 0)
        vs.setSpacing(0)

        # vehicle ID
        vid_label = QLabel("VEHICLE ID")
        vid_label.setObjectName("fieldLabel")
        vs.addWidget(vid_label)
        vs.addSpacing(6)

        vid_row = QHBoxLayout()
        vid_row.setSpacing(6)
        self._vid_input = QLineEdit(saved.get("vehicle_id", ""))
        self._vid_input.setPlaceholderText("e.g.  car1")
        vid_row.addWidget(self._vid_input)

        self._lock_btn = QPushButton("🔒")
        self._lock_btn.setObjectName("lockBtn")
        self._lock_btn.setFixedWidth(36)
        self._lock_btn.setToolTip("Unlock both fields")
        self._lock_btn.clicked.connect(self._toggle_fields_lock)
        vid_row.addWidget(self._lock_btn)
        vs.addLayout(vid_row)
        vs.addSpacing(14)

        # data directory
        dir_label = QLabel("DATA DIRECTORY")
        dir_label.setObjectName("fieldLabel")
        vs.addWidget(dir_label)
        vs.addSpacing(6)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(6)
        self._dir_input = QLineEdit(saved.get("data_dir", ""))
        self._dir_input.setPlaceholderText("Leave blank for GPS puck")
        dir_row.addWidget(self._dir_input)

        self._browse_btn = QPushButton("…")
        self._browse_btn.setObjectName("browseBtn")
        self._browse_btn.setFixedWidth(36)
        self._browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(self._browse_btn)

        vs.addLayout(dir_row)
        vs.addSpacing(14)

        # logger file layout: the [obs_columns.<name>] layouts, when there's
        # more than one to choose from
        layouts = _settings.SETTINGS.obs_layouts
        self._layout_combo = QComboBox()
        self._layout_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for name, fields in layouts:
            self._layout_combo.addItem(fields.label or name, name)
        idx = self._layout_combo.findData(saved.get("obs_layout", ""))
        self._layout_combo.setCurrentIndex(max(idx, 0))
        if len(layouts) > 1:
            layout_label = QLabel("LOGGER FILE")
            layout_label.setObjectName("fieldLabel")
            vs.addWidget(layout_label)
            vs.addSpacing(6)
            vs.addWidget(self._layout_combo)
            vs.addSpacing(14)
        else:
            self._layout_combo.setVisible(False)

        # vehicle icon picker
        icon_label = QLabel("VEHICLE ICON")
        icon_label.setObjectName("fieldLabel")
        vs.addWidget(icon_label)
        vs.addSpacing(6)

        icon_row = QHBoxLayout()
        icon_row.setSpacing(4)
        self._icon_btns: dict[str, QToolButton] = {}
        _icons = [
            ("car", "CAR"), ("drone", "DRONE"), ("mesonet", "MESO"),
            ("lidar", "LIDAR"), ("radar", "RADAR"), ("hailcam", "HAIL"),
        ]
        for key, label in _icons:
            btn = QToolButton()
            btn.setText(label)
            btn.setObjectName("iconBtn")
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setIconSize(QSize(22, 22))
            btn.setIcon(QIcon(_svg_pixmap(key, P.TEXT_MUTED, 22)))
            btn.clicked.connect(lambda _checked, k=key: self._select_icon(k))
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            icon_row.addWidget(btn)
            self._icon_btns[key] = btn
        vs.addLayout(icon_row)
        vs.addSpacing(12)

        root.addWidget(self._vehicle_section)

        self._set_icon_selected(saved.get("vehicle_icon", "car"))

        # single lock controls all vehicle config fields; lock when either value exists.
        self._set_fields_locked(bool(saved.get("vehicle_id") or saved.get("data_dir")))

        mode_label = QLabel("LAUNCH MODE")
        mode_label.setObjectName("fieldLabel")
        root.addWidget(mode_label)
        root.addSpacing(6)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        self._mode_btns: dict[str, QPushButton] = {}
        for key, label in (("vehicle", "VEHICLE"), ("monitor", "MONITOR"), ("viewer", "VIEWER"), ("archive", "ARCHIVE")):
            btn = QPushButton(label)
            btn.setStyleSheet(_MODE_BTN_STYLE)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _checked, k=key: self._select_mode(k))
            mode_row.addWidget(btn)
            self._mode_btns[key] = btn
        root.addLayout(mode_row)
        root.addSpacing(12)

        self._admin_check = QCheckBox("admin mode")
        self._admin_check.setChecked(False)
        self._admin_check.setStyleSheet(
            f"QCheckBox {{ color: {P.TEXT_SECONDARY}; font-size: 11px; font-weight: 600; }}"
            f"QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {P.BORDER_SUBTLE};"
            f" border-radius: 3px; background: {P.BG_ELEVATED}; }}"
            f"QCheckBox::indicator:checked {{ background: {P.ACCENT}; border-color: {P.ACCENT}; }}"
        )
        self._admin_check.toggled.connect(self._on_admin_toggled)
        root.addWidget(self._admin_check)
        root.addSpacing(8)

        self._admin_passphrase_row = QWidget()
        admin_pw_layout = QVBoxLayout(self._admin_passphrase_row)
        admin_pw_layout.setContentsMargins(0, 0, 0, 0)
        admin_pw_layout.setSpacing(6)
        admin_label = QLabel("ADMIN PASSPHRASE")
        admin_label.setObjectName("fieldLabel")
        admin_pw_layout.addWidget(admin_label)
        self._admin_passphrase_input = QLineEdit()
        self._admin_passphrase_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._admin_passphrase_input.setPlaceholderText("Enter admin passphrase")
        admin_pw_layout.addWidget(self._admin_passphrase_input)
        self._admin_passphrase_row.setVisible(False)
        root.addWidget(self._admin_passphrase_row)
        root.addSpacing(4)

        self._archive_section = QWidget()
        av_layout = QVBoxLayout(self._archive_section)
        av_layout.setContentsMargins(0, 10, 0, 0)
        av_layout.setSpacing(6)

        arc_lbl = QLabel("ARCHIVE START TIME (UTC)")
        arc_lbl.setObjectName("fieldLabel")
        av_layout.addWidget(arc_lbl)

        self._archive_dt_edit = QDateTimeEdit()
        self._archive_dt_edit.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        self._archive_dt_edit.setCalendarPopup(True)
        self._archive_dt_edit.setCalendarWidget(_AvailabilityCalendar(self._archive_dt_edit))
        # default to yesterday at 20:00 UTC as a sensible starting point.
        now_utc = datetime.now(timezone.utc)
        yesterday = now_utc.replace(hour=20, minute=0, second=0, microsecond=0)
        self._archive_dt_edit.setDateTime(
            QDateTime(
                yesterday.year, yesterday.month, yesterday.day,
                yesterday.hour, yesterday.minute, yesterday.second,
            )
        )
        dt_row = QHBoxLayout()
        dt_row.setSpacing(6)
        dt_row.addWidget(self._archive_dt_edit, 1)
        dt_row.addWidget(self._build_calendar_button())
        self._archive_scan_progress = _CircularProgress(self)
        self._archive_scan_progress.setToolTip("Finding dates with data…")
        self._archive_scan_progress.hide()
        dt_row.addWidget(self._archive_scan_progress)
        av_layout.addLayout(dt_row)
        self._init_calendar_icons()

        arc_hint = QLabel("All data products will replay from this UTC time. "
                          "Shaded calendar days have data.")
        arc_hint.setObjectName("hint")
        arc_hint.setWordWrap(True)
        av_layout.addWidget(arc_hint)

        # one link per line: side by side they don't fit the form's width
        links = QVBoxLayout()
        links.setSpacing(7)
        self._find_dates_btn = self._link_button(
            "Browse available cases…", "Find dates with data by campaign, years, months and vehicles",
            self._open_date_finder)
        self._open_case_btn = self._link_button(
            "Open case…", "Open a case package: its date, storm track, settings and packed data",
            self._on_open_case_clicked)
        for btn in (self._find_dates_btn, self._open_case_btn):
            links.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)
        av_layout.addSpacing(4)
        av_layout.addLayout(links)

        case_row = QHBoxLayout()
        case_row.setSpacing(6)
        self._case_label = QLabel("")
        self._case_label.setObjectName("hint")
        self._case_label.setWordWrap(True)
        self._case_label.setToolTip("Launch opens this case: its storm track, settings and packed data")
        case_row.addWidget(self._case_label, 1)
        self._case_clear_btn = self._link_button("Clear", "Don't open this case", self._clear_case_package)
        case_row.addWidget(self._case_clear_btn, 0, Qt.AlignmentFlag.AlignTop)
        self._case_label.hide()
        self._case_clear_btn.hide()
        av_layout.addLayout(case_row)
        self._archive_dt_edit.dateTimeChanged.connect(self._on_archive_dt_changed_for_case)

        self._archive_section.setVisible(False)
        root.addWidget(self._archive_section)

        # apply saved mode
        self._select_mode(saved.get("mode", "vehicle"))

        root.addSpacing(14)
        div2 = QFrame()
        div2.setObjectName("divider")
        div2.setFrameShape(QFrame.Shape.HLine)
        div2.setStyleSheet(f"background-color: {P.BORDER_SUBTLE};")
        div2.setFixedHeight(1)
        root.addWidget(div2)
        root.addSpacing(10)

        self._data_toggle_btn = QPushButton("▸  DATA CONFIGURATION")
        self._data_toggle_btn.setObjectName("dataToggleBtn")
        self._data_toggle_btn.setFixedHeight(18)
        self._data_toggle_btn.clicked.connect(self._toggle_data_section)
        root.addWidget(self._data_toggle_btn)

        # collapsible container — hidden by default
        self._data_section = QWidget()
        ds = QVBoxLayout(self._data_section)
        ds.setContentsMargins(0, 8, 0, 0)
        ds.setSpacing(6)

        # row 1: SPC / NWS / RADAR as multi-select button strip
        self._layer_btns: dict[str, QPushButton] = {}
        layer_row = QHBoxLayout()
        layer_row.setSpacing(6)
        for key, label in (("spc", "SPC"), ("nws", "NWS"), ("radar", "RADAR")):
            btn = QPushButton(label)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _checked, k=key: self._toggle_layer(k))
            layer_row.addWidget(btn)
            self._layer_btns[key] = btn
        ds.addLayout(layer_row)

        # row 2: satellite selector (exclusive)
        sat_row = QHBoxLayout()
        sat_row.setSpacing(6)
        sat_lbl = QLabel("SAT")
        sat_lbl.setObjectName("fieldLabel")
        sat_lbl.setFixedWidth(28)
        sat_row.addWidget(sat_lbl)
        self._sat_btns: dict[str, QPushButton] = {}
        for key, label in (("", "OFF"), ("conus", "CONUS"), ("auto_meso", "AUTO-MESO")):
            btn = QPushButton(label)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _checked, k=key: self._select_satellite(k))
            sat_row.addWidget(btn)
            self._sat_btns[key] = btn
        ds.addLayout(sat_row)

        # row 3: surface obs multi-select, the station_obs [[sources]] (none: no row)
        obs_row = QHBoxLayout()
        obs_row.setSpacing(6)
        obs_lbl = QLabel("OBS")
        obs_lbl.setObjectName("fieldLabel")
        obs_lbl.setFixedWidth(28)
        obs_row.addWidget(obs_lbl)
        self._obs_btns: dict[str, QPushButton] = {}
        for src in _obs_sources():
            btn = QPushButton(src.short)
            btn.setToolTip(f"Turn on {src.name} at launch")
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _checked, k=src.id: self._toggle_obs(k))
            obs_row.addWidget(btn)
            self._obs_btns[src.id] = btn
        if self._obs_btns:
            ds.addLayout(obs_row)
        else:
            obs_lbl.hide()

        # row 4: radar render resolution
        res_row = QHBoxLayout()
        res_row.setSpacing(6)
        res_lbl = QLabel("RES")
        res_lbl.setObjectName("fieldLabel")
        res_lbl.setFixedWidth(28)
        res_row.addWidget(res_lbl)
        self._res_combo = QComboBox()
        self._res_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        _RES_OPTIONS = [
            ("Dynamic (auto-adjust)", -1),
            ("256 px  (fast)", 256),
            ("384 px", 384),
            ("512 px", 512),
            ("768 px  (sharp)", 768),
            ("1024 px  (ultra)", 1024),
            ("1536 px  (max)", 1536),
        ]
        for label, value in _RES_OPTIONS:
            self._res_combo.addItem(label, value)
        saved_res = int(saved.get("radar_resolution", -1))
        default_idx = 0
        for i, (_, v) in enumerate(_RES_OPTIONS):
            if v == saved_res:
                default_idx = i
                break
        self._res_combo.setCurrentIndex(default_idx)
        res_row.addWidget(self._res_combo)
        ds.addLayout(res_row)

        self._data_section.setVisible(False)
        root.addWidget(self._data_section)

        # initialise multi-select state from saved settings
        self._selected_layers: set[str] = set()
        for key, setting in (("spc", "auto_spc"), ("nws", "auto_nws"), ("radar", "auto_radar")):
            if saved.get(setting, False):
                self._selected_layers.add(key)
        self._refresh_layer_styles()

        self._select_satellite(saved.get("auto_satellite", ""))

        self._selected_obs: set[str] = set()
        for key in self._obs_btns:
            if saved.get(f"auto_obs_{key}", False):
                self._selected_obs.add(key)
        self._refresh_obs_styles()

        # auto-expand if any data pref is set
        any_data = (
            bool(self._selected_layers)
            or bool(self._selected_satellite)
            or bool(self._selected_obs)
        )
        if any_data:
            self._toggle_data_section()

        root.addSpacing(4)

        # Keep launch/update controls reachable while a long form scrolls.
        self._footer = QWidget()
        root = QVBoxLayout(self._footer)
        root.setContentsMargins(32, 14, 32, 20)
        root.setSpacing(0)
        frame.addWidget(self._footer)

        # launch button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        launch = QPushButton("LAUNCH STORM")
        launch.setObjectName("launchBtn")
        launch.clicked.connect(self._on_launch)
        launch.setDefault(True)
        self._launch_btn = launch
        btn_row.addWidget(launch)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # update button (below launch, centered, smaller)
        root.addSpacing(10)
        upd_row = QHBoxLayout()
        upd_row.addStretch()
        self._update_btn = QPushButton("CHECKING FOR UPDATES...")
        self._update_btn.setEnabled(False)
        self._update_btn.setStyleSheet(_UPD_CHECKING)
        self._update_btn.clicked.connect(self._on_update_clicked)
        upd_row.addWidget(self._update_btn)
        upd_row.addStretch()
        root.addLayout(upd_row)

        # log viewer links (very subtle, bottom of dialog)
        root.addSpacing(4)
        log_row = QHBoxLayout()
        log_row.addStretch()
        self._crash_log_btn = QPushButton("VIEW CRASH LOG")
        self._crash_log_btn.setStyleSheet(_LOG_BTN_STYLE)
        self._crash_log_btn.clicked.connect(self._on_view_crash_log_clicked)
        log_row.addWidget(self._crash_log_btn)
        log_row.addStretch()
        root.addLayout(log_row)

        # defer adjustSize until the event loop starts so the full layout is
        self._layout_timer.start(0)


    def _post_layout_adjust(self):
        """Honor wrapped content height; scroll the form on smaller screens.

        QWidget.adjustSize() caps top-level windows at a fraction of the screen
        and can squeeze nested layouts below their height-for-width. Compute the
        form's height explicitly instead of pumping a nested event loop.
        """
        self._form.setMinimumHeight(0)
        layout = self._form.layout()
        layout.invalidate()
        layout.activate()
        width = max(self.minimumWidth(), self._form.minimumSizeHint().width())
        height = layout.heightForWidth(width)
        if height < 0:                       # no height-for-width in the form
            height = layout.sizeHint().height()
        footer_height = self._footer.sizeHint().height()
        screen = self.screen().availableGeometry()
        max_height = max(240, screen.height() - 60)
        scrolls = height + footer_height > max_height
        scrollbar = self._form_scroll.verticalScrollBar().sizeHint().width() if scrolls else 0
        self._form.setMinimumHeight(height)
        self.resize(width + scrollbar, min(height + footer_height, max_height))
        self.layout().activate()
        self._form.layout().activate()
        if not self._user_moved_window:
            self._recenter()

    def _recenter(self):
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.x() + max(0, (screen.width()  - self.width())  // 2)
        y = screen.y() + max(0, (screen.height() - self.height()) // 2)
        self._recentering_window = True
        try:
            self.move(x, y)
        finally:
            self._recentering_window = False

    def moveEvent(self, event):
        super().moveEvent(event)
        if not self._recentering_window:
            self._user_moved_window = True


    def _init_calendar_icons(self):
        """Arrow icons on the date field and its calendar."""
        _drop = self._archive_dt_edit.findChild(QToolButton)
        if _drop:
            _drop.setIcon(_triangle_icon([(0.1, 0.2), (0.9, 0.2), (0.5, 0.85)], P.TEXT_LABEL, 10, 8))
            _drop.setIconSize(QSize(10, 8))
        self._style_calendar_nav_icons(self._archive_dt_edit.calendarWidget())

    def _style_calendar_nav_icons(self, calendar: QCalendarWidget):
        """Month arrows, and the year button opening the year grid instead
        of Qt's editable year spin box."""
        _prev = calendar.findChild(QToolButton, "qt_calendar_prevmonth")
        _next = calendar.findChild(QToolButton, "qt_calendar_nextmonth")
        if _prev:
            _prev.setIcon(_triangle_icon(_LEFT, P.WHITE))
            _prev.setIconSize(QSize(10, 10))
        if _next:
            _next.setIcon(_triangle_icon(_RIGHT, P.WHITE))
            _next.setIconSize(QSize(10, 10))
        _spin = calendar.findChild(QSpinBox, "qt_calendar_yearedit")
        if _spin:
            # should Qt still show it, it isn't a text field
            _spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            _spin.setReadOnly(True)
            _spin.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            _spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _year_btn = calendar.findChild(QToolButton, "qt_calendar_yearbutton")
        if _year_btn is not None:
            _year_btn.setToolTip("Choose a year")
            consumer = _ClickConsumer(lambda c=calendar, b=_year_btn: self._open_year_grid(c, b), calendar)
            _year_btn.installEventFilter(consumer)
            self._year_click_filters.append(consumer)

    def _open_year_grid(self, calendar: QCalendarWidget, anchor: QToolButton):
        if self._year_grid_popup is not None:
            self._year_grid_popup.hide()
            self._year_grid_popup.deleteLater()
        popup = _YearGridPopup(calendar.yearShown(), parent=self)
        popup.set_known_years(d.year for d in self._known_dates())
        popup.yearPicked.connect(lambda year, c=calendar: c.setCurrentPage(year, c.monthShown()))
        popup.move(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        popup.show()
        self._year_grid_popup = popup

    def _link_button(self, text: str, tip: str, slot) -> QPushButton:
        btn = QPushButton(text)
        btn.setToolTip(tip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFlat(True)
        btn.setStyleSheet(_LINK_BTN_STYLE)
        btn.clicked.connect(slot)
        return btn

    def _build_calendar_button(self) -> QToolButton:
        btn = QToolButton()
        btn.setIcon(_calendar_glyph_icon())
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip("Choose a date (shaded days have data)")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedSize(32, 32)
        btn.setStyleSheet(_CAL_BTN_STYLE)
        btn.clicked.connect(self._open_date_picker)
        self._cal_btn = btn
        return btn

    def _open_date_picker(self):
        """A calendar under the button, on the archive field's date."""
        if self._calendar_popup is None:
            popup = _AvailabilityCalendar(self)
            popup.setWindowFlags(Qt.WindowType.Popup)
            popup.setGridVisible(False)
            popup.setStyleSheet(_DIALOG_STYLE)
            popup.clicked.connect(self._on_date_picked)
            self._calendar_popup = popup
            self._style_calendar_nav_icons(popup)
        self._calendar_popup.setSelectedDate(self._archive_dt_edit.date())
        self._calendar_popup.set_known_dates(self._known_dates())
        self._calendar_popup.move(self._cal_btn.mapToGlobal(self._cal_btn.rect().bottomLeft()))
        self._calendar_popup.show()

    def _set_archive_date(self, day: QDate):
        """A new date at the same time of day."""
        self._archive_dt_edit.setDateTime(QDateTime(day, self._archive_dt_edit.time()))

    def _on_date_picked(self, qdate):
        self._set_archive_date(qdate)
        if self._calendar_popup is not None:
            self._calendar_popup.hide()

    def _open_date_finder(self):
        if self._date_finder is None:
            self._date_finder = FindDatesDialog(self)
            self._date_finder.date_chosen.connect(
                lambda d: self._set_archive_date(QDate(d.year, d.month, d.day)))
        self._date_finder.set_snapshot(self._availability_snapshot)
        self._date_finder.set_current_date(self._archive_dt_edit.date().toPyDate())
        self._date_finder.show()
        self._date_finder.raise_()
        self._date_finder.activateWindow()

    # -- case package ---------------------------------------------------------

    def _on_open_case_clicked(self):
        """Choose a case package: the date and time move to the case, and
        Launch opens it (its track, settings and packed data)."""
        from core import case_package  # noqa: PLC0415
        folder = case_package.PACKAGES_ROOT
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Case Package", str(folder if folder.is_dir() else Path.home()),
            "STORM case package (*.zip)")
        if path:
            self._choose_case_package(Path(path))

    def _choose_case_package(self, path: Path):
        from core import case_package  # noqa: PLC0415
        try:
            manifest, tracks = case_package.read(path)
            case = manifest["case"]
            when = datetime.fromisoformat(case["archive_time"].replace("Z", "+00:00"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, "Open Case Package", f"{path.name}: {exc}")
            return
        self._case_package_path = None          # the date change below mustn't clear it
        self._archive_dt_edit.setDateTime(QDateTime(when.year, when.month, when.day,
                                                    when.hour, when.minute, when.second))
        self._case_package_path = path
        self._case_package_day = when.date()
        packed = len(manifest.get("data", []))
        frame = manifest.get("time_frame")
        span = f"{frame['start'][11:16]}–{frame['end'][11:16]} UTC" if frame else "whole session"
        n_tracks = len(tracks)
        self._case_label.setText(
            f"Case: {path.name}\n"
            f"{case.get('radar_station') or 'No radar'} · {span} · "
            f"{n_tracks} track{'s' if n_tracks != 1 else ''} · {packed} file{'s' if packed != 1 else ''}")
        self._case_label.show()
        self._case_clear_btn.show()
        self._layout_timer.start(0)

    def _clear_case_package(self):
        self._case_package_path = None
        self._case_label.hide()
        self._case_clear_btn.hide()
        self._layout_timer.start(0)

    def _on_archive_dt_changed_for_case(self, qdt):
        # another date means not this case any more
        if self._case_package_path is not None and qdt.date().toPyDate() != self._case_package_day:
            self._clear_case_package()

    # -- archive dates: one background scan, newest sources first ---------------

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_availability()

    def hideEvent(self, event):
        self._availability_generation += 1
        self._availability_timer.stop()
        self._availability.cancel()
        if self._date_finder is not None:
            self._date_finder.close()
        super().hideEvent(event)

    def _schedule_availability(self, *_):
        self._availability_generation += 1
        self._availability.cancel()
        self._availability_timer.stop()
        if self._selected_mode == "archive":
            self._render_availability()
            if self.isVisible():
                self._availability_timer.start()

    def _request_availability(self):
        if self.isVisible() and self._selected_mode == "archive":
            self._availability.request(self._availability_generation)

    def _on_availability_updated(self, generation, snapshot):
        if generation != self._availability_generation or self._selected_mode != "archive":
            return
        # failed checks go to the log, once each
        previous = self._availability_snapshot
        seen = {(pid, e) for pid, r in previous.platforms.items() for e in r.errors} if previous else set()
        for pid, result in snapshot.platforms.items():
            for error in result.errors:
                if (pid, error) not in seen:
                    log.warning("Archive availability [%s]: %s", pid, error)
        self._availability_snapshot = snapshot
        if self._date_finder is not None:
            self._date_finder.set_snapshot(snapshot)
        self._render_availability()

    def _known_dates(self):
        snapshot = self._availability_snapshot
        return snapshot.dates if snapshot is not None else frozenset()

    def _render_availability(self):
        snapshot = self._availability_snapshot
        dates = self._known_dates()
        self._archive_dt_edit.calendarWidget().set_known_dates(dates)
        if self._calendar_popup is not None:
            self._calendar_popup.set_known_dates(dates)
        if self._year_grid_popup is not None:
            self._year_grid_popup.set_known_years({d.year for d in dates})
        running = snapshot is None or snapshot.checked < snapshot.total
        self._archive_scan_progress.setVisible(running)
        self._archive_scan_progress.setRange(0, snapshot.total if snapshot else 0)
        self._archive_scan_progress.setValue(snapshot.checked if snapshot else 0)
        self._archive_scan_progress.setToolTip(
            f"Finding dates with data · {snapshot.checked}/{snapshot.total} catalogs checked"
            if snapshot else "Finding dates with data…")


    def _set_fields_locked(self, locked: bool):
        self._vid_input.setReadOnly(locked)
        self._dir_input.setReadOnly(locked)
        self._browse_btn.setEnabled(not locked)
        self._layout_combo.setEnabled(not locked)
        self._lock_btn.setText("🔒" if locked else "🔓")
        self._lock_btn.setToolTip("Unlock all fields" if locked else "Lock all fields")
        for btn in self._icon_btns.values():
            btn.setEnabled(not locked)

    def _toggle_fields_lock(self):
        self._set_fields_locked(not self._vid_input.isReadOnly())

    def _select_mode(self, mode: str):
        self._selected_mode = mode
        for key, btn in self._mode_btns.items():
            btn.setStyleSheet(_MODE_BTN_SELECTED_STYLE if key == mode else _MODE_BTN_STYLE)
        vehicle_mode = (mode == "vehicle")
        archive_mode = (mode == "archive")

        self._vehicle_section.setVisible(vehicle_mode)
        # archive-specific date/time picker; data-on-launch hidden in archive mode
        self._archive_section.setVisible(archive_mode)
        if hasattr(self, "_data_section"):
            self._data_toggle_btn.setVisible(not archive_mode)
            if archive_mode and self._data_section.isVisible():
                self._data_section.setVisible(False)
                self._data_toggle_btn.setText("▸  DATA CONFIGURATION")

        if self.isVisible():
            self._post_layout_adjust()
        self._schedule_availability()

    def _on_admin_toggled(self, checked: bool):
        self._admin_passphrase_row.setVisible(checked and "admin" not in self._verified_modes)
        if not checked:
            self._admin_passphrase_input.clear()
        if self.isVisible():
            self._post_layout_adjust()

    def _toggle_data_section(self):
        visible = not self._data_section.isVisible()
        self._data_section.setVisible(visible)
        self._data_toggle_btn.setText("▾  DATA ON LAUNCH" if visible else "▸  DATA ON LAUNCH")
        self._layout_timer.start(0)

    def _toggle_layer(self, key: str):
        self._selected_layers.discard(key) if key in self._selected_layers else self._selected_layers.add(key)
        self._refresh_layer_styles()

    def _refresh_layer_styles(self):
        for k, btn in self._layer_btns.items():
            btn.setStyleSheet(_MODE_BTN_SELECTED_STYLE if k in self._selected_layers else _MODE_BTN_STYLE)

    def _select_satellite(self, key: str):
        self._selected_satellite = key if key in self._sat_btns else ""
        for k, btn in self._sat_btns.items():
            btn.setStyleSheet(_MODE_BTN_SELECTED_STYLE if k == self._selected_satellite else _MODE_BTN_STYLE)

    def _toggle_obs(self, key: str):
        self._selected_obs.discard(key) if key in self._selected_obs else self._selected_obs.add(key)
        self._refresh_obs_styles()

    def _refresh_obs_styles(self):
        for k, btn in self._obs_btns.items():
            btn.setStyleSheet(_MODE_BTN_SELECTED_STYLE if k in self._selected_obs else _MODE_BTN_STYLE)

    def _select_icon(self, key: str):
        self._set_icon_selected(key)

    def _set_icon_selected(self, key: str):
        self._selected_icon = key if key in self._icon_btns else "car"
        for k, btn in self._icon_btns.items():
            selected = (k == self._selected_icon)
            btn.setStyleSheet(_ICON_SELECTED_STYLE if selected else "")
            btn.setIcon(QIcon(_svg_pixmap(k, P.ACCENT if selected else P.TEXT_MUTED, 22)))


    def _start_update_check(self):
        self._worker = UpdateWorker()
        self._worker.check_done.connect(self._on_check_done)
        self._worker.pull_done.connect(self._on_pull_done)
        self._worker.start_check()

    def _on_check_done(self, commits_behind: int):
        if commits_behind == -3:
            self._update_btn.setText("⚠   NOT A GIT INSTALL — CLONE REPO FOR IN-APP UPDATES")
            self._update_btn.setStyleSheet(_UPD_WARNING)
        elif commits_behind == -2:
            self._update_btn.setText("DEV BUILD")
            self._update_btn.setStyleSheet(_UPD_CURRENT)
        elif commits_behind < 0:
            self._update_btn.setText("⚠   UPDATE CHECK FAILED — PROCEED AND TRY AGAIN LATER")
            self._update_btn.setStyleSheet(_UPD_WARNING)
        elif commits_behind == 0:
            self._update_btn.setText("✓   UP TO DATE")
            self._update_btn.setStyleSheet(_UPD_CURRENT)
        else:
            n = commits_behind
            label = "UPDATE AVAILABLE — CLICK TO UPDATE"
            self._update_btn.setText(label)
            self._update_btn.setStyleSheet(_UPD_AVAILABLE)
            self._update_btn.setEnabled(True)

    def _on_update_clicked(self):
        self._update_btn.setEnabled(False)
        self._update_btn.setText("UPDATING...")
        self._update_btn.setStyleSheet(_UPD_CHECKING)
        self._worker.start_pull()

    def _on_pull_done(self, success: bool, deps_changed: bool):
        if success and deps_changed:
            # show dialog asking user if they want automatic update
            reply = QMessageBox.question(
                self,
                "Dependencies Changed",
                "The update includes dependency changes that require updating your conda environment.\n\n"
                "Would you like STORM to update the environment automatically?\n\n"
                "This will run: conda env update -f envs/storm.yml --prune",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._start_conda_update()
            else:
                # show manual instructions
                _cmd = "conda env update -f envs/storm.yml --prune"
                self._update_btn.setText(f"⚠   DEPS CHANGED — RUN:\n{_cmd}\nTHEN RESTART")
                self._update_btn.setStyleSheet(_UPD_WARNING)
                self._update_btn.setWordWrap(True)
                self._layout_timer.start(0)
        elif success:
            self._update_btn.setText("✓   UPDATED — RESTARTING...")
            self._update_btn.setStyleSheet(_UPD_SUCCESS)
            QTimer.singleShot(800, self._restart_app)
        else:
            self._update_btn.setText("UPDATE FAILED")
            self._update_btn.setStyleSheet(_UPD_ERROR)

    def _start_conda_update(self):
        """Show progress dialog and start conda update in background."""
        # prevent multiple simultaneous updates
        if hasattr(self, '_conda_dialog') and self._conda_dialog:
            return
        
        self._conda_dialog = _CondaUpdateDialog(self)
        self._conda_dialog.rejected.connect(self._worker.cancel_conda_update)
        # use a unique-connection to avoid duplicate slots on repeated calls
        try:
            self._worker.conda_update_done.disconnect(self._on_conda_update_done)
        except TypeError:
            pass
        self._worker.conda_update_done.connect(self._on_conda_update_done)
        self._worker.start_conda_update()
        self._conda_dialog.exec()

    def _on_conda_update_done(self, success: bool, error_msg: str):
        """Handle conda update completion."""
        dialog = getattr(self, "_conda_dialog", None)
        if dialog:
            dialog.accept()
            self._conda_dialog = None
        else:
            # dialog already dismissed (user cancelled) — just show manual fallback
            if not success:
                _cmd = "conda env update -f envs/storm.yml --prune"
                self._update_btn.setText(f"⚠   DEPS CHANGED — RUN:\n{_cmd}\nTHEN RESTART")
                self._update_btn.setStyleSheet(_UPD_WARNING)
                self._update_btn.setWordWrap(True)
                self._layout_timer.start(0)
            return

        if success:
            self._update_btn.setText("✓   UPDATED — RESTARTING...")
            self._update_btn.setStyleSheet(_UPD_SUCCESS)
            QTimer.singleShot(800, self._restart_app)
        else:
            # show error and fall back to manual instructions
            QMessageBox.warning(
                self,
                "Conda Update Failed",
                f"Automatic conda update failed:\n\n{error_msg}\n\n"
                "Please run the following command manually:\n"
                "conda env update -f envs/storm.yml --prune",
            )
            _cmd = "conda env update -f envs/storm.yml --prune"
            self._update_btn.setText(f"⚠   DEPS CHANGED — RUN:\n{_cmd}\nTHEN RESTART")
            self._update_btn.setStyleSheet(_UPD_WARNING)
            self._update_btn.setWordWrap(True)
            self._layout_timer.start(0)

    def _restart_app(self):
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def _on_view_crash_log_clicked(self):
        log_path = os.path.join(self._project_root, "storm_fault.log")
        dlg = _LogViewerDialog(log_path, parent=self)
        dlg.exec()


    def _browse_dir(self):
        current = self._dir_input.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Select data directory", current
        )
        if chosen:
            self._dir_input.setText(chosen)

    def _on_launch(self):
        mode = getattr(self, "_selected_mode", "vehicle")

        # vehicle and monitor publish, so they need a broker login that can
        if problems := _settings.SETTINGS.broker_problems(mode):
            lines = "\n".join(f"  \u2022 {p}" for p in problems)
            QMessageBox.warning(
                self,
                "No Broker Login",
                f"{mode.upper()} mode needs a broker it can publish to "
                f"([mqtt] in {_settings.SETTINGS.path.name}):\n\n{lines}\n\n"
                "VIEWER and ARCHIVE launch without one. "
                "See docs/configuration.md.",
            )
            return

        if self._admin_check.isChecked() and "admin" not in self._verified_modes:
            if not _config.ADMIN_PASSPHRASE_HASH:
                QMessageBox.warning(
                    self,
                    "Admin Passphrase Not Configured",
                    "Admin/Labs mode is not configured for this build.",
                )
                self._admin_check.setFocus()
                return
            if not _verify_pbkdf2(
                self._admin_passphrase_input.text(),
                _config.ADMIN_PASSPHRASE_HASH,
            ):
                QMessageBox.warning(
                    self,
                    "Incorrect Admin Passphrase",
                    "The admin passphrase you entered is incorrect.",
                )
                self._admin_passphrase_input.clear()
                self._admin_passphrase_input.setFocus()
                return

        # vehicle ID is required in vehicle mode
        if mode == "vehicle":
            vid = self._vid_input.text().strip()
            if not vid:
                QMessageBox.warning(
                    self,
                    "Vehicle ID Required",
                    "Please enter a vehicle ID before launching.",
                )
                self._vid_input.setFocus()
                return

        self._do_accept()

    def _do_accept(self):
        s = QSettings()
        s.setValue("launch/vehicle_id",     self._vid_input.text().strip())
        s.setValue("launch/data_dir",       self._dir_input.text().strip())
        s.setValue("launch/obs_layout",     self._layout_combo.currentData())
        mode = getattr(self, "_selected_mode", "vehicle")
        # don't persist archive mode as the default (it needs explicit selection).
        s.setValue("launch/mode", mode if mode != "archive" else "viewer")
        s.setValue("launch/vehicle_icon",   getattr(self, "_selected_icon", "car"))
        layers = getattr(self, "_selected_layers", set())
        s.setValue("launch/auto_spc",   "spc"   in layers)
        s.setValue("launch/auto_nws",   "nws"   in layers)
        s.setValue("launch/auto_radar", "radar" in layers)
        s.setValue("launch/auto_satellite", getattr(self, "_selected_satellite", ""))
        obs = getattr(self, "_selected_obs", set())
        for key in getattr(self, "_obs_btns", {}):
            s.setValue(f"launch/auto_obs_{key}", key in obs)
        s.setValue("launch/radar_resolution", self._res_combo.currentData())
        self.accept()


    def vehicle_id(self) -> str:
        if getattr(self, "_selected_mode", "vehicle") != "vehicle":
            return ""
        return self._vid_input.text().strip()

    def data_dir(self) -> str:
        if getattr(self, "_selected_mode", "vehicle") != "vehicle":
            return ""
        return self._dir_input.text().strip()

    def obs_layout(self) -> str:
        """The [obs_columns] layout of the vehicle's logger files."""
        if getattr(self, "_selected_mode", "vehicle") != "vehicle":
            return ""
        return self._layout_combo.currentData() or ""

    def monitor(self) -> bool:
        return getattr(self, "_selected_mode", "vehicle") == "monitor"

    def viewer(self) -> bool:
        return getattr(self, "_selected_mode", "vehicle") == "viewer"

    def archive(self) -> bool:
        return getattr(self, "_selected_mode", "vehicle") == "archive"

    def admin_mode(self) -> bool:
        return self._admin_check.isChecked()

    def archive_start_time(self) -> "datetime | None":
        """UTC start time chosen in the archive date/time picker, or None."""
        if not self.archive():
            return None
        qt_dt = self._archive_dt_edit.dateTime()
        return datetime(
            qt_dt.date().year(),
            qt_dt.date().month(),
            qt_dt.date().day(),
            qt_dt.time().hour(),
            qt_dt.time().minute(),
            qt_dt.time().second(),
            tzinfo=timezone.utc,
        )

    def case_package(self) -> "Path | None":
        """The case package chosen to open at launch (ARCHIVE only)."""
        return self._case_package_path if self.archive() else None

    def vehicle_icon(self) -> str:
        if getattr(self, "_selected_mode", "vehicle") != "vehicle":
            return "car"
        return getattr(self, "_selected_icon", "car")

    def radar_resolution(self) -> int:
        """Return the chosen render grid size, or -1 for dynamic (adaptive) mode."""
        return self._res_combo.currentData()
