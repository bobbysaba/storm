from typing import Callable

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QMenu, QToolButton, QWidget

from ui import palette as P
from ui.launch.icons import toolbar_icon

FULL = "full"           # icon + label
ICONS = "icons"         # icon only, label in the tooltip
OVERFLOW = "overflow"   # icon only, low-use buttons in the ⋯ menu
TIERS = (FULL, ICONS, OVERFLOW)

ICON_PX = 14


def pick_tier(widths: dict[str, int], available: int) -> str:
    """Widest tier whose measured width fits ``available``; the narrowest otherwise."""
    measured = [t for t in TIERS if t in widths]
    for tier in measured:
        if widths[tier] <= available:
            return tier
    return measured[-1]


class ResponsiveToolbar(QWidget):
    """Floating toolbar of drawer buttons in groups split by separators.

    ``fit(width)`` measures the toolbar in each tier and applies the widest one
    that fits, so breakpoints follow whatever buttons this mode and feature set
    actually show. Buttons marked ``overflow`` move into the ⋯ menu in the
    narrowest tier; the menu entries click the real button, so its side effects
    and drawer bookkeeping still run.
    """

    overflow_changed = pyqtSignal(object)   # set of buttons now in the ⋯ menu

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("floatingToolbar")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 4, 8, 4)
        self._layout.setSpacing(4)

        self._groups: list[list[QToolButton]] = [[]]
        self._separators: list[QFrame] = []
        self._labels: dict[QToolButton, str] = {}
        self._icon_keys: dict[QToolButton, str] = {}
        self._overflowable: list[QToolButton] = []
        self._unavailable: set[QToolButton] = set()
        self._icon_px = ICON_PX
        # drawer pins for buttons in the ⋯ menu, which can't be right-clicked
        self._is_pinned: Callable[[QToolButton], bool | None] | None = None
        self._set_pinned: Callable[[QToolButton, bool], None] | None = None

        self._tier = FULL
        self._widths: dict[str, int] | None = None
        self._last_available: int | None = None
        self._overflowed: set[QToolButton] = set()

        self._more_menu = QMenu(self)
        self._more_menu.aboutToShow.connect(self._rebuild_menu)
        self.more_button = QToolButton()
        self.more_button.setObjectName("toolbarMore")
        self.more_button.setToolTip("More layers")
        self.more_button.setMenu(self._more_menu)
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._icon_keys[self.more_button] = "more"
        self._apply_icon(self.more_button)

    # ── building ─────────────────────────────────────────────────────────
    def add_button(self, label: str, tooltip: str, icon_key: str,
                   overflow: bool = False) -> QToolButton:
        btn = QToolButton()
        btn.setText(label)
        btn.setToolTip(f"{label}: {tooltip}")
        btn.setCheckable(True)
        self._labels[btn] = label
        self._icon_keys[btn] = icon_key
        self._apply_icon(btn)
        if overflow:
            self._overflowable.append(btn)
        self._groups[-1].append(btn)
        self._layout.addWidget(btn)
        self.invalidate()
        return btn

    def add_group(self) -> None:
        """Start a new group; a separator sits between it and the previous one."""
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {P.DIVIDER}; margin: 6px 2px;")
        self._separators.append(sep)
        self._layout.addWidget(sep)
        self._groups.append([])

    def finish(self) -> None:
        """Call once all buttons are added; puts the ⋯ button at the end."""
        self._layout.addWidget(self.more_button)
        self._apply(self._tier)

    def set_available(self, btn: QToolButton, available: bool) -> None:
        """Show or drop a button for this mode. Use instead of ``hide()``,
        which the toolbar would undo when it re-applies a tier."""
        if available:
            self._unavailable.discard(btn)
        else:
            self._unavailable.add(btn)
        self.invalidate()
        self._apply(self._tier)

    def set_pin_handlers(self, is_pinned: Callable[[QToolButton], bool | None],
                         set_pinned: Callable[[QToolButton, bool], None]) -> None:
        """Offer "keep open" toggles in the ⋯ menu. ``is_pinned`` returns None
        for a button whose drawer can't be pinned."""
        self._is_pinned = is_pinned
        self._set_pinned = set_pinned

    def set_icon_px(self, px: int) -> None:
        if px == self._icon_px:
            return
        self._icon_px = px
        for btn in self._icon_keys:
            self._apply_icon(btn)
        self.invalidate()

    # ── fitting ──────────────────────────────────────────────────────────
    def tier(self) -> str:
        return self._tier

    def overflowed(self) -> set[QToolButton]:
        return set(self._overflowed)

    def invalidate(self) -> None:
        """Forget measured widths; the next ``fit`` measures again."""
        self._widths = None

    def measured_widths(self) -> dict[str, int]:
        if self._widths is None:
            current = self._tier
            widths = {}
            for tier in TIERS:
                if tier == OVERFLOW and not self._overflow_candidates():
                    continue
                self._apply(tier, notify=False)
                self._layout.invalidate()
                widths[tier] = self._layout.sizeHint().width()
            self._apply(current, notify=False)
            self._widths = widths
        return self._widths

    def fit(self, available: int) -> str:
        """Apply the widest tier that fits ``available`` pixels."""
        if self._widths is not None and available == self._last_available:
            return self._tier
        self._last_available = available
        self._apply(pick_tier(self.measured_widths(), available))
        return self._tier

    def changeEvent(self, event):
        # text scale re-applies the stylesheet: label widths change
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.FontChange):
            self.invalidate()
        super().changeEvent(event)

    # ── internals ────────────────────────────────────────────────────────
    def _overflow_candidates(self) -> list[QToolButton]:
        return [b for b in self._overflowable if b not in self._unavailable]

    def _apply_icon(self, btn: QToolButton) -> None:
        btn.setIcon(toolbar_icon(self._icon_keys[btn], self._icon_px))
        btn.setIconSize(QSize(self._icon_px, self._icon_px))

    def _apply(self, tier: str, notify: bool = True) -> None:
        self._tier = tier
        overflowed = set(self._overflow_candidates()) if tier == OVERFLOW else set()
        style = (Qt.ToolButtonStyle.ToolButtonTextBesideIcon if tier == FULL
                 else Qt.ToolButtonStyle.ToolButtonIconOnly)
        any_before = False
        for i, group in enumerate(self._groups):
            shown = False
            for btn in group:
                visible = btn not in self._unavailable and btn not in overflowed
                btn.setToolButtonStyle(style)
                btn.setVisible(visible)
                shown = shown or visible
            if i > 0:
                self._separators[i - 1].setVisible(shown and any_before)
            any_before = any_before or shown
        self.more_button.setVisible(bool(overflowed))
        if notify and overflowed != self._overflowed:
            self._overflowed = overflowed
            self.overflow_changed.emit(set(overflowed))
        elif notify:
            self._overflowed = overflowed

    def _rebuild_menu(self) -> None:
        self._more_menu.clear()
        for btn in self._overflowable:
            if btn not in self._overflowed:
                continue
            act = self._more_menu.addAction(btn.icon(), self._labels[btn])
            act.setCheckable(True)
            act.setChecked(btn.isChecked())
            act.setToolTip(btn.toolTip())
            act.triggered.connect(lambda _on, b=btn: b.click())
        if self._is_pinned is None:
            return
        pins = [(b, self._is_pinned(b)) for b in self._overflowable if b in self._overflowed]
        pins = [(b, p) for b, p in pins if p is not None]
        if not pins:
            return
        self._more_menu.addSection("Keep open when other drawers open")
        for btn, pinned in pins:
            act = self._more_menu.addAction(f"Pin {self._labels[btn]}")
            act.setCheckable(True)
            act.setChecked(pinned)
            act.toggled.connect(lambda on, b=btn: self._set_pinned(b, on))
