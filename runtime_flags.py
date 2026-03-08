"""Runtime flags configured from CLI options."""

from dataclasses import dataclass


@dataclass
class RuntimeFlags:
    debug_run: int = 0
    runtime_safe: bool = False
    safe_map_mode: bool = False
    enable_startup_toggles: bool = False
    disable_radar: bool = False
    disable_mqtt: bool = False
    disable_vehicle_fetcher: bool = False
    disable_annotations: bool = False
    disable_deploy_locs: bool = False
    disable_data_inputs: bool = False
    mqtt_no_tls: bool = False


FLAGS = RuntimeFlags()


def reset_flags() -> None:
    global FLAGS
    FLAGS = RuntimeFlags()


def apply_debug_run_profile(level: int | None) -> None:
    if level is None:
        return

    FLAGS.debug_run = level

    # Profiles are intentionally coarse for quick field diagnostics.
    # 0: normal
    # 1: map-safe only
    # 2: runtime-safe (also safe-map)
    # 3: disable radar path
    # 4: disable MQTT path
    # 5: minimal/offline core shell
    # 6: MQTT diagnostics (no TLS)
    if level == 1:
        FLAGS.safe_map_mode = True
    elif level == 2:
        FLAGS.runtime_safe = True
        FLAGS.safe_map_mode = True
    elif level == 3:
        FLAGS.enable_startup_toggles = True
        FLAGS.disable_radar = True
    elif level == 4:
        FLAGS.enable_startup_toggles = True
        FLAGS.disable_mqtt = True
    elif level == 5:
        FLAGS.enable_startup_toggles = True
        FLAGS.runtime_safe = True
        FLAGS.safe_map_mode = True
        FLAGS.disable_radar = True
        FLAGS.disable_mqtt = True
        FLAGS.disable_vehicle_fetcher = True
        FLAGS.disable_annotations = True
        FLAGS.disable_deploy_locs = True
        FLAGS.disable_data_inputs = True
    elif level == 6:
        FLAGS.mqtt_no_tls = True


def apply_overrides(**kwargs: bool | int | None) -> None:
    for name, value in kwargs.items():
        if value is None:
            continue
        if hasattr(FLAGS, name):
            setattr(FLAGS, name, value)


def finalize_flags() -> None:
    # Any explicit component disable implies toggle mode is enabled.
    if any([
        FLAGS.disable_radar,
        FLAGS.disable_mqtt,
        FLAGS.disable_vehicle_fetcher,
        FLAGS.disable_annotations,
        FLAGS.disable_deploy_locs,
        FLAGS.disable_data_inputs,
    ]):
        FLAGS.enable_startup_toggles = True

    # MQTT-off implies dependent systems off.
    if FLAGS.disable_mqtt:
        FLAGS.disable_vehicle_fetcher = True
        FLAGS.disable_annotations = True
        FLAGS.disable_data_inputs = True