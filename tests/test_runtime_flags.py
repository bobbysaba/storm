"""Tests for runtime_flags — debug profiles, overrides, and finalization."""

import runtime_flags as rf


class TestDebugProfiles:
    def setup_method(self):
        rf.reset_flags()

    def test_default_flags(self):
        assert rf.FLAGS.debug_run == 0
        assert rf.FLAGS.runtime_safe is False
        assert rf.FLAGS.safe_map_mode is False
        assert rf.FLAGS.disable_radar is False
        assert rf.FLAGS.disable_mqtt is False

    def test_level_none(self):
        rf.apply_debug_run_profile(None)
        assert rf.FLAGS.debug_run == 0

    def test_level_1_safe_map(self):
        rf.apply_debug_run_profile(1)
        assert rf.FLAGS.debug_run == 1
        assert rf.FLAGS.safe_map_mode is True
        assert rf.FLAGS.runtime_safe is False

    def test_level_2_runtime_safe(self):
        rf.apply_debug_run_profile(2)
        assert rf.FLAGS.debug_run == 2
        assert rf.FLAGS.runtime_safe is True
        assert rf.FLAGS.safe_map_mode is True

    def test_level_3_disable_radar(self):
        rf.apply_debug_run_profile(3)
        assert rf.FLAGS.disable_radar is True
        assert rf.FLAGS.enable_startup_toggles is True

    def test_level_4_disable_mqtt(self):
        rf.apply_debug_run_profile(4)
        assert rf.FLAGS.disable_mqtt is True
        assert rf.FLAGS.enable_startup_toggles is True
        assert rf.FLAGS.disable_radar is False

    def test_level_5_minimal(self):
        rf.apply_debug_run_profile(5)
        assert rf.FLAGS.runtime_safe is True
        assert rf.FLAGS.safe_map_mode is True
        assert rf.FLAGS.disable_radar is True
        assert rf.FLAGS.disable_mqtt is True
        assert rf.FLAGS.disable_annotations is True
        assert rf.FLAGS.disable_deploy_locs is True
        assert rf.FLAGS.disable_data_inputs is True
        assert rf.FLAGS.enable_startup_toggles is True

    def test_level_6_mqtt_no_tls(self):
        rf.apply_debug_run_profile(6)
        assert rf.FLAGS.mqtt_no_tls is True


class TestOverrides:
    def setup_method(self):
        rf.reset_flags()

    def test_apply_override(self):
        rf.apply_overrides(disable_radar=True)
        assert rf.FLAGS.disable_radar is True

    def test_none_values_ignored(self):
        rf.apply_overrides(disable_radar=None)
        assert rf.FLAGS.disable_radar is False

    def test_unknown_attr_ignored(self):
        # should not raise
        rf.apply_overrides(nonexistent_flag=True)


class TestFinalize:
    def setup_method(self):
        rf.reset_flags()

    def test_disable_component_enables_toggles(self):
        rf.FLAGS.disable_radar = True
        rf.finalize_flags()
        assert rf.FLAGS.enable_startup_toggles is True

    def test_disable_mqtt_cascades(self):
        rf.FLAGS.disable_mqtt = True
        rf.finalize_flags()
        assert rf.FLAGS.disable_annotations is True
        assert rf.FLAGS.disable_data_inputs is True
        assert rf.FLAGS.enable_startup_toggles is True

    def test_no_disables_leaves_toggles_off(self):
        rf.finalize_flags()
        assert rf.FLAGS.enable_startup_toggles is False
