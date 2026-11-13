"""Color table for GOES infrared (band 13) brightness temperature.

Warm surfaces and low cloud are grey (dark when warm, light when cold);
from -30 °C cloud tops step into colors, so deep convection stands out:
blue, green, yellow, red, purple, and white for the coldest overshooting tops.
"""
from __future__ import annotations

import numpy as np

# (kelvin, (r, g, b)), coldest first. The pair at 243 K is the step from grey
# into the enhancement.
IR_STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (173.0, (255, 255, 255)),
    (183.0, (255, 255, 255)),   # -90 °C
    (193.0, (120, 20, 130)),    # -80 °C
    (203.0, (225, 35, 30)),     # -70 °C
    (213.0, (250, 230, 40)),    # -60 °C
    (223.0, (40, 185, 60)),     # -50 °C
    (233.0, (30, 90, 225)),     # -40 °C
    (242.99, (120, 200, 255)),
    (243.0, (230, 230, 230)),   # -30 °C
    (323.0, (10, 10, 10)),      # 50 °C
]
IR_MIN_K = IR_STOPS[0][0]
IR_MAX_K = IR_STOPS[-1][0]

_LUT_STEP = 0.25
_LUT_K = np.arange(IR_MIN_K, IR_MAX_K + _LUT_STEP, _LUT_STEP, dtype=np.float32)
_LUT = np.stack(
    [np.interp(_LUT_K, [k for k, _ in IR_STOPS], [c[i] for _, c in IR_STOPS]) for i in range(3)],
    axis=-1,
).round().astype(np.uint8)


def ir_rgb(kelvin: np.ndarray) -> np.ndarray:
    """(..., 3) uint8 colors for brightness temperatures in kelvin; values
    outside the table take its end colors and NaN the warm end."""
    k = np.nan_to_num(np.asarray(kelvin, dtype=np.float32), nan=IR_MAX_K)
    idx = np.clip(((k - IR_MIN_K) / _LUT_STEP).round(), 0, len(_LUT) - 1).astype(np.intp)
    return _LUT[idx]
