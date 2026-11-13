"""TRACK drawer (archive): undo / redo / clear for the hand-placed storm track,
the table, importing and exporting a CSV, which layers to draw, the point
count, where it is saved and the storm motion it gives.

Editing itself happens on the map while the drawer is open (see
ui/app/main_window_track.py); this drawer only reports and offers the buttons.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from ui import palette as P
from ui.widgets.drawer import Drawer


class TrackControls(Drawer):
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    table_requested = pyqtSignal()
    import_requested = pyqtSignal()
    export_requested = pyqtSignal()
    open_folder_requested = pyqtSignal()
    layers_changed = pyqtSignal()          # Line / Points / Keep on map

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("trackDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        # ── row 1: edits, layers ──
        row = QHBoxLayout()
        row.setSpacing(4)
        self._btn_undo = self._button("UNDO", "Undo the last track edit (Ctrl+Z)", self.undo_requested)
        self._btn_redo = self._button("REDO", "Redo the last undone edit (Ctrl+Shift+Z)", self.redo_requested)
        self._btn_clear = self._button("CLEAR", "Remove every point (can be undone)", self.clear_requested)
        for b in (self._btn_undo, self._btn_redo, self._btn_clear):
            row.addWidget(b)
        row.addWidget(self._vdiv())
        self._btn_table = self._button("TABLE", "Exact times and positions of every point", self.table_requested)
        self._btn_import = self._button(
            "IMPORT", "Replace the track with a CSV file (STORM's or MESO-VIEW's; can be undone)",
            self.import_requested)
        self._btn_export = self._button("EXPORT", "Save a copy of the track as CSV elsewhere", self.export_requested)
        self._btn_folder = self._button("FOLDER", "Show the storm_tracks folder", self.open_folder_requested)
        for b in (self._btn_table, self._btn_import, self._btn_export, self._btn_folder):
            row.addWidget(b)
        row.addWidget(self._vdiv())
        self.chk_line = QCheckBox("Line")
        self.chk_points = QCheckBox("Points")
        self.chk_keep = QCheckBox("Keep on map")
        self.chk_line.setToolTip("Draw the line joining the points")
        self.chk_points.setToolTip("Draw the points")
        self.chk_keep.setToolTip("Keep drawing the track after TRACK is closed")
        self.chk_line.setChecked(True)
        self.chk_points.setChecked(True)
        for chk in (self.chk_line, self.chk_points, self.chk_keep):
            chk.toggled.connect(lambda _on: self.layers_changed.emit())
            row.addWidget(chk)
        row.addStretch(1)
        col.addLayout(row)

        # ── row 2: count, storm motion ──
        info = QHBoxLayout()
        info.setSpacing(10)
        self._count_label = QLabel()
        self._count_label.setStyleSheet(f"color: {P.TEXT_SECONDARY}; font-size: 11px;")
        info.addWidget(self._count_label)
        title = QLabel("STORM MOTION")
        title.setStyleSheet(
            f"color: {P.TEXT_LABEL}; font-size: 10px; font-weight: 600; letter-spacing: 1px;"
        )
        info.addWidget(title)
        self._motion_mean = QLabel()
        self._motion_now = QLabel()
        self._motion_mean.setToolTip("End to end: first point to last, as in MESO-VIEW")
        self._motion_now.setToolTip("The track segment at the current time")
        for label in (self._motion_mean, self._motion_now):
            label.setStyleSheet(f"color: {P.TEXT}; font-size: 11px;")
            info.addWidget(label)
        info.addStretch(1)
        col.addLayout(info)

        # where edits are saved
        self._file_label = QLabel()
        self._file_label.setStyleSheet(f"color: {P.TEXT_MUTED}; font-size: 10px;")
        col.addWidget(self._file_label)

        # how to start, until there are two points
        self._hint = QLabel(
            "Click the storm's center on the map, step the clock to a later frame, and click it again."
        )
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(f"color: {P.TEXT_MUTED}; font-size: 11px;")
        col.addWidget(self._hint)

        outer.addWidget(self._drawer)
        self.set_point_count(0)
        self.set_motion(None, None)
        self.set_file("New track — saves with the first point")
        self.set_edit_state(can_undo=False, can_redo=False)

    def _button(self, text: str, tip: str, signal) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.clicked.connect(signal.emit)
        return b

    def _vdiv(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet(f"color: {P.BORDER}; margin: 4px 2px;")
        return div

    # ── state from the window ─────────────────────────────
    def layers(self) -> tuple[bool, bool]:
        return self.chk_line.isChecked(), self.chk_points.isChecked()

    def keep_on_map(self) -> bool:
        return self.chk_keep.isChecked()

    def set_edit_state(self, *, can_undo: bool, can_redo: bool) -> None:
        self._btn_undo.setEnabled(can_undo)
        self._btn_redo.setEnabled(can_redo)

    def set_point_count(self, count: int) -> None:
        self._count_label.setText(f"{count} point" + ("" if count == 1 else "s"))
        self._btn_clear.setEnabled(count > 0)
        self._btn_export.setEnabled(count > 0)
        if self._hint.isVisibleTo(self) != (count < 2):
            self._hint.setVisible(count < 2)
            self.content_changed()

    def set_motion(self, mean: str | None, now: str | None) -> None:
        """Readouts such as "240° · 15.1 m/s (29 kt)"; None shows why there's none."""
        self._motion_mean.setText(f"Mean {mean or '— (2 points needed)'}")
        self._motion_now.setText(f"Now {now or '— (outside the track)'}")

    def set_file(self, text: str, path: str = "") -> None:
        """Where the track saves, e.g. "Saved to storm_tracks/storm_20240427_track.csv"."""
        self._file_label.setText(text)
        self._file_label.setToolTip(path)
