"""VAD time-height grid and the VAD window's HODOGRAPH / TIME-HEIGHT toggle."""
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.vad import VADProfile, VADSet, time_height_grid

T0 = datetime(2026, 5, 17, 19, 0, tzinfo=timezone.utc)


def _profile(minutes, heights=(300.0, 600.0, 900.0), speed=20.0, direction=180.0):
    n = len(heights)
    return VADProfile(T0 + timedelta(minutes=minutes), "TLX", np.array(heights, float),
                      np.full(n, direction), np.full(n, speed))


def test_profiles_share_one_height_grid_without_extrapolation():
    vs = VADSet([_profile(0, (300, 600, 900)), _profile(5, (300, 600))])
    grid = time_height_grid(vs, dz_m=150.0)
    assert grid.u_kt.shape == (len(grid.heights_m), 2)
    top = np.argmin(np.abs(grid.heights_m - 900.0))
    assert np.isfinite(grid.speed_kt[top, 0]) and np.isnan(grid.speed_kt[top, 1])   # 2nd stops at 600 m
    assert np.allclose(grid.speed_kt[np.isfinite(grid.speed_kt)], 20.0)
    assert np.allclose(grid.v_kt[np.isfinite(grid.v_kt)], 20.0)       # from the south: v positive


def test_a_lone_profile_beside_a_gap_stays_narrow():
    # NEXRAD VADs every ~5 min, then a missing hour
    vs = VADSet([_profile(0), _profile(5), _profile(10), _profile(70), _profile(75)])
    edges, column = time_height_grid(vs).column_edges()
    assert list(column) == [0, 1, 2, -1, 3, 4]                          # the gap is its own empty column
    widths = np.diff(edges)
    assert widths[column == 2][0] <= 5 * 60 + 1e-6                      # at most one spacing wide
    assert widths[column == -1][0] > 30 * 60                            # the gap stays empty


def test_no_usable_profiles_gives_no_grid():
    assert time_height_grid(VADSet([_profile(0, heights=(300.0,))])) is None


@pytest.fixture
def dialog(monkeypatch):
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui.dialogs import vad_dialog
    vs = VADSet([_profile(m, speed=10.0 + m) for m in (0, 5, 10, 15)])
    monkeypatch.setattr(vad_dialog, "fetch_recent_vads", lambda site, count=12: vs)
    dlg = vad_dialog.VADDialog("TLX")
    dlg._fetch_data()
    yield dlg
    dlg.close()
    dlg.deleteLater()
    app.processEvents()


def test_toggle_switches_between_hodograph_and_time_height(dialog):
    assert dialog._view == "hodograph" and dialog._hodo_ax is not None
    assert not dialog._view_widget.isHidden()                          # several profiles: toggle offered
    dialog._view_buttons["time_height"].click()
    assert dialog._view == "time_height"
    assert dialog._th_ax is not None and dialog._hodo_ax is None
    assert dialog._view_buttons["time_height"].isChecked() and not dialog._view_buttons["hodograph"].isChecked()
    dialog._view_buttons["hodograph"].click()
    assert dialog._hodo_ax is not None and dialog._th_ax is None


def test_clicking_the_time_height_plot_picks_that_profile(dialog):
    import matplotlib.dates as mdates
    dialog._set_view("time_height")
    when = mdates.date2num(T0 + timedelta(minutes=10, seconds=40))
    dialog._on_canvas_click(SimpleNamespace(inaxes=dialog._th_ax, xdata=when))
    assert dialog._scrubber.value() == 2 and dialog._cur_idx == 2


def test_one_profile_offers_no_toggle(monkeypatch):
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from ui.dialogs import vad_dialog
    monkeypatch.setattr(vad_dialog, "fetch_recent_vads", lambda site, count=12: VADSet([_profile(0)]))
    dlg = vad_dialog.VADDialog("TLX")
    dlg._fetch_data()
    assert dlg._view_widget.isHidden() and dlg._hodo_ax is not None
    dlg.deleteLater()
