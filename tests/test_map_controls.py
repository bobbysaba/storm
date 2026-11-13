import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication

from ui.controls.map_controls import MapControls


def test_annotations_button_starts_on_and_reports_toggles():
    app = QApplication.instance() or QApplication([])
    controls = MapControls()
    seen = []
    controls.btn_annotations.toggled.connect(seen.append)
    assert controls.btn_annotations.isCheckable() and controls.btn_annotations.isChecked()
    controls.btn_annotations.setChecked(False)
    controls.btn_annotations.setChecked(True)
    assert seen == [False, True]
    controls.deleteLater()
    app.processEvents()
