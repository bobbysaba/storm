from types import SimpleNamespace

import numpy as np
import pytest

from core.velocity_processing import dealias, sweep_nyquist


def _fold(v, nyquist):
    return (v + nyquist) % (2 * nyquist) - nyquist


def test_region_based_dealiasing_recovers_a_folded_wind_field():
    """A smooth 35 m/s flow seen by a radar with an 20 m/s Nyquist folds on
    the upwind/downwind beams; dealiasing must unfold it back."""
    az = np.arange(0, 360, 1.0)
    ranges = 2.0 + np.arange(200) * 0.25
    true = np.outer(35.0 * np.cos(np.deg2rad(az - 225.0)), np.ones_like(ranges)).astype(np.float32)
    folded = _fold(true, 20.0).astype(np.float32)
    folded[:, 150:] = np.nan                                    # no echo far out
    out = dealias(folded, 20.0)
    good = np.isfinite(folded)
    assert np.isnan(out[~good]).all()                           # never invents gates
    assert np.abs(out[good] - true[good]).max() < 0.5
    assert np.nanmax(np.abs(folded)) <= 20.0 < np.nanmax(np.abs(out))


def test_noisy_gappy_sweep_with_a_double_fold_comes_back():
    """A full-size sweep: a flow growing with range plus a strong couplet
    (up to ~2.5 x Nyquist), noise, speckle, a sector with no echo and the
    far range empty. Regions across gaps are tied together."""
    rng = np.random.default_rng(0)
    az = np.arange(0, 360, 0.5)
    ranges = 2.0 + np.arange(600) * 0.25
    a, r = np.meshgrid(az, ranges, indexing="ij")
    true = (30 * np.cos(np.deg2rad(a - 200)) * (1 + r / 150)
            + 25 * np.exp(-((a - 90) ** 2 / 50 + (r - 100) ** 2 / 30)))
    noisy = (true + rng.normal(0, 1.5, true.shape)).astype(np.float32)
    folded = _fold(noisy, 26.0).astype(np.float32)
    folded[rng.random(true.shape) < 0.3] = np.nan
    folded[100:140, :] = np.nan
    folded[:, 500:] = np.nan
    out = dealias(folded, 26.0)
    good = np.isfinite(folded)
    assert np.nanmax(np.abs(noisy[good])) > 2 * 26.0
    assert np.abs(out[good] - noisy[good]).max() < 1e-3
    assert np.isnan(out[~good]).all()


def test_unaliased_field_and_input_are_left_alone():
    vel = np.outer(np.linspace(-15, 15, 90), np.ones(40)).astype(np.float32)
    vel[10:20, 5:9] = np.nan
    before = vel.copy()
    out = dealias(vel, 25.0)
    np.testing.assert_array_equal(np.isnan(out), np.isnan(vel))
    np.testing.assert_allclose(out[np.isfinite(vel)], vel[np.isfinite(vel)], atol=1e-5)
    np.testing.assert_array_equal(vel, before)                  # input untouched


def test_unfolding_follows_rays_across_north():
    """The fold sits next to north: the flow only lines up with its unfolded
    neighbors when the last ray meets the first."""
    az = np.arange(0, 360, 1.0)
    true = np.outer(30.0 * np.cos(np.deg2rad(az - 5.0)), np.ones(50))
    folded = _fold(true, 20.0).astype(np.float32)
    out = dealias(folded, 20.0)
    np.testing.assert_allclose(out, true, atol=1e-4)


def test_bad_input_raises():
    with pytest.raises(ValueError):
        dealias(np.full((4, 4), np.nan, dtype=np.float32), 20.0)
    with pytest.raises(ValueError):
        dealias(np.zeros((4, 4), dtype=np.float32), 0.0)
    with pytest.raises(ValueError):
        dealias(np.zeros((4, 4), dtype=np.float32), float("nan"))


def test_nyquist_from_either_message_type():
    msg31 = [SimpleNamespace(radial_consts=SimpleNamespace(nyq_vel=28.04), header=None)]
    msg1 = [SimpleNamespace(header=SimpleNamespace(nyq_vel=23.5))]
    missing = [SimpleNamespace(header=SimpleNamespace(el_angle=0.5))]
    assert sweep_nyquist(msg31) == 28.04
    assert sweep_nyquist(msg1) == 23.5
    assert sweep_nyquist(missing) is None
