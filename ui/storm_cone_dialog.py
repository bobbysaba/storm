# ui/storm_cone_dialog.py
# Speed / heading input dialog for placing and editing storm motion cones.

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QSpinBox, QPushButton, QFormLayout, QWidget
)
from PyQt6.QtCore import Qt

from ui.theme import ACCENT, BG_BASE, BG_ELEVATED, TEXT_MUTED


def _dialog_style() -> str:
    return f"""
        QDialog {{
            background-color: {BG_BASE};
        }}
        QLabel {{
            background-color: transparent;
        }}
        QDoubleSpinBox, QSpinBox {{
            background-color: {BG_ELEVATED};
            border: 1px solid #2E2E4E;
            border-radius: 6px;
            padding: 5px 8px;
            font-size: 12px;
            color: #E8EAF0;
            min-width: 100px;
        }}
        QDoubleSpinBox:focus, QSpinBox:focus {{
            border-color: {ACCENT};
        }}
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
        QSpinBox::up-button, QSpinBox::down-button {{
            width: 18px;
            border-left: 1px solid #2E2E4E;
            background-color: {BG_ELEVATED};
        }}
        QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover,
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
            background-color: #252540;
        }}
    """


class StormConeInputDialog(QDialog):
    """
    Single dialog for both new cone placement and cone editing.

    Usage:
        dlg = StormConeInputDialog(parent=self)               # new
        dlg = StormConeInputDialog(edit_mode=True, speed_kts=40, heading=240, parent=self)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            action  = dlg.action()    # "ok" | "save" | "delete" | "cancel"
            speed   = dlg.speed_kts()
            heading = dlg.heading()
    """

    def __init__(self, edit_mode: bool = False,
                 speed_kts: float = 35.0, heading: int = 240,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("annotationDialog")
        self.setWindowTitle("Edit Storm Motion Cone" if edit_mode else "Storm Motion Cone")
        self.setModal(True)
        self.setMinimumWidth(300)
        self.setStyleSheet(_dialog_style())
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)

        self._action = "cancel"
        self._edit_mode = edit_mode

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # ── header ────────────────────────────────────────────────────────
        header = QWidget()
        header.setStyleSheet(f"background-color: {BG_ELEVATED}; border-radius: 8px;")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 10, 12, 10)

        icon_label = QLabel("⟶")
        icon_label.setStyleSheet(f"font-size: 22px; color: {ACCENT}; background: transparent;")
        h_layout.addWidget(icon_label)

        title_label = QLabel("Storm Motion Cone")
        title_label.setStyleSheet("font-size: 14px; font-weight: 700; background: transparent;")
        h_layout.addWidget(title_label)
        h_layout.addStretch()
        layout.addWidget(header)

        # ── hint ──────────────────────────────────────────────────────────
        hint = QLabel("Heading is the direction the storm is <b>coming from</b>"
                      "<br>(e.g. 206° = SSW → storm moves NNE).")
        hint.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; background: transparent;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ── inputs ────────────────────────────────────────────────────────
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._speed_spin = QDoubleSpinBox()
        self._speed_spin.setRange(0, 120)
        self._speed_spin.setSingleStep(5)
        self._speed_spin.setSuffix(" kts")
        self._speed_spin.setDecimals(0)
        self._speed_spin.setValue(speed_kts)

        self._heading_spin = QSpinBox()
        self._heading_spin.setRange(0, 359)
        self._heading_spin.setSingleStep(5)
        self._heading_spin.setSuffix("°")
        self._heading_spin.setWrapping(True)
        self._heading_spin.setValue(int(heading))

        speed_label = QLabel("Speed")
        speed_label.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED};")
        heading_label = QLabel("Heading")
        heading_label.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED};")

        form.addRow(speed_label, self._speed_spin)
        form.addRow(heading_label, self._heading_spin)
        layout.addLayout(form)

        # ── buttons ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        if edit_mode:
            btn_delete = QPushButton("Delete")
            btn_delete.setObjectName("dangerButton")
            btn_delete.clicked.connect(self._on_delete)
            btn_row.addWidget(btn_delete)

        btn_row.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        if edit_mode:
            btn_ok = QPushButton("Save")
        else:
            btn_ok = QPushButton("OK")
        btn_ok.setObjectName("primaryButton")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._on_ok)
        btn_row.addWidget(btn_ok)

        layout.addLayout(btn_row)

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_ok(self):
        self._action = "save" if self._edit_mode else "ok"
        self.accept()

    def _on_delete(self):
        self._action = "delete"
        self.accept()

    # ── accessors ─────────────────────────────────────────────────────────────

    def action(self) -> str:
        """Returns 'ok', 'save', 'delete', or 'cancel'."""
        return self._action

    def speed_kts(self) -> float:
        return self._speed_spin.value()

    def heading(self) -> int:
        return self._heading_spin.value()
