"""Where STORM's data lives on THREDDS: every host and folder in one place.

When a THREDDS tree is reorganized (a folder renamed or moved), change it
here; fetchers build their URLs from this file. Run
`python scripts/check_data_sources.py` to see which of these locations still
answer, and, for any that don't, what their parent folder now holds.

Paths are relative to a THREDDS server's root, as they appear in its URLs:
    <host>/catalog/<path>/catalog.html      a folder's listing
    <host>/fileServer/<path>/<file>         a file
"""
from __future__ import annotations

# ---- servers -------------------------------------------------------------------
NSSL = "https://data.nssl.noaa.gov/thredds"      # NSSL field data (FOFS, CLAMPS)
UCAR = "https://thredds.ucar.edu/thredds"        # Unidata: live NEXRAD Level 3, VAD
THREDDS_NS = "http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0"   # catalog.xml namespace


def catalog_root(host: str = NSSL) -> str:
    """e.g. https://data.nssl.noaa.gov/thredds/catalog"""
    return f"{host}/catalog"


def file_root(host: str = NSSL) -> str:
    """e.g. https://data.nssl.noaa.gov/thredds/fileServer"""
    return f"{host}/fileServer"


def catalog_url(path: str, host: str = NSSL, page: str = "catalog.html") -> str:
    """A folder's listing: <host>/catalog/<path>/catalog.html (or catalog.xml)."""
    return f"{catalog_root(host)}/{path.strip('/')}/{page}"


def file_url(path: str, host: str = NSSL) -> str:
    """A file: <host>/fileServer/<path>."""
    return f"{file_root(host)}/{path.strip('/')}"


# ---- UCAR (live mode) ---------------------------------------------------------------
UCAR_NEXRAD_LEVEL3 = "nexrad/level3"                    # <product>/<site>/<YYYYMMDD>/

# ---- NSSL FOFS (archive mode) ---------------------------------------------------------
FOFS_MESONET = "FOFS/Mobile-Mesonet"                    # crawled whole (archive/fofs_index.py)
FOFS_MESONET_DATA = f"{FOFS_MESONET}/data"              # <vehicle>/raw/<YYYYMMDD>.txt
FOFS_ANNOTATIONS = "FOFS/STORM/annotations"             # recorded MQTT: storm.<topic>.<YYYYMMDD>

# ---- NSSL FRDD/CLAMPS: the two CLAMPS trailers and the lidar truck (archive mode) ----
CLAMPS = "FRDD/CLAMPS"
DLTRUCK1 = "dltruck/dltruck1"                           # the NSSL lidar truck
CLAMPS1 = "clamps/clamps1"
CLAMPS2 = "clamps/clamps2"


def clamps_ingested(platform_dir: str, datastream: str) -> str:
    """Instrument files as recorded: FRDD/CLAMPS/<platform>/ingested/<datastream>."""
    return f"{CLAMPS}/{platform_dir}/ingested/{datastream}"


def clamps_processed(platform_dir: str, datastream: str) -> str:
    """Retrievals (winds, TROPoe): FRDD/CLAMPS/<platform>/processed/<datastream>."""
    return f"{CLAMPS}/{platform_dir}/processed/{datastream}"


# Lidar wind profiles (processed): (STORM id, platform folder, datastream)
CLAMPS_WIND_STREAMS = (
    ("DLTRUCK1-DL1-VAD", DLTRUCK1, "dltruckdlvadDL1.c1"),
    ("DLTRUCK1-DL2-VAD", DLTRUCK1, "dltruckdlvadDL2.c1"),
    ("DLTRUCK1-DL1-CSMWINDS", DLTRUCK1, "dltruckdlcsmwindsDL1.c1"),
    ("DLTRUCK1-DL2-CSMWINDS", DLTRUCK1, "dltruckdlcsmwindsDL2.c1"),
    ("CLAMPS1-VAD", CLAMPS1, "clampsdlvadC1.c1"),
    ("CLAMPS2-VAD", CLAMPS2, "clampsdlvadC2.c1"),
)

# TROPoe retrievals (processed): (STORM id, platform folder, unit suffix). The
# datastream is clampstropoe10.<variant>.<unit>, variants tried in this order.
CLAMPS_TROPOE_PLATFORMS = (
    ("CLAMPS1", CLAMPS1, "C1"),
    ("CLAMPS2", CLAMPS2, "C2"),
)
CLAMPS_TROPOE_VARIANTS = (   # in scientific preference order
    "aeri_mwr.v2", "aeri_mwr.v1", "aeri.v2", "aeri.v1", "mwr.v2", "mwr.v1",
)


def clamps_tropoe_stream(variant: str, unit: str) -> str:
    return f"clampstropoe10.{variant}.{unit}"


# Trailer surface met (ingested), in preference order per trailer:
# (STORM id, platform folder, datastream, kind, wind direction usable)
CLAMPS_SURFACE_STREAMS = (
    ("CLAMPS2", CLAMPS2, "clampsmetC2.a1", "met_tower", False),
    ("CLAMPS1", CLAMPS1, "clampsmwrC1.a1", "mwr", True),
    ("CLAMPS2", CLAMPS2, "clampsmwrC2.a1", "mwr", True),
)

# Radiosondes launched from the lidar truck (ingested)
SONDE_PLATFORM = DLTRUCK1
SONDE_STREAM = "dltruckdlsonderawDL1.b1"

# Raw Doppler lidar rays (ingested): the PPI and CSM (continuous scan mode)
# files only, the scans STORM draws on the map. DL1 and DL2 are the same
# lidar on the truck, published under two stream names (DL2: 2022-05-13 to
# 06-16 only); both are read.
RAW_LIDAR_PRODUCTS = ("csm", "ppi")
RAW_LIDAR_PLATFORMS = (
    # (STORM id, instrument, platform folder, datastream prefix, unit, mobile)
    ("DLTRUCK1-DL1", "DLTRUCK1", DLTRUCK1, "dltruck", "DL1", True),
    ("DLTRUCK1-DL2", "DLTRUCK1", DLTRUCK1, "dltruck", "DL2", True),
    ("CLAMPS1", "CLAMPS1", CLAMPS1, "clamps", "C1", False),
    ("CLAMPS2", "CLAMPS2", CLAMPS2, "clamps", "C2", False),
)


def raw_lidar_stream(prefix: str, product: str, unit: str) -> str:
    return f"{prefix}dl{product}{unit}.b1"


# CopterSonde (OU), only under the PERiLS campaign folders. Checked 2026-09 (fork):
# FRDD/UAS, FRDD/CopterSonde, FRDD/CLAMPS/CopterSonde and RRDD/CopterSonde are 404.
PERILS = f"{CLAMPS}/campaigns/PERiLS"
PERILS_IOPS = (
    ("2022", "IOP1"), ("2022", "IOP2"), ("2022", "IOP4"),
    ("2023", "IOP1"), ("2023", "IOP2"), ("2023", "IOP3"), ("2023", "IOP4"), ("2023", "IOP5"),
)


def coptersonde_iop(year: str, iop: str) -> str:
    return f"{PERILS}/{year}/CopterSonde/v1/{iop}"


def all_folders() -> list[tuple[str, str, str, bool]]:
    """(what, host, path, required) for every folder STORM reads, for the
    checker (scripts/check_data_sources.py) and the tests. TROPoe variants
    and the CLAMPS2 combined retrieval aren't required: each trailer has only
    some, and the fetcher takes the first that exists."""
    out = [
        ("UCAR NEXRAD Level 3 (live)", UCAR, UCAR_NEXRAD_LEVEL3, True),
        ("FOFS mobile mesonet (archive)", NSSL, FOFS_MESONET_DATA, True),
        ("FOFS recorded MQTT (archive)", NSSL, FOFS_ANNOTATIONS, True),
    ]
    out += [(f"lidar winds {sid}", NSSL, clamps_processed(d, s), True) for sid, d, s in CLAMPS_WIND_STREAMS]
    out += [(f"TROPoe {sid} {v}", NSSL, clamps_processed(d, clamps_tropoe_stream(v, u)), False)
            for sid, d, u in CLAMPS_TROPOE_PLATFORMS for v in CLAMPS_TROPOE_VARIANTS]
    out += [(f"CLAMPS surface {sid} {kind}", NSSL, clamps_ingested(d, s), True)
            for sid, d, s, kind, _ in CLAMPS_SURFACE_STREAMS]
    out += [(f"raw lidar {sid} {p.upper()}", NSSL, clamps_ingested(d, raw_lidar_stream(prefix, p, unit)), True)
            for sid, _i, d, prefix, unit, _m in RAW_LIDAR_PLATFORMS for p in RAW_LIDAR_PRODUCTS]
    out.append(("lidar truck sondes", NSSL, clamps_ingested(SONDE_PLATFORM, SONDE_STREAM), True))
    out += [(f"CopterSonde PERiLS {y} {iop}", NSSL, coptersonde_iop(y, iop), True) for y, iop in PERILS_IOPS]
    return out
