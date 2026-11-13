"""Archive soundings have no HRRR source (Open-Meteo's historical HRRR was dropped)."""
import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from archive.fetchers.sounding_archive_fetcher import ArchiveSoundingFetcher
from core.sounding import SoundingSet
from ui.app.main_window import MainWindow
from ui.sounding.controls import SoundingControls


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_archive_fetcher_has_no_model_sounding():
    assert not hasattr(ArchiveSoundingFetcher, "fetch_model_sounding")


def test_drawer_without_hrrr_hides_it_and_defaults_to_obs(app):
    controls = SoundingControls()
    controls.set_hrrr_available(False)
    assert controls._btn_hrrr.isHidden() and controls._hrrr_div.isHidden()
    assert controls.active_mode == "obs" and controls._btn_obs.isChecked()

    controls._btn_nssl.setChecked(True)
    assert controls.active_mode == "nssl"
    controls.reset_mode()
    assert controls.active_mode == "obs"
    assert controls._btn_obs.isChecked() and not controls._btn_nssl.isChecked()


def test_live_drawer_still_resets_to_hrrr(app):
    controls = SoundingControls()
    controls._btn_obs.setChecked(True)
    controls.reset_mode()
    assert controls.active_mode == "hrrr" and controls._btn_hrrr.isChecked()


class _Host:
    _compute_available_sources = MainWindow._compute_available_sources


@pytest.mark.parametrize("archive, expect_hrrr", [(True, False), (False, True)])
def test_obs_sounding_offers_hrrr_comparison_only_live(archive, expect_hrrr):
    host = _Host()
    host._archive = archive
    host._last_nssl_sset = None
    sset = SoundingSet(lat=35.2, lon=-97.4, elevation=357.0,
                       fetch_time=datetime(2019, 6, 8, 0, tzinfo=timezone.utc),
                       soundings=[], source="obs")
    assert ("hrrr" in host._compute_available_sources(sset)) is expect_hrrr
