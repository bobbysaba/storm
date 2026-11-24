"""The sounding window's plots (ui/sounding/dialog.py): the Skew-T and the
hodograph inset in its corner, with any comparison soundings overlaid."""
import logging

import metpy.calc as mpcalc
import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.transforms import blended_transform_factory
from metpy.plots import Hodograph, SkewT
from metpy.units import units

from core import units as display_units
from core.sounding import Sounding
from ui.sounding.params import _p_at_t, _thin_to_mandatory
from ui.sounding.theme import (
    _ACCENT, _AX_BG, _BARB_CLR, _BORDER, _DEWP_CLR, _EIL_CLR, _FIG_BG, _HODO_LAYERS, _LM_CLR, _MUTED,
    _OBS_MOTION_CLR, _PARCEL_CLR, _RM_CLR, _SOURCE_LS, _TEMP_CLR, _TEXT, _VPARCEL_CLR, _VTEMP_CLR,
)

log = logging.getLogger(__name__)


class SoundingPlotsMixin:
    """Drawing for SoundingDialog; uses its figure, its EIL bounds and the
    observed storm motion."""

    def _draw_skewt(self, snd: Sounding, secondary_snds: list | None = None):
        skewt = SkewT(self._fig, rotation=45, rect=(0.07, 0.04, 0.91, 0.93))
        ax = skewt.ax
        self._skewt_ax = ax

        ax.set_facecolor(_AX_BG)
        for sp in ax.spines.values():
            sp.set_edgecolor(_BORDER)
        ax.tick_params(colors=_MUTED, labelsize=8)
        ax.set_xlabel("Temperature (°C)", color=_MUTED, fontsize=8)
        ax.set_ylabel("Pressure (hPa)",   color=_MUTED, fontsize=8)
        ax.grid(True, color=_BORDER, alpha=0.45, linewidth=0.5)

        pres = snd.pressure    * units.hPa
        temp = snd.temperature * units.degC
        dewp = snd.dewpoint    * units.degC
        u_kt = (snd.u_wind * units("m/s")).to("knots")
        v_kt = (snd.v_wind * units("m/s")).to("knots")

        # index set for plotted traces.  High-resolution soundings (>200 levels,
        _pt = _thin_to_mandatory(snd.pressure) if len(snd.pressure) > 200 \
              else np.arange(len(snd.pressure))

        skewt.plot_dry_adiabats(
            t0=np.arange(-40, 200, 10) * units.degC,
            alpha=0.14, colors="saddlebrown", linewidths=0.7,
        )
        skewt.plot_moist_adiabats(
            t0=np.arange(-20, 35, 5) * units.degC,
            alpha=0.14, colors="forestgreen", linewidths=0.7,
        )
        skewt.plot_mixing_lines(
            pressure=np.arange(1000, 99, -50) * units.hPa,
            alpha=0.14, colors="dodgerblue", linewidths=0.7,
        )

        # dgz shading
        p_neg10 = _p_at_t(snd, -10.0)
        p_neg20 = _p_at_t(snd, -20.0)
        if p_neg10 is not None and p_neg20 is not None:
            ax.axhspan(p_neg10, p_neg20, alpha=0.07, color="#4fc3f7", zorder=1)

        # main traces — thinned for high-res soundings, full-res otherwise
        skewt.plot(pres[_pt], temp[_pt], color=_TEMP_CLR, linewidth=2, zorder=5)
        skewt.plot(pres[_pt], dewp[_pt], color=_DEWP_CLR, linewidth=2, zorder=5)

        # environment virtual temperature (dashed, warm tint).
        mr_env = tv_env = None
        try:
            mr_env = mpcalc.saturation_mixing_ratio(pres, dewp)
            tv_env = mpcalc.virtual_temperature(temp, mr_env).to("degC")
            skewt.plot(pres[_pt], tv_env[_pt], color=_VTEMP_CLR, linewidth=1.1,
                       linestyle="--", alpha=0.6, zorder=5)
        except Exception as e:
            log.debug("virtual temperature plot failed: %s", e)

        # wind barbs at mandatory pressure levels only
        thin = _thin_to_mandatory(snd.pressure)
        skewt.plot_barbs(pres[thin], u_kt[thin], v_kt[thin], color=_BARB_CLR,
                         linewidth=0.8, length=6)

        # parcel profile (white dashed) + virtual-temperature parcel (red dashed).
        sb_parcel = None
        try:
            sb_parcel = mpcalc.parcel_profile(pres, temp[0], dewp[0])
            skewt.plot(pres[_pt], sb_parcel[_pt], color=_PARCEL_CLR, linewidth=1,
                       linestyle="--", alpha=0.4, zorder=5)
        except Exception as e:
            log.debug("parcel profile failed: %s", e)

        if sb_parcel is not None:
            _vt_shaded = False
            try:
                # virtual parcel: below LCL mixing ratio is constant (= surface value);
                _r0      = mpcalc.saturation_mixing_ratio(pres[0:1], dewp[0:1])
                _r_sat_p = mpcalc.saturation_mixing_ratio(pres, sb_parcel.to("degC"))
                _lcl_p, _ = mpcalc.lcl(pres[0], temp[0], dewp[0])
                _below = pres.m >= float(_lcl_p.to("hPa").m)
                _r_prc = (np.where(_below, float(_r0.to("kg/kg").m),
                                   _r_sat_p.to("kg/kg").m)) * units("kg/kg")
                sb_parcel_vt = mpcalc.virtual_temperature(sb_parcel, _r_prc).to("degC")
                skewt.plot(pres[_pt], sb_parcel_vt[_pt], color=_VPARCEL_CLR,
                           linewidth=1.1, linestyle="--", alpha=0.75, zorder=5)
                if tv_env is not None:
                    skewt.shade_cape(pres, tv_env, sb_parcel_vt, alpha=0.18, color=_TEMP_CLR)
                    skewt.shade_cin(pres, tv_env, sb_parcel_vt, alpha=0.18, color="#4fc3f7")
                    _vt_shaded = True
            except Exception as e:
                log.debug("virtual parcel profile failed: %s", e)
            if not _vt_shaded:
                # fall back to non-virtual shading
                try:
                    skewt.shade_cape(pres, temp, sb_parcel, alpha=0.18, color=_TEMP_CLR)
                    skewt.shade_cin(pres, temp, sb_parcel, alpha=0.18, color="#4fc3f7")
                except Exception as e:
                    log.debug("CAPE-CIN shading failed: %s", e)

        # lcl marker
        try:
            lcl_p, lcl_t = mpcalc.lcl(pres[0], temp[0], dewp[0])
            skewt.plot(lcl_p, lcl_t, "o", color="cyan", markersize=5,
                       markerfacecolor="none", markeredgewidth=1.5, zorder=6)
        except Exception as e:
            log.debug("LCL marker failed: %s", e)

        # surface T/Td labels (SHARPpy style) — positioned just below trace endpoints
        sfc_t = float(snd.temperature[0])
        sfc_td = float(snd.dewpoint[0])
        sfc_p = float(snd.pressure[0])

        # t/td in the display unit ([units])
        sfc_t_F = display_units.temp(sfc_t)
        sfc_td_F = display_units.temp(sfc_td)

        ax.text(sfc_t, sfc_p + 18, f"{sfc_t_F:.0f}",
                color=_TEMP_CLR, fontsize=8, fontweight="bold",
                ha="center", va="top", zorder=7)
        ax.text(sfc_td, sfc_p + 18, f"{sfc_td_F:.0f}",
                color=_DEWP_CLR, fontsize=8, fontweight="bold",
                ha="center", va="top", zorder=7)

        ax.set_ylim(1050, 100)
        ax.set_xlim(-40, 50)

        _agl_trans = blended_transform_factory(ax.transAxes, ax.transData)
        z_sfc = snd.height[0]
        for agl_m, agl_lbl in [
            (500,  "0.5"), (1000, "1"), (2000, "2"),
            (3000, "3"),   (4000, "4"), (6000, "6"), (9000, "9"),
        ]:
            z_target = z_sfc + agl_m
            if z_target > snd.height[-1]:
                continue
            p_agl = float(np.interp(z_target, snd.height, snd.pressure))
            if not (100 < p_agl < 1050):
                continue
            ax.axhline(p_agl, color="#cc3333", linewidth=0.5,
                       linestyle=":", alpha=0.35, zorder=1)
            ax.text(0.008, p_agl, f"{agl_lbl}",
                    transform=_agl_trans, color="#cc3333",
                    fontsize=6, va="center", ha="left",
                    alpha=0.85, clip_on=False)

        self._cursor_hline = ax.axhline(
            y=850, color=_ACCENT, linewidth=0.9, alpha=0, zorder=10,
        )

        # eil bracket on left spine
        if self._eil_base_p is not None and self._eil_top_p is not None:
            _bl = blended_transform_factory(ax.transAxes, ax.transData)
            ax.plot([0.012, 0.012],
                    [self._eil_base_p, self._eil_top_p],
                    color=_EIL_CLR, linewidth=2.5,
                    transform=_bl, solid_capstyle="round",
                    clip_on=False, zorder=9)
            for p in (self._eil_base_p, self._eil_top_p):
                ax.plot([0.012, 0.038], [p, p],
                        color=_EIL_CLR, linewidth=2.5,
                        transform=_bl, solid_capstyle="round",
                        clip_on=False, zorder=9)
            mid_p = (self._eil_base_p + self._eil_top_p) / 2
            ax.text(0.045, mid_p, "EIL",
                    transform=_bl, color=_EIL_CLR,
                    fontsize=6, va="center", ha="left", alpha=0.85)

        # secondary source overlays — temp/dewpoint traces only (no barbs/parcel/shading)
        if secondary_snds:
            for sec_snd, src_key in secondary_snds:
                try:
                    sec_pres = sec_snd.pressure    * units.hPa
                    sec_temp = sec_snd.temperature * units.degC
                    sec_dewp = sec_snd.dewpoint    * units.degC
                    _sec_pt = (
                        _thin_to_mandatory(sec_snd.pressure)
                        if len(sec_snd.pressure) > 200
                        else np.arange(len(sec_snd.pressure))
                    )
                    ls = _SOURCE_LS[src_key]
                    skewt.plot(sec_pres[_sec_pt], sec_temp[_sec_pt],
                               color=_TEMP_CLR, linewidth=1.7,
                               linestyle=ls, alpha=0.80, zorder=4)
                    skewt.plot(sec_pres[_sec_pt], sec_dewp[_sec_pt],
                               color=_DEWP_CLR, linewidth=1.7,
                               linestyle=ls, alpha=0.80, zorder=4)
                except Exception as e:
                    log.debug("secondary skewt overlay failed for %s: %s", src_key, e)

        ax_hodo = ax.inset_axes([0.675, 0.625, 0.30, 0.36])
        self._draw_hodograph_inset(snd, ax_hodo, secondary_snds)


    def _draw_hodograph_inset(self, snd: Sounding, ax, secondary_snds: list | None = None):
        ax.set_facecolor(_AX_BG + "dd")
        for sp in ax.spines.values():
            sp.set_edgecolor(_BORDER)
        ax.tick_params(left=False, bottom=False,
                       labelleft=False, labelbottom=False)

        h = Hodograph(ax, component_range=60)
        h.add_grid(increment=20, color=_BORDER, alpha=0.7, linewidth=0.6)

        for spd in (20, 40, 60):
            ax.text(spd * -0.72, spd * -0.72, f"{spd}", fontsize=5,
                    color=_MUTED, ha="center", va="center", alpha=0.7)

        # use only levels that have actual wind observations — skipping T/Td-only
        if len(snd.pressure) > 200:
            candidate_idx = _thin_to_mandatory(snd.pressure)
        else:
            candidate_idx = np.arange(len(snd.pressure))
        wind_mask  = ~np.isnan(snd.u_wind[candidate_idx])
        thin       = candidate_idx[wind_mask]
        u_kt       = (snd.u_wind[thin] * units("m/s")).to("knots").magnitude
        v_kt       = (snd.v_wind[thin] * units("m/s")).to("knots").magnitude
        pres_thin  = snd.pressure[thin]
        hgt_thin   = snd.height[thin]
        if not hgt_thin.size:
            # no wind at all (a retrieval): an empty hodograph
            ax.text(0, 0, "no wind", fontsize=7, color=_MUTED, ha="center", va="center")
            return
        hgt_agl_km = (hgt_thin - hgt_thin[0]) / 1000.0

        # gapless height-colored trace via LineCollection
        if len(u_kt) >= 2:
            pts  = np.column_stack([u_kt, v_kt]).reshape(-1, 1, 2)
            segs = np.concatenate([pts[:-1], pts[1:]], axis=1)

            seg_colors = []
            for i in range(len(hgt_agl_km) - 1):
                mid_h = (hgt_agl_km[i] + hgt_agl_km[i + 1]) / 2.0
                clr = _HODO_LAYERS[-1][0]
                for c, lo, hi in _HODO_LAYERS:
                    if lo <= mid_h < hi:
                        clr = c
                        break
                seg_colors.append(clr)

            ax.add_collection(
                LineCollection(segs, colors=seg_colors, linewidth=2.7, zorder=4)
            )

            # eil highlight
            if self._eil_base_p is not None and self._eil_top_p is not None:
                eil_colors = []
                for i in range(len(pres_thin) - 1):
                    p_mid = (pres_thin[i] + pres_thin[i + 1]) / 2.0
                    in_eil = self._eil_top_p <= p_mid <= self._eil_base_p
                    eil_colors.append(_EIL_CLR if in_eil else (0, 0, 0, 0))
                ax.add_collection(
                    LineCollection(segs, colors=eil_colors,
                                   linewidth=5.5, alpha=0.28, zorder=3)
                )

        # height labels
        for km in (1, 3, 6):
            i = int(np.argmin(np.abs(hgt_agl_km - km)))
            if i < len(u_kt):
                ax.annotate(f"{km}", xy=(u_kt[i], v_kt[i]),
                            fontsize=5, color=_TEXT, alpha=0.85,
                            xytext=(3, 3), textcoords="offset points")

        # bunkers storm motion — colored dot markers + corner text
        try:
            wind_valid = (
                np.isfinite(snd.pressure)
                & np.isfinite(snd.height)
                & np.isfinite(snd.u_wind)
                & np.isfinite(snd.v_wind)
            )
            if wind_valid.sum() < 4:
                raise ValueError("not enough finite wind levels for Bunkers motion")
            pres = snd.pressure[wind_valid] * units.hPa
            u_ms = snd.u_wind[wind_valid]   * units("m/s")
            v_ms = snd.v_wind[wind_valid]   * units("m/s")
            hgt  = snd.height[wind_valid]   * units.m
            rm, lm, _ = mpcalc.bunkers_storm_motion(pres, u_ms, v_ms, hgt)

            rm_u_kt = float(rm[0].to("knots").m)
            rm_v_kt = float(rm[1].to("knots").m)
            lm_u_kt = float(lm[0].to("knots").m)
            lm_v_kt = float(lm[1].to("knots").m)
            if not all(np.isfinite(x) for x in (rm_u_kt, rm_v_kt, lm_u_kt, lm_v_kt)):
                raise ValueError("Bunkers motion returned non-finite values")

            rm_spd = float(np.sqrt(rm_u_kt**2 + rm_v_kt**2))
            rm_dir = float((np.degrees(np.arctan2(rm[0].to("m/s").m, rm[1].to("m/s").m)) + 180) % 360)
            lm_spd = float(np.sqrt(lm_u_kt**2 + lm_v_kt**2))
            lm_dir = float((np.degrees(np.arctan2(lm[0].to("m/s").m, lm[1].to("m/s").m)) + 180) % 360)

            ax.plot(rm_u_kt, rm_v_kt, "o", color=_RM_CLR, markersize=5,
                    markeredgecolor=_FIG_BG, markeredgewidth=0.8, zorder=7)
            ax.plot(lm_u_kt, lm_v_kt, "o", color=_LM_CLR, markersize=5,
                    markeredgecolor=_FIG_BG, markeredgewidth=0.8, zorder=7)

            # dir/spd text in top-left corner of hodograph
            ax.text(0.03, 0.97, f"RM  {rm_dir:.0f}°/{rm_spd:.0f}kt",
                    transform=ax.transAxes, color=_RM_CLR,
                    fontsize=5.5, fontweight="bold", va="top", ha="left", zorder=8)
            ax.text(0.03, 0.90, f"LM  {lm_dir:.0f}°/{lm_spd:.0f}kt",
                    transform=ax.transAxes, color=_LM_CLR,
                    fontsize=5.5, fontweight="bold", va="top", ha="left", zorder=8)
        except Exception as e:
            log.debug("Bunkers storm motion failed: %s", e)

        # observed storm motion from the archive storm track
        if self._observed_motion is not None:
            obs_u, obs_v = self._observed_motion
            obs_u_kt, obs_v_kt = obs_u * 1.94384, obs_v * 1.94384
            obs_dir = float((np.degrees(np.arctan2(obs_u, obs_v)) + 180) % 360)
            ax.plot(obs_u_kt, obs_v_kt, "D", color=_OBS_MOTION_CLR, markersize=5,
                    markeredgecolor=_FIG_BG, markeredgewidth=0.8, zorder=7)
            ax.text(0.03, 0.83, f"OBS {obs_dir:.0f}°/{np.hypot(obs_u_kt, obs_v_kt):.0f}kt",
                    transform=ax.transAxes, color=_OBS_MOTION_CLR,
                    fontsize=5.5, fontweight="bold", va="top", ha="left", zorder=8)

        # secondary source hodograph overlays — same height colormap, different linestyle
        if secondary_snds:
            for sec_snd, src_key in secondary_snds:
                try:
                    if len(sec_snd.pressure) > 200:
                        sec_cand = _thin_to_mandatory(sec_snd.pressure)
                    else:
                        sec_cand = np.arange(len(sec_snd.pressure))
                    sec_wind_mask = ~np.isnan(sec_snd.u_wind[sec_cand])
                    sec_thin = sec_cand[sec_wind_mask]
                    if len(sec_thin) < 2:
                        continue
                    sec_u = (sec_snd.u_wind[sec_thin] * units("m/s")).to("knots").magnitude
                    sec_v = (sec_snd.v_wind[sec_thin] * units("m/s")).to("knots").magnitude
                    sec_hgt_agl = (sec_snd.height[sec_thin] - sec_snd.height[sec_thin[0]]) / 1000.0

                    sec_pts  = np.column_stack([sec_u, sec_v]).reshape(-1, 1, 2)
                    sec_segs = np.concatenate([sec_pts[:-1], sec_pts[1:]], axis=1)
                    sec_colors = []
                    for i in range(len(sec_hgt_agl) - 1):
                        mid_h = (sec_hgt_agl[i] + sec_hgt_agl[i + 1]) / 2.0
                        clr = _HODO_LAYERS[-1][0]
                        for c, lo, hi in _HODO_LAYERS:
                            if lo <= mid_h < hi:
                                clr = c
                                break
                        sec_colors.append(clr)

                    ax.add_collection(LineCollection(
                        sec_segs, colors=sec_colors,
                        linewidth=2.0,
                        linestyle=_SOURCE_LS[src_key],
                        alpha=0.75, zorder=3,
                    ))
                except Exception as e:
                    log.debug("secondary hodograph overlay failed for %s: %s", src_key, e)
