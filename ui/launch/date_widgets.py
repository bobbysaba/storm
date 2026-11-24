"""The launch window's date widgets (ui/launch/dialog.py): the archive
calendar that marks the dates with data, the year grid its year button
opens, and the ring that shows how far the availability scan has got."""
from PyQt6.QtCore import QDate, QEvent, QObject, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QTextCharFormat
from PyQt6.QtWidgets import QCalendarWidget, QGridLayout, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from ui import palette as P
from ui.launch.icons import _LEFT, _RIGHT, _triangle_icon
from ui.launch.styles import _YEAR_GRID_STYLE


class _CircularProgress(QWidget):
    """A small ring beside the archive date: how many catalogs the
    background date scan has checked."""

    def __init__(self, parent=None, diameter: int = 20):
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)
        self._value = 0
        self._maximum = 0

    def setRange(self, minimum: int, maximum: int) -> None:
        self._maximum = max(0, maximum)
        self.update()

    def setValue(self, value: int) -> None:
        self._value = value
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)
        track = QPen(QColor(P.BORDER))
        track.setWidth(3)
        painter.setPen(track)
        painter.drawArc(rect, 0, 360 * 16)
        fraction = (self._value / self._maximum) if self._maximum else 0.0
        arc = QPen(QColor(P.ACCENT))
        arc.setWidth(3)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc)
        # from 12 o'clock, clockwise (Qt angles run counterclockwise from 3 o'clock)
        painter.drawArc(rect, 90 * 16, -round(360 * 16 * fraction))
        painter.end()


class _AvailabilityCalendar(QCalendarWidget):
    """A calendar that shades the days with archive data."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._known_dates = frozenset()
        self.setMinimumSize(322, 260)
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.setMaximumDate(QDate.currentDate())     # no case is in the future
        weekday = QTextCharFormat()
        weekday.setForeground(QColor(P.TEXT_LABEL))
        for day in Qt.DayOfWeek:
            self.setWeekdayTextFormat(day, weekday)

    def set_known_dates(self, dates):
        self._known_dates = frozenset(dates)
        self.updateCells()

    def paintCell(self, painter, rect, date):
        # each cell drawn once (tinting over Qt's own cell washes out the
        # number and the selection); inset tiles keep runs of days apart
        current_month = date.year() == self.yearShown() and date.month() == self.monthShown()
        enabled = self.isEnabled() and self.minimumDate() <= date <= self.maximumDate()
        available = date.toPyDate() in self._known_dates
        selected = date == self.selectedDate() and enabled
        background = P.BG_SUNKEN_3
        foreground = P.TEXT if current_month else P.TEXT_LABEL
        border = None
        if not enabled:
            foreground = P.TEXT_DISABLED
        elif selected:
            background, foreground = P.ACCENT, P.BG_BASE
        elif available:
            background = P.AVAILABLE if current_month else P.AVAILABLE_DIM
            foreground = P.TEXT_BRIGHT if current_month else P.TEXT_LABEL
            border = P.AVAILABLE_EDGE if current_month else P.AVAILABLE_EDGE_DIM
        if enabled and not selected and date == QDate.currentDate():
            border = P.TEXT_LABEL

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(rect, QColor(P.BG_SUNKEN_3))
        tile = QRectF(rect).adjusted(2, 2, -2, -2)
        painter.setPen(QPen(QColor(border), 1) if border else Qt.PenStyle.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(tile, 4, 4)
        font = painter.font()
        font.setPixelSize(12)
        font.setBold(selected or (available and enabled))
        painter.setFont(font)
        painter.setPen(QColor(foreground))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(date.day()))
        painter.restore()


class _ClickConsumer(QObject):
    """Takes a widget's click (or Space / Enter) and calls on_click instead:
    the calendar's year button opens the year grid rather than Qt's year
    spin box."""

    def __init__(self, on_click, parent=None):
        super().__init__(parent)
        self._on_click = on_click

    def eventFilter(self, obj, event):
        mouse = event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton
        key = event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if mouse or key:
            self._on_click()
            return True
        return False


class _YearGridPopup(QWidget):
    """A dozen years to jump to from the calendar's year button; years with
    data are outlined. Never pages past this year."""

    yearPicked = pyqtSignal(int)

    _COLUMNS = 4
    _ROWS = 3
    _COUNT = _COLUMNS * _ROWS

    def __init__(self, current_year: int, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("yearGridPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_YEAR_GRID_STYLE)
        self._known_years = frozenset()
        self._current_year = current_year
        self._max_year = QDate.currentDate().year()
        self._start_year = self._clamp_start(current_year - 5)
        self._buttons: list[QPushButton] = []
        self._build_ui()
        self._refresh()

    def _clamp_start(self, start: int) -> int:
        return min(start, self._max_year - self._COUNT + 1)

    def _nav_button(self, points, tip, delta) -> QToolButton:
        btn = QToolButton()
        btn.setObjectName("yearNavBtn")
        btn.setIcon(_triangle_icon(points, P.TEXT_LABEL))
        btn.setIconSize(QSize(9, 9))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tip)
        btn.clicked.connect(lambda: self._shift(delta))
        return btn

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)
        nav = QHBoxLayout()
        nav.setSpacing(4)
        nav.addWidget(self._nav_button(_LEFT, "Earlier years", -self._COUNT))
        self._range_lbl = QLabel()
        self._range_lbl.setObjectName("yearRangeLbl")
        self._range_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self._range_lbl, 1)
        self._next_btn = self._nav_button(_RIGHT, "Later years", self._COUNT)
        nav.addWidget(self._next_btn)
        outer.addLayout(nav)

        grid = QGridLayout()
        grid.setSpacing(5)
        for i in range(self._COUNT):
            btn = QPushButton()
            btn.setObjectName("yearCell")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked, idx=i: self._pick(idx))
            grid.addWidget(btn, i // self._COLUMNS, i % self._COLUMNS)
            self._buttons.append(btn)
        outer.addLayout(grid)

    def _shift(self, delta: int):
        self._start_year = self._clamp_start(self._start_year + delta)
        self._refresh()

    def _pick(self, idx: int):
        self.yearPicked.emit(self._start_year + idx)
        self.hide()

    def set_known_years(self, years):
        self._known_years = frozenset(years)
        self._refresh()

    def _refresh(self):
        self._range_lbl.setText(f"{self._start_year} – {self._start_year + self._COUNT - 1}")
        self._next_btn.setEnabled(self._start_year + self._COUNT - 1 < self._max_year)
        for i, btn in enumerate(self._buttons):
            year = self._start_year + i
            btn.setText(str(year))
            btn.setProperty("available", year in self._known_years)
            btn.setProperty("selected", year == self._current_year)
            btn.setToolTip("Dates with data" if year in self._known_years else "No dates found yet")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
