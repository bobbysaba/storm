from dataclasses import dataclass

import runtime_flags


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    default_enabled: bool = True
    admin_only: bool = False


FEATURES = {
    "hrrr": Feature(
        key="hrrr",
        label="HRRR overlays",
        default_enabled=True,
        admin_only=True,
    ),
    "mesoanalysis": Feature(
        key="mesoanalysis",
        label="Mesoanalysis overlays",
        default_enabled=True,
        admin_only=True,
    ),
    "sfcoa": Feature(
        key="sfcoa",
        label="SFCOA overlays",
        default_enabled=True,
        admin_only=False,
    ),
}


def is_enabled(key: str) -> bool:
    feature = FEATURES[key]
    if feature.admin_only and not runtime_flags.FLAGS.admin_mode:
        return False
    return feature.default_enabled
