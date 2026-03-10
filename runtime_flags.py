# Bobby Saba - functions to configure runtime flags

# import required packages
from dataclasses import dataclass

# TABLE OF FLAGS AND WHAT THEY CORRESPOND TO
# ---------------------------------------- # 
# 0: normal
# 1: map-safe only
# 2: runtime-safe (also safe-map)
# 3: disable radar path
# 4: disable MQTT path
# 5: minimal/offline core shell
# 6: MQTT diagnostics (no TLS)

# dataclass for runtime flags
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

# global flags
FLAGS = RuntimeFlags()

# function to reset flags
def reset_flags() -> None:
    # pylint: disable=global-statement
    global FLAGS

    # reset flags
    FLAGS = RuntimeFlags()

# function to apply debug run profile
def apply_debug_run_profile(level: int | None) -> None:
    # if no level is specified
    if level is None:
        # do not run in a debug profile
        return

    # obtain the level provided in the command line
    FLAGS.debug_run = level

    # set appropriate flags based on chart above
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

# function to apply overrides
def apply_overrides(**kwargs: bool | int | None) -> None:
    # parse through any provided overrides
    for name, value in kwargs.items():
        # if there is none provided
        if value is None:
            # skip
            continue

        # apply the override
        if hasattr(FLAGS, name):

            # pylint: disable=protected-access
            setattr(FLAGS, name, value)

# function to finalize flags
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