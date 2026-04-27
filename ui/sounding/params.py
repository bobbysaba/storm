
import numpy as np
import metpy.calc as mpcalc
from metpy.units import units

from core.sounding import Sounding
from ui.sounding.theme import _MUTED


_THRESHOLDS: dict[str, list] = {
    "sbcape":    [(0, _MUTED), (500, "#f9ca24"), (1500, "#f0932b"), (3000, "#eb4d4b")],
    "mlcape":    [(0, _MUTED), (500, "#f9ca24"), (1500, "#f0932b"), (3000, "#eb4d4b")],
    "mucape":    [(0, _MUTED), (500, "#f9ca24"), (1500, "#f0932b"), (3000, "#eb4d4b")],
    "sbcin":     [(-250, "#eb4d4b"), (-150, "#f0932b"), (-75, "#f9ca24"), (-25, _MUTED)],
    "mlcin":     [(-250, "#eb4d4b"), (-150, "#f0932b"), (-75, "#f9ca24"), (-25, _MUTED)],
    "mucin":     [(-250, "#eb4d4b"), (-150, "#f0932b"), (-75, "#f9ca24"), (-25, _MUTED)],
    "srh_500":   [(0, _MUTED), (50,  "#f9ca24"), (100, "#f0932b"), (200, "#eb4d4b")],
    "srh01":     [(0, _MUTED), (100, "#f9ca24"), (200, "#f0932b"), (400, "#eb4d4b")],
    "srh03":     [(0, _MUTED), (150, "#f9ca24"), (300, "#f0932b"), (500, "#eb4d4b")],
    "shear_500": [(0, _MUTED), (10,  "#f9ca24"), (20,  "#f0932b"), (30,  "#eb4d4b")],
    "shear01":   [(0, _MUTED), (15,  "#f9ca24"), (25,  "#f0932b"), (35,  "#eb4d4b")],
    "shear03":   [(0, _MUTED), (20,  "#f9ca24"), (30,  "#f0932b"), (45,  "#eb4d4b")],
    "shear06":   [(0, _MUTED), (30,  "#f9ca24"), (40,  "#f0932b"), (50,  "#eb4d4b")],
    "srw_500":   [(0, _MUTED), (10,  "#f9ca24"), (20,  "#f0932b"), (30,  "#eb4d4b")],
    "srw01":     [(0, _MUTED), (15,  "#f9ca24"), (25,  "#f0932b"), (35,  "#eb4d4b")],
    "srw03":     [(0, _MUTED), (15,  "#f9ca24"), (25,  "#f0932b"), (35,  "#eb4d4b")],
    "srw06":     [(0, _MUTED), (15,  "#f9ca24"), (25,  "#f0932b"), (35,  "#eb4d4b")],
    "stp":       [(0, _MUTED), (1,   "#f9ca24"), (3,   "#f0932b"), (5,   "#eb4d4b"),
                  (8, "#c0392b")],
    "scp":       [(0, _MUTED), (2,   "#f9ca24"), (4,   "#f0932b"), (8,   "#eb4d4b")],
    "ehi":       [(0, _MUTED), (1,   "#f9ca24"), (2,   "#f0932b"), (3,   "#eb4d4b")],
    "cape3km":   [(0, _MUTED), (50,  "#f9ca24"), (100, "#f0932b"), (200, "#eb4d4b")],
    "lr75":      [(0, _MUTED), (7.0, "#f9ca24"), (8.0, "#f0932b"), (9.0, "#eb4d4b")],
    "lr03":      [(5, _MUTED), (7.0, "#f9ca24"), (8.0, "#f0932b"), (9.0, "#eb4d4b")],
    "sfc_the":   [(320, _MUTED), (335, "#f9ca24"), (350, "#f0932b"), (365, "#eb4d4b")],
    "pw":        [(0, _MUTED), (25,  "#f9ca24"), (38,  "#4fc3f7"), (50,  "#74b9ff")],
}


def _threshold_color(key: str, value) -> str | None:
    thresholds = _THRESHOLDS.get(key)
    if not thresholds or value is None:
        return None
    if not np.isfinite(value):
        return None
    color = thresholds[0][1]
    for threshold, c in thresholds:
        if value >= threshold:
            color = c
    return color


def _finite_float(value) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    return out if np.isfinite(out) else None


def _convective_temperature_f(pres, temp, dewp, hgt=None) -> float | None:
    """Return convective temperature in degF for MetPy API variants."""
    try:
        if hasattr(mpcalc, "ccl"):
            if hgt is None:
                _, _, conv_t = mpcalc.ccl(pres, temp, dewp)
            else:
                _, _, conv_t = mpcalc.ccl(pres, temp, dewp, height=hgt)
            return _finite_float(conv_t.to("degF").m)

        ccl_p, ccl_t = mpcalc.convective_condensation_level(pres, temp, dewp)
        conv_t_k = float(ccl_t.to("K").m) * (
            float(pres[0].to("hPa").m) / float(ccl_p.to("hPa").m)
        ) ** 0.2854
        conv_t_c = conv_t_k - 273.15
        return _finite_float(conv_t_c * 9.0 / 5.0 + 32.0)
    except Exception:
        return None

_SCALAR_PARAMS = [
    ("lr75",    "LR 700-500", "°C/km", _MUTED),
    ("lr03",    "LR 0-3 km",  "°C/km", _MUTED),
    ("sfc_the", "SFC θe",     "K",     "#ff9f43"),
    ("pw",      "PW",         "mm",    "#4ecdc4"),
    ("conv_t",  "Conv Temp",  "°F",    "#ff9f43"),
    ("stp",     "STP",        "",      "#fd79a8"),
    ("scp",     "SCP",        "",      "#b39ddb"),
    ("ehi",     "EHI",        "",      "#fd79a8"),
    ("cape3km", "0-3km CAPE", "J/kg",  "#e17055"),
]

# pressure levels used for wind barbs and hodograph thinning.
_MANDATORY_PRES = np.array(
    [1000, 975, 950, 925, 900, 875, 850, 825, 800, 750, 700,
     650, 600, 550, 500, 450, 400, 350, 300, 250, 200, 150, 100],
    dtype=float
)

def _thin_to_mandatory(pres_arr: np.ndarray, tol: float = 15.0) -> np.ndarray:
    """Return sorted indices of levels closest to each mandatory pressure level.

    Skips a mandatory level if no data point falls within *tol* hPa of it.
    Each data index is included at most once.
    """
    indices: list[int] = []
    seen: set[int] = set()
    for mp in _MANDATORY_PRES:
        if mp > pres_arr[0] + tol or mp < pres_arr[-1] - tol:
            continue  # outside the sounding's pressure range
        i = int(np.argmin(np.abs(pres_arr - mp)))
        if np.abs(pres_arr[i] - mp) <= tol and i not in seen:
            seen.add(i)
            indices.append(i)
    return np.array(sorted(indices), dtype=int)


def _p_at_t(snd: Sounding, target_c: float) -> float | None:
    """Pressure (hPa) where temperature first crosses target_c °C from surface up."""
    for i in range(len(snd.temperature) - 1):
        t0, t1 = snd.temperature[i], snd.temperature[i + 1]
        if (t0 >= target_c >= t1) or (t0 <= target_c <= t1):
            frac = (target_c - t0) / (t1 - t0) if t1 != t0 else 0.0
            return float(snd.pressure[i] + frac * (snd.pressure[i + 1] - snd.pressure[i]))
    return None


def _vt_cape_cin(pres, temp, dewp, parcel_profile, init_pres, init_temp, init_dewp):
    """Compute CAPE/CIN with virtual-temperature correction.

    Converts both the environment sounding and the parcel profile to virtual
    temperature before integrating buoyancy.  Below the LCL the parcel mixing
    ratio is held constant at the surface value; above the LCL it equals the
    saturation mixing ratio at the parcel temperature.

    Falls back to standard (non-virtual) cape_cin if the correction fails.
    """
    try:
        # environment virtual temperature
        _mr_env = mpcalc.saturation_mixing_ratio(pres, dewp)
        _tv_env = mpcalc.virtual_temperature(temp, _mr_env).to("degC")

        # parcel virtual temperature
        _r0     = mpcalc.saturation_mixing_ratio(init_pres.reshape(1)
                      if hasattr(init_pres, "reshape") and init_pres.shape == ()
                      else init_pres[0:1],
                  init_dewp.reshape(1)
                      if hasattr(init_dewp, "reshape") and init_dewp.shape == ()
                      else init_dewp[0:1])
        _r_sat  = mpcalc.saturation_mixing_ratio(pres, parcel_profile.to("degC"))
        _lcl_p, _ = mpcalc.lcl(init_pres if init_pres.shape != () else init_pres[0],
                                init_temp  if init_temp.shape  != () else init_temp[0],
                                init_dewp  if init_dewp.shape  != () else init_dewp[0])
        _below  = pres.m >= float(_lcl_p.to("hPa").m)
        _r_prc  = np.where(_below, float(_r0.to("kg/kg").m),
                           _r_sat.to("kg/kg").m) * units("kg/kg")
        _tv_prc = mpcalc.virtual_temperature(parcel_profile, _r_prc).to("degC")

        cape, cin = mpcalc.cape_cin(pres, _tv_env, dewp, _tv_prc)
        return float(cape.to("J/kg").m), float(cin.to("J/kg").m)
    except Exception:
        cape, cin = mpcalc.cape_cin(pres, temp, dewp, parcel_profile)
        return float(cape.to("J/kg").m), float(cin.to("J/kg").m)
