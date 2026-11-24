"""The [[sources]] in storm.toml and the code providers' sources
(data/sources.py): their toolbar, MAP and SOUNDING drawer buttons, the
overlay layers and the sounding fetchers. station_obs sources plot through
SURFACE (main_window_overlays.py); their drawer buttons are SurfaceControls'.
Sounding sources show in the Skew-T viewer (main_window_sounding.py).
Everything is live only, except overlays with archive = true."""

import logging

from PyQt6.QtCore import QTimer

import settings
from data.sources import sounding_key
from ui.widgets.layer_order_pill import register_layer

log = logging.getLogger("ui.app.main_window")


def source_layer_key(source_id: str) -> str:
    """The key a source has in the layer pill and layer health."""
    return f"source:{source_id}"


class MainWindowSourcesMixin:

    def _mode_sources(self, kind: str) -> tuple:
        """The sources of *kind* this mode shows: all of them live; in
        ARCHIVE only the overlays with archive = true."""
        sources = settings.SETTINGS.sources_of(kind)
        if self._archive:
            return tuple(s for s in sources if s.kind == "overlay" and s.archive)
        return sources

    def _init_source_toolbar_buttons(self, tb) -> None:
        """Toolbar buttons for the sources with where = "toolbar" (in the data
        group, after SURFACE), and every live source's layer-pill entry."""
        self._source_btns = {}
        for source in self._mode_sources("station_obs") + self._mode_sources("overlay"):
            parts = (("fill", "line", "circle", "label") if source.kind == "overlay" else ())
            register_layer(source_layer_key(source.id), source.name,
                           [f"source-{source.id}-{p}" for p in parts])
            if source.where == "toolbar":
                btn = tb.add_button(source.short, source.name, source.icon, overflow=True)
                self._source_btns[source.id] = self._mark_key_needed(btn, source)

    def _init_overlay_drawer_buttons(self) -> None:
        """MAP drawer buttons for the overlays that sit in the drawer."""
        for source in self._mode_sources("overlay"):
            if source.where == "drawer":
                btn = self.map_controls.add_overlay_button(source.short, source.name)
                self._source_btns[source.id] = self._mark_key_needed(btn, source)

    @staticmethod
    def _mark_key_needed(btn, source):
        missing = source.missing_key()
        if missing:
            btn.setEnabled(False)
            btn.setToolTip(f"{source.name}: needs {missing} in .env")
        return btn

    def _init_overlay_sources(self) -> None:
        """The overlay fetcher, wired to the overlay buttons."""
        from data.fetchers.overlay_fetcher import OverlayFetcher  # noqa: PLC0415
        sources = self._mode_sources("overlay")
        if not sources:
            return
        self._overlay_fetcher = OverlayFetcher(parent=self, sources=sources)
        self._overlay_fetcher.overlay_ready.connect(self._on_overlay_ready)
        self._overlay_fetcher.error.connect(self._on_overlay_error)
        for source in sources:
            btn = self._source_btns.get(source.id)
            if btn is not None:
                btn.toggled.connect(lambda on, key=source.id: self._on_overlay_toggled(key, on))

    def _on_overlay_toggled(self, key: str, on: bool) -> None:
        self._overlay_fetcher.set_enabled(key, on)
        self.map_widget.set_source_overlay_visible(key, on)
        self._set_layer_active(source_layer_key(key), on)

    def _on_overlay_ready(self, key: str, geojson: dict) -> None:
        source = self._overlay_fetcher.sources[key]
        self.map_widget.set_source_overlay(key, geojson, source.style)
        # the layer was hidden while it loaded, or the button went off
        btn = self._source_btns.get(key)
        self.map_widget.set_source_overlay_visible(key, btn is None or btn.isChecked())

    def _on_overlay_error(self, key: str, msg: str) -> None:
        self.status_msg_label.setText(msg)

    def _source_buttons_state(self) -> list[str]:
        return [key for key, btn in getattr(self, "_source_btns", {}).items() if btn.isChecked()]

    def _restore_source_buttons(self, keys) -> None:
        for key in keys or ():
            btn = getattr(self, "_source_btns", {}).get(key)
            if btn is not None and btn.isEnabled() and not btn.isChecked():
                btn.setChecked(True)

    # -- sounding sources ---------------------------------------------------

    def _init_sounding_sources(self) -> None:
        """SOUNDING drawer buttons for the sounding sources, a fetcher for
        the viewer and one for comparisons, and the refresh timer."""
        self._last_source_ssets = {}
        sources = self._mode_sources("sounding")
        if not sources:
            return
        from data.fetchers.source_sounding_fetcher import SourceSoundingFetcher  # noqa: PLC0415
        from ui.sounding.theme import register_source  # noqa: PLC0415
        for source in sources:
            key = sounding_key(source.id)
            register_source(key, source.short)
            self.sounding_controls.add_source_button(key, source.short, source.name,
                                                     source.missing_key())
        self._source_sounding_fetcher = SourceSoundingFetcher(sources, self)
        self._source_sounding_fetcher.sounding_ready.connect(self._on_sounding_ready)
        self._source_sounding_fetcher.fetch_error.connect(self._on_sounding_error)
        self._comp_source_sounding_fetcher = SourceSoundingFetcher(sources, self)
        self._comp_source_sounding_fetcher.sounding_ready.connect(self._on_comp_sounding_ready)
        self._comp_source_sounding_fetcher.fetch_error.connect(self._on_sounding_error)
        self._source_sounding_refresh_timer = QTimer(self)
        self._source_sounding_refresh_timer.setSingleShot(True)
        self._source_sounding_refresh_timer.timeout.connect(self._refresh_source_sounding_if_visible)

    @staticmethod
    def _sounding_source_id(key: str) -> str:
        return key.split(":", 1)[1] if key.startswith("source:") else ""

    def _fetch_source_sounding(self, key: str, comparison: bool = False) -> None:
        fetcher = getattr(self, "_comp_source_sounding_fetcher" if comparison
                          else "_source_sounding_fetcher", None)
        source = fetcher.sources.get(self._sounding_source_id(key)) if fetcher else None
        if source is None:
            return
        self.status_msg_label.setText(f"Fetching {source.name}…")
        fetcher.fetch(source.id)

    def _remember_source_sounding(self, sset, shown: bool) -> None:
        """Keep a sounding source's latest set for comparisons; while the
        viewer shows it, refetch it every refresh_min."""
        if not sset.source.startswith("source:"):
            if shown and hasattr(self, "_source_sounding_refresh_timer"):
                self._source_sounding_refresh_timer.stop()
            return
        self._last_source_ssets[sset.source] = sset
        if shown:
            source = self._source_sounding_fetcher.sources[self._sounding_source_id(sset.source)]
            self._source_sounding_refresh_timer.start(int(source.refresh_min * 60_000))

    def _refresh_source_sounding_if_visible(self) -> None:
        dlg = self._sounding_dialog_obj
        sset = getattr(dlg, "_sset", None)
        if dlg is not None and dlg.isVisible() and sset is not None and sset.source.startswith("source:"):
            self._fetch_source_sounding(sset.source)
