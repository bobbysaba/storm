"""Check that NSSL's data sources still answer: its THREDDS folders
(paths.py), its API (endpoints.py) and the regional mesonets in
profiles/nssl.toml, with scripts/check_data_sources.py's checker, asked
with the profile's [[auth]] tables as STORM asks: the API with NSSL_API_KEY
(if set), both servers trusting the bundled Sectigo intermediate.

    python providers/nssl/check_sources.py           # everything
    python providers/nssl/check_sources.py clamps    # only checks whose description or address has "clamps"

data.nssl.noaa.gov throttles bursts: don't run it in a loop or alongside a
STORM session that is loading archive data. Exit status: 0 when everything
answers, 1 otherwise.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_data_sources as check  # noqa: E402
import settings  # noqa: E402
from providers.nssl import endpoints, paths  # noqa: E402

PROFILE = ROOT / "profiles" / "nssl.toml"


def main(argv: list[str]) -> int:
    profile = settings.load(PROFILE)
    check.auth = profile.auth
    mesonets = profile.sources
    return check.run(argv, paths.all_folders(), endpoints.all_endpoints() + check.source_endpoints(mesonets),
                     "providers/nssl/paths.py", "providers/nssl/endpoints.py or profiles/nssl.toml")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
