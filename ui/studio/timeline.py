"""The Video Studio panel's two timelines (ui/studio/panel.py): the case
clock over the session (CaseStrip) and the movie in movie seconds
(MovieTimeline). Both read and edit through the panel they're given."""
from __future__ import annotations

from datetime import datetime, timedelta

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QToolTip, QWidget

from core.studio import Keyframe
from ui import palette as P

# keyframes and moves in the archive color, the case clock in the accent,
# the playhead in red; a backward move in violet
ORANGE, CYAN, RED, VIOLET = P.MODE_ARCHIVE, P.ACCENT, P.ERROR_SOFT, P.MODE_VIEWER
TEXT, MUTED, DIM, GROOVE = P.TEXT, P.TEXT_MUTED, P.TEXT_DISABLED, P.BORDER_SUBTLE


def movie_clock(s: float) -> str:
    """0:04.2"""
    s = max(0.0, s)
    m = int(s // 60)
    return f"{m}:{s - 60 * m:04.1f}"


def speed_text(x: float) -> str:
    x = abs(x)
    return f"{x:.0f}×" if x >= 10 else f"{x:.1f}×"


def case_span_text(seconds: float) -> str:
    seconds = abs(seconds)
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def diamond(x: float, y: float, r: float) -> QPolygonF:
    return QPolygonF([QPointF(x, y - r), QPointF(x + r, y), QPointF(x, y + r), QPointF(x - r, y)])


def _rgba(color: str, a: int) -> QColor:
    c = QColor(color)
    c.setAlpha(a)
    return c


# ---------------------------------------------------------------------------
class CaseStrip(QWidget):
    """The case clock over the whole session (or, zoomed, a window that
    follows the clock): time ticks, radar scans, where the movie's keyframes
    sit in the case, and the clock. Drag to move the clock (it is set on
    release; keyframe times snap), scroll to step radar scans."""
    seek = pyqtSignal(object)                # datetime, on release

    INSET = 10

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        self._tc = panel._tc
        self._drag_time = None
        self._wheel = 0
        self.zoom_s: float | None = None          # None: the whole session
        self._center = self._tc.current_time
        self.setFixedHeight(42)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("")

    # ---- the span shown: the whole session, or a window that follows the clock ----
    def set_zoom(self, seconds: float | None) -> None:
        self.zoom_s = seconds
        self._center = self._tc.current_time
        self.update()

    def span(self) -> tuple[datetime, datetime]:
        lo, hi = self._tc.window
        if self.zoom_s is None or (hi - lo).total_seconds() <= self.zoom_s:
            return lo, hi
        half = timedelta(seconds=self.zoom_s / 2)
        clock = self._drag_time or self._tc.current_time
        if self._drag_time is None:                      # keep the clock in view
            a, b = self._center - half, self._center + half
            margin = half * 0.1
            if not (a + margin <= clock <= b - margin):
                self._center = clock
        a = max(lo, min(self._center - half, hi - 2 * half))
        return a, a + 2 * half

    def _x(self, t: datetime) -> float:
        start, end = self.span()
        total = max(1.0, (end - start).total_seconds())
        return self.INSET + (self.width() - 2 * self.INSET) * (t - start).total_seconds() / total

    def _t(self, x: float) -> datetime:
        start, end = self.span()
        frac = min(1.0, max(0.0, (x - self.INSET) / max(1, self.width() - 2 * self.INSET)))
        t = start + timedelta(seconds=round((end - start).total_seconds() * frac))
        for k in self._panel.project.keyframes:          # snap to keyframes' case times
            if abs(self._x(k.time) - x) <= 5:
                return k.time
        return t

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        start, end = self.span()
        total = (end - start).total_seconds()
        if total <= 0:
            return
        font = QFont(self.font())
        font.setPixelSize(10)
        p.setFont(font)
        fm = p.fontMetrics()
        w = self.width() - 2 * self.INSET
        mid = 20
        # ticks and labels: a label at least every ~70 px
        tick = next((m for m in (5, 10, 15, 30, 60, 120, 180, 360) if w * m * 60 / total >= 22), 360)
        every = next((n for n in (1, 2, 3, 4, 6, 12, 24) if w * tick * n * 60 / total >= 64), 24)
        t = start.replace(minute=0, second=0, microsecond=0)
        while t < start:
            t += timedelta(minutes=tick)
        while t <= end:
            x = self._x(t)
            minutes = t.hour * 60 + t.minute
            labeled = (minutes // tick) % every == 0
            p.setPen(QColor(P.BORDER_DIM) if not labeled else QColor(P.BORDER_STRONG))
            p.drawLine(QPointF(x, mid - (7 if labeled else 4)), QPointF(x, mid + (7 if labeled else 4)))
            if labeled:
                text = f"{t:%H}Z" if t.minute == 0 and tick * every >= 60 else f"{t:%H:%M}Z"
                p.setPen(QColor(DIM))
                p.drawText(int(min(max(0, x - fm.horizontalAdvance(text) / 2), self.width() - fm.horizontalAdvance(text))),
                           self.height() - 2, text)
            t += timedelta(minutes=tick)
        p.fillRect(QRectF(self.INSET, mid - 2, w, 4), QColor(GROOVE))
        # radar scans
        p.setPen(QPen(_rgba(P.TEXT_MUTED, 110), 1))
        for s in self._panel.scan_times():
            if start <= s <= end:
                x = self._x(s)
                p.drawLine(QPointF(x, mid - 6), QPointF(x, mid - 3))
        # the movie's coverage of the case and its keyframes
        ks = self._panel.project.sorted()
        if len(ks) > 1:
            lo, hi = min(k.time for k in ks), max(k.time for k in ks)
            p.fillRect(QRectF(self._x(lo), mid - 2, max(2.0, self._x(hi) - self._x(lo)), 4), QColor(ORANGE))
        sel = self._panel.selected
        for i, k in enumerate(ks):
            x = self._x(k.time)
            p.setPen(QPen(QColor(P.WHITE) if k is sel else QColor(P.BG_BASE), 1.2))
            p.setBrush(QColor(ORANGE))
            p.drawPolygon(diamond(x, mid, 6 if k is sel else 4.5))
        # the clock
        clock = self._drag_time or self._tc.current_time
        x = self._x(clock)
        p.setPen(QPen(QColor(CYAN), 2))
        p.drawLine(QPointF(x, 3), QPointF(x, mid + 9))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(CYAN))
        p.drawEllipse(QPointF(x, 5), 4, 4)
        if self._drag_time is not None:
            text = f"{self._drag_time:%H:%M:%S}Z"
            tw = fm.horizontalAdvance(text) + 8
            bx = min(max(0, x + 7), self.width() - tw)
            p.setBrush(_rgba(P.BG_DEEP, 200))
            p.drawRoundedRect(QRectF(bx, 0, tw, 14), 3, 3)
            p.setPen(QColor(CYAN))
            p.drawText(int(bx + 4), 11, text)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_time = self._t(e.position().x())
            self.update()

    def mouseMoveEvent(self, e) -> None:
        t = self._t(e.position().x())
        if self._drag_time is not None:
            self._drag_time = t
            self.update()
        else:
            QToolTip.showText(e.globalPosition().toPoint(),
                             f"{t:%H:%M:%S} UTC — click or drag to set the clock\nScroll: radar scans (Shift: minutes)", self)

    def mouseReleaseEvent(self, e) -> None:
        if self._drag_time is not None:
            t, self._drag_time = self._drag_time, None
            self.seek.emit(t)
            self.update()

    def wheelEvent(self, e) -> None:
        """Scroll: a radar scan per notch (Shift: a minute)."""
        d = e.angleDelta()
        self._wheel += d.y() if abs(d.y()) >= abs(d.x()) else -d.x()
        while abs(self._wheel) >= 120:
            step = 1 if self._wheel > 0 else -1
            self._wheel -= 120 * step
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier or not self._panel.scan_times():
                self._panel._nudge_clock(60 * step)
            else:
                self._panel._scan(step)
        e.accept()


# ---------------------------------------------------------------------------
class MovieTimeline(QWidget):
    """The movie in movie seconds: ruler, clips, holds, keyframes, playhead."""
    scrub = pyqtSignal(float)               # movie seconds
    view_changed = pyqtSignal()             # zoom or scroll

    PAD = 14
    RULER_H = 18
    TRACK_Y = 24
    TRACK_H = 48

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        self.pps = 40.0                     # pixels per movie second
        self.left = 0.0                     # movie seconds at the left edge
        self._drag = None                   # (keyframe, press x, its at) while dragging one
        self._drag_moved = False
        self._scrubbing = False
        self.setFixedHeight(self.TRACK_Y + self.TRACK_H + 6)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    # ---- geometry ----------------------------------------------------------
    def x(self, m: float) -> float:
        return self.PAD + (m - self.left) * self.pps

    def m(self, x: float) -> float:
        return self.left + (x - self.PAD) / self.pps

    def visible_seconds(self) -> float:
        return max(0.1, (self.width() - 2 * self.PAD) / self.pps)

    def content_seconds(self) -> float:
        return max(self._panel.project.duration() * 1.15 + 2.0, self.visible_seconds())

    def set_left(self, left: float) -> None:
        self.left = max(0.0, min(left, self.content_seconds() - self.visible_seconds()))
        self.update()
        self.view_changed.emit()

    def zoom(self, factor: float, anchor_x: float | None = None) -> None:
        anchor_x = self.width() / 2 if anchor_x is None else anchor_x
        anchor_m = self.m(anchor_x)
        self.pps = min(600.0, max(2.0, self.pps * factor))
        self.set_left(anchor_m - (anchor_x - self.PAD) / self.pps)

    def fit(self) -> None:
        d = max(self._panel.project.duration(), 4.0)
        self.pps = min(600.0, max(2.0, (self.width() - 2 * self.PAD) / (d * 1.08)))
        self.set_left(0.0)

    def ensure_visible(self, m: float) -> None:
        if m < self.left or m > self.left + self.visible_seconds() * 0.96:
            self.set_left(m - self.visible_seconds() * 0.1)

    def _hit(self, x: float, y: float) -> Keyframe | None:
        if y < self.TRACK_Y - 8:
            return None
        best, dist = None, 8.0
        for k in self._panel.project.keyframes:
            d = abs(self.x(k.at) - x)
            if d <= dist:
                best, dist = k, d
        return best

    def segment_at(self, m: float) -> int:
        """Index of the keyframe whose hold or following move contains m (-1: none)."""
        ks = self._panel.project.sorted()
        for i, k in enumerate(ks):
            end = ks[i + 1].at if i + 1 < len(ks) else k.leaves
            if k.at - 1e-9 <= m < end or (i + 1 == len(ks) and abs(m - k.at) < 1e-6):
                return i
        return -1

    # ---- painting ---------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(10)
        p.setFont(font)
        fm = p.fontMetrics()
        W = self.width()
        project = self._panel.project
        ks = project.sorted()
        duration = project.duration()
        top, h = self.TRACK_Y, self.TRACK_H

        # ruler
        p.fillRect(QRectF(0, 0, W, self.RULER_H), _rgba(P.BG_DEEP, 120))
        step = next((s for s in (0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if s * self.pps >= 56), 600)
        minor = step / 5
        first = int(self.left / minor)
        n = first
        while True:
            m = n * minor
            x = self.x(m)
            if x > W:
                break
            major = abs(m / step - round(m / step)) < 1e-6
            p.setPen(QColor(P.BORDER_STRONG) if not major else QColor(P.BORDER_SOFT))
            p.drawLine(QPointF(x, self.RULER_H - (7 if major else 3)), QPointF(x, self.RULER_H))
            if major:
                text = movie_clock(m) if step < 1 else f"{int(m // 60)}:{int(m % 60):02d}"
                p.setPen(QColor(DIM))
                p.drawText(int(x + 3), 11, text)
            n += 1

        # the track
        p.fillRect(QRectF(0, top, W, h), _rgba(P.WHITE, 8))
        if not ks:
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(0, top, W, h), Qt.AlignmentFlag.AlignCenter, "No keyframes")
        thumbs = self._panel.thumbs
        sel = self._panel.selected
        for i, k in enumerate(ks):
            # its hold
            if k.hold > 0:
                r = QRectF(self.x(k.at), top + 6, max(1.0, k.hold * self.pps), h - 12)
                p.setPen(QPen(QColor(P.BORDER_SOFT), 1, Qt.PenStyle.DashLine))
                p.setBrush(_rgba(P.BORDER_STRONG, 120))
                p.drawRoundedRect(r, 4, 4)
                if r.width() > 46:
                    p.setPen(QColor(MUTED))
                    p.drawText(r.adjusted(6, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, f"hold {k.hold:.1f}s")
            if i + 1 == len(ks):
                break
            # the move to the next keyframe: a clip
            b = ks[i + 1]
            r = QRectF(self.x(k.leaves) + 1, top + 2, max(1.0, self.x(b.at) - self.x(k.leaves) - 2), h - 4)
            case_s = (b.time - k.time).total_seconds()
            if case_s > 0:
                fill, edge = _rgba(ORANGE, 46), QColor(ORANGE)
            elif case_s < 0:
                fill, edge = _rgba(VIOLET, 46), QColor(VIOLET)
            else:
                fill, edge = _rgba(CYAN, 34), QColor(CYAN)
            selected_clip = k is sel
            p.setPen(QPen(edge if selected_clip else edge.darker(160), 1.6 if selected_clip else 1))
            p.setBrush(fill.lighter(140) if selected_clip else fill)
            p.drawRoundedRect(r, 5, 5)
            text_x = r.left() + 8
            thumb = thumbs.get(self._panel.thumb_key(k))
            if thumb is not None and r.width() > 120:
                th = h - 12
                tw = th * 16 / 9
                p.drawPixmap(QRectF(r.left() + 5, r.top() + 4, tw, th).toRect(), thumb)
                text_x += tw + 2
            if r.right() - text_x > 30:
                radar = project.radar_at(k.at)
                station = f"{radar['station']} · " if radar and radar.get("station") else ""
                if case_s:
                    line1 = f"{station}{k.time:%H:%M:%S} → {b.time:%H:%M:%S}Z"
                    line2 = (f"{speed_text(project.segment_speed(i))} · {case_span_text(case_s)} of case"
                             + (" · reverse" if case_s < 0 else "") + f" · {k.easing}")
                else:
                    line1 = f"{station}camera move at {k.time:%H:%M:%S}Z"
                    line2 = f"clock still · {k.easing}"
                avail = int(r.right() - text_x - 6)
                p.setPen(QColor(TEXT))
                p.drawText(int(text_x), int(r.top() + 17), fm.elidedText(line1, Qt.TextElideMode.ElideRight, avail))
                p.setPen(QColor(MUTED))
                p.drawText(int(text_x), int(r.top() + 31), fm.elidedText(line2, Qt.TextElideMode.ElideRight, avail))

        # the end of the movie
        if ks:
            x = self.x(duration)
            p.setPen(QPen(QColor(P.BORDER_SOFT), 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(x, top - 4), QPointF(x, top + h))

        # keyframes
        for i, k in enumerate(ks):
            x = self.x(k.at)
            r = 7.5 if k is sel else 6
            p.setPen(QPen(QColor(P.WHITE) if k is sel else QColor(P.BG_BASE), 1.5))
            p.setBrush(QColor(ORANGE))
            p.drawPolygon(diamond(x, top, r))

        # the playhead
        x = self.x(self._panel.playhead)
        p.setPen(QPen(QColor(RED), 2))
        p.drawLine(QPointF(x, 4), QPointF(x, top + h))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(RED))
        p.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x + 6, 6), QPointF(x, 12),
                                 QPointF(x - 6, 6)]))

    # ---- mouse --------------------------------------------------------------
    def mousePressEvent(self, e) -> None:
        x, y = e.position().x(), e.position().y()
        kf = self._hit(x, y)
        if e.button() == Qt.MouseButton.RightButton:
            if kf is None:
                i = self.segment_at(self.m(x))
                kf = self._panel.project.sorted()[i] if i >= 0 else None
            self._panel.context_menu(kf, e.globalPosition().toPoint(), self.m(x))
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if kf is not None:
            self._panel.select(kf)
            self._drag = (kf, x, kf.at)
            self._drag_moved = False
            return
        self._scrubbing = True
        m = self.m(x)
        if y >= self.TRACK_Y:
            i = self.segment_at(m)
            if i >= 0:
                self._panel.select(self._panel.project.sorted()[i])
        self.scrub.emit(m)

    def mouseMoveEvent(self, e) -> None:
        x, y = e.position().x(), e.position().y()
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            kf, x0, at0 = self._drag
            if not self._drag_moved and abs(x - x0) < 3:
                return
            if not self._drag_moved:
                self._panel.snapshot()
                self._drag_moved = True
            at = at0 + (x - x0) / self.pps
            if abs(self.x(self._panel.playhead) - self.x(at)) <= 6:       # snap to the playhead
                at = self._panel.playhead
            else:
                fps = self._panel.project.fps
                at = round(at * fps) / fps
            ripple = not (e.modifiers() & Qt.KeyboardModifier.AltModifier)
            self._panel.project.move(kf, at, ripple=ripple)
            self._panel.edited(scroll=False)
            QToolTip.showText(e.globalPosition().toPoint(),
                              f"{movie_clock(kf.at)}" + ("" if ripple else "  (only this keyframe)"), self)
            return
        if self._scrubbing and e.buttons() & Qt.MouseButton.LeftButton:
            self.scrub.emit(self.m(x))
            return
        hit = self._hit(x, y)
        self.setCursor(Qt.CursorShape.SizeHorCursor if hit is not None and self._panel.project.sorted().index(hit) > 0
                       else Qt.CursorShape.ArrowCursor)
        if hit is not None:
            ks = self._panel.project.sorted()
            QToolTip.showText(e.globalPosition().toPoint(),
                              f"Keyframe {ks.index(hit) + 1} · {movie_clock(hit.at)} · {hit.time:%H:%M:%S}Z\n"
                              "Drag to retime (later keyframes follow; Alt: this one only)\n"
                              "Double-click to go there · right-click for more", self)

    def mouseReleaseEvent(self, _e) -> None:
        if self._drag is not None and self._drag_moved:
            self._panel.edited()
        self._drag = None
        self._scrubbing = False

    def mouseDoubleClickEvent(self, e) -> None:
        kf = self._hit(e.position().x(), e.position().y())
        if kf is not None:
            self._panel.go_to(kf)

    def wheelEvent(self, e) -> None:
        d = e.angleDelta()
        if e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            self.zoom(1.0015 ** d.y(), e.position().x())
        else:
            px = d.x() if abs(d.x()) > abs(d.y()) else -d.y()
            self.set_left(self.left - px / 2 / self.pps)
        e.accept()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.set_left(self.left)
