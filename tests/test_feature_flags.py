"""Tests for admin-gated feature flags."""

import feature_flags
import runtime_flags as rf


def setup_function():
    rf.reset_flags()


def test_admin_features_disabled_by_default():
    assert feature_flags.is_enabled("hrrr") is False
    assert feature_flags.is_enabled("mesoanalysis") is False


def test_admin_features_enabled_in_admin_mode():
    rf.FLAGS.admin_mode = True
    assert feature_flags.is_enabled("hrrr") is True
    assert feature_flags.is_enabled("mesoanalysis") is True
