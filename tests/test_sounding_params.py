import numpy as np
from metpy.units import units

from ui.sounding.params import _convective_temperature_f


def test_convective_temperature_uses_metpy_ccl_api():
    pressure = [993, 957, 925, 886, 850, 813, 798, 732, 716, 700] * units.hPa
    temperature = [34.6, 31.1, 27.8, 24.3, 21.4, 19.6, 18.7, 13, 13.5, 13] * units.degC
    dewpoint = [19.6, 18.7, 17.8, 16.3, 12.4, -0.4, -3.8, -6, -13.2, -11] * units.degC

    conv_t = _convective_temperature_f(pressure, temperature, dewpoint)

    assert conv_t is not None
    np.testing.assert_allclose(conv_t, 101.1893903, atol=1e-4)
