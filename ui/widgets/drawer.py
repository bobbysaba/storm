from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QTimer, pyqtSignal
from PyQt6.QtWidgets import QWidget

# Qt's QWIDGETSIZE_MAX: "no cap" for maximumHeight
_NO_CAP = 16777215


class Drawer(QWidget):
    """Floating pill that slides open/closed below the toolbar.

    Open/close animates ``maximumHeight``; when fully open the cap is lifted so
    content can grow. Subclasses call ``content_changed()`` after showing or
    hiding rows, and the drawer re-animates to its new natural height.

    ``geometry_changed`` fires on every animation step and after content
    changes, so the main window can re-run overlay layout only when needed.
    """

    geometry_changed = pyqtSignal()
    opened = pyqtSignal()
    closed = pyqtSignal()

    OPEN_MS = 180
    RESIZE_MS = 120

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_open = False
        self._animation: QPropertyAnimation | None = None
        self._open_pending = False
        self._content_pending = False
        self.setMaximumHeight(0)

    def is_open(self) -> bool:
        return self._is_open

    def is_animating(self) -> bool:
        return (self._animation is not None
                and self._animation.state() == QPropertyAnimation.State.Running)

    def occupies_space(self) -> bool:
        """True while open or still sliding closed — the stack keeps its slot."""
        return self._is_open or self.is_animating()

    def toggle_drawer(self, checked: bool):
        self._is_open = checked
        self._stop_animation()
        if checked:
            # measure on the next event-loop turn so content rebuilt in the
            # same turn (e.g. SFCOA product grid) is in the size hint
            self._open_pending = True
            QTimer.singleShot(0, self._animate_open)
            self.opened.emit()
        else:
            self._open_pending = False
            current = min(self.height(), self.maximumHeight())
            self.setMaximumHeight(current)
            self._animate(current, 0, self.OPEN_MS)
            self.closed.emit()

    def content_changed(self):
        """Re-fit to the content's natural height (coalesced per event-loop turn)."""
        if self._content_pending:
            return
        self._content_pending = True
        QTimer.singleShot(0, self._apply_content_change)

    def _animate_open(self):
        if not (self._open_pending and self._is_open):
            return
        self._open_pending = False
        start = min(self.height(), self.maximumHeight())
        self._animate(start, self._natural_height(), self.OPEN_MS)

    def _apply_content_change(self):
        self._content_pending = False
        if not self._is_open or self._open_pending:
            return
        target = self._natural_height()
        current = self.height()
        if target != current and not self.is_animating():
            self.setMaximumHeight(current)
            self._animate(current, target, self.RESIZE_MS)
        elif self.is_animating():
            # retarget the running open animation
            self._animation.setEndValue(target)
        else:
            self.setMaximumHeight(_NO_CAP)
        self.geometry_changed.emit()

    def _natural_height(self) -> int:
        cap = self.maximumHeight()
        self.setMaximumHeight(_NO_CAP)
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        self.updateGeometry()
        h = max(1, self.sizeHint().height())
        self.setMaximumHeight(cap)
        return h

    def _animate(self, start: int, end: int, ms: int):
        self._stop_animation()
        anim = QPropertyAnimation(self, b"maximumHeight", self)
        anim.setDuration(ms)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.valueChanged.connect(self.geometry_changed)
        anim.finished.connect(self._on_animation_finished)
        anim.start()
        self._animation = anim

    def _on_animation_finished(self):
        if self._is_open:
            self.setMaximumHeight(_NO_CAP)
        self.geometry_changed.emit()

    def _stop_animation(self):
        if self._animation is not None:
            self._animation.stop()
            self._animation = None
