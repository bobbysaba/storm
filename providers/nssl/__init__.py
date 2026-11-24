"""NSSL's data provider: everything that needs NSSL access or only makes sense
for NSSL's field program (the mobile mesonet, CLAMPS, the lidar truck).

It ships with STORM and is loaded for everyone (settings.BUILTIN_PROVIDERS)
unless storm.toml has ``[providers.nssl] enabled = false``: its THREDDS data
is public, so ARCHIVE's NSSL drawer (archive/drawer.py) is there for every
team, and the provider trusts the Sectigo intermediate THREDDS leaves out of
its TLS chain itself (AUTH). ``team = true`` (the NSSL profile) makes NSSL
the setup's own team. Live layers that read the NSSL API also need the
API's key: the profile's [[auth]] table for the API names NSSL_API_KEY
(environment or .env).

The rest of NSSL's setup is settings in the profile: recorded history
([archive] history), the day's shared items ([mqtt] shared_items_url), past
deployment locations ([map] deployment_locations, from locs/); the
regional mesonets and SFCOA are [[sources]] there.

live/      live-mode fetchers (CLAMPS soundings)
archive/   archive-mode fetchers (FOFS vehicle obs, CLAMPS, lidar, CopterSonde)
certs/     the broker CA, the read-only viewer login, and team/ for the team
           certificates the STORM team hands out (not in the repo)
locs/      NSSL's past deployment locations
"""
from __future__ import annotations

import os
from pathlib import Path

# importing settings first puts the .env keys in the environment
import settings

# the provider's name on buttons (the sounding drawer's source)
LABEL = "NSSL"

DIR = Path(__file__).resolve().parent
CERTS_DIR = DIR / "certs"

API_ROOT = os.environ.get("NSSL_API_ROOT", "https://api.nssl.noaa.gov").rstrip("/")

# vehicle IDs in NSSL's recordings from before its vehicles published under
# their FOFS folder names (lid1 -> dltruck, p1 -> probe1, ...)
from providers.nssl.archive.vehicle_aliases import LEGACY_IDS as VEHICLE_RENAMES  # noqa: E402

# NSSL's THREDDS server leaves the Sectigo intermediate out of its TLS
# chain, so the provider trusts it there itself: its public archive works
# without the profile. A storm.toml [[auth]] table for the server wins.
AUTH = [{"url": "https://data.nssl.noaa.gov",
         "ca_cert": str(CERTS_DIR / "sectigo_public_server_authentication_ca_dv_r36.pem")}]


def enabled() -> bool:
    """The provider is loaded: [providers.nssl] enabled = true, or no
    [providers.nssl] table (it's built in; settings.BUILTIN_PROVIDERS)."""
    return any(p.name == "nssl" for p in settings.SETTINGS.code_providers)


def api_enabled() -> bool:
    """The provider is loaded and the [[auth]] table for the NSSL API has its key."""
    from data import auth
    found = auth.for_url(API_ROOT)
    return enabled() and found is not None and bool(found.api_key)


# the archive hooks every provider may define (settings/code_providers.py)

def vehicle_obs_source(options=None):
    """The FOFS mobile mesonet's one-second files on THREDDS."""
    from providers.nssl.archive.vehicle_obs_archive_fetcher import FofsVehicleObsSource
    return FofsVehicleObsSource()


def live_sounding_fetcher(options, parent=None):
    """The lidar truck's soundings from the NSSL API; None without a key."""
    if not api_enabled():
        return None
    from providers.nssl.live.clamps_sounding_fetcher import ClampsSoundingFetcher
    return ClampsSoundingFetcher(parent)


def archive_drawer(options, archive_time=None, parent=None):
    """ARCHIVE's NSSL button and drawer: NSSL's vehicles, lidar, radiosondes,
    CopterSondes and TROPoe, all from THREDDS, for every team."""
    from providers.nssl.archive.drawer import NsslArchiveDrawer
    return NsslArchiveDrawer(options, archive_time, parent=parent)


def api_headers() -> dict[str, str]:
    """The NSSL API's key header, from its [[auth]] table (never send it to THREDDS)."""
    from data import auth
    return auth.request(API_ROOT)[1]

