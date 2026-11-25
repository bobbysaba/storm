"""Where STORM's data comes from, apart from THREDDS: the AWS open-data
buckets and every public web API, in one place.

THREDDS folders are in data/thredds_paths.py; the NSSL API and NSSL's
THREDDS folders are in providers/nssl/. When a source moves an endpoint,
change it here; the fetchers build their URLs from this file. Run
`python scripts/check_data_sources.py` to check that every endpoint here
(and every THREDDS folder) still answers.
"""
from __future__ import annotations

# ---- STORM's own documentation (opened from the Help menu) ------------------------------
DOCS_URL = "https://bobbysaba.com/docs/storm/"


# ---- AWS open data (archive mode) -----------------------------------------------------
NEXRAD_LEVEL2_BUCKET = "https://unidata-nexrad-level2.s3.amazonaws.com"   # <YYYY>/<MM>/<DD>/<SITE>/
GOES16_BUCKET = "noaa-goes16"
GOES19_BUCKET = "noaa-goes19"


def s3_bucket_url(bucket: str) -> str:
    return f"https://{bucket}.s3.amazonaws.com"


# ---- Iowa Environmental Mesonet (IEM) -----------------------------------------------------
IEM = "https://mesonet.agron.iastate.edu"
IEM_METAR_GEOJSON = f"{IEM}/geojson/metar.geojson"           # live ASOS stations
IEM_CURRENTS = f"{IEM}/api/1/currents.json"                  # live ASOS obs
IEM_RAOB = f"{IEM}/json/raob.py"                             # observed soundings
IEM_AFOS = f"{IEM}/cgi-bin/afos/retrieve.py"                  # product text (warnings, watches)
IEM_VTEC_EVENT = f"{IEM}/json/vtec_event.py"
IEM_GOES_EAST_WMS = f"{IEM}/cgi-bin/wms/goes_east.cgi"        # live satellite
IEM_STORM_BASED_WARNINGS = f"{IEM}/geojson/sbw.geojson"      # archive warnings
IEM_SPC_WATCHES = f"{IEM}/json/spcwatch.py"                  # archive watches
IEM_SPC_MD_GIS = f"{IEM}/cgi-bin/request/gis/spc_mcd.py"      # archive mesoscale discussions
IEM_ASOS_HISTORY = f"{IEM}/cgi-bin/request/asos.py"            # archive ASOS

# ---- SPC ------------------------------------------------------------------------------------
SPC = "https://www.spc.noaa.gov"
SPC_SOUNDINGS = f"{SPC}/exper/soundings"                      # <YYMMDDHH>_OBS/
SPC_OUTLOOK_ARCHIVE = f"{SPC}/products/outlook/archive"
SPC_EXTENDED_OUTLOOK_ARCHIVE = f"{SPC}/products/exper/day4-8/archive"


def spc_md_text(number: str) -> str:
    """A mesoscale discussion's text, e.g. spc_md_text("0612")."""
    return f"{SPC}/products/md/md{number}.txt"


# ---- NWS map services (live hazards) -------------------------------------------------------------
NWS_MAPSERVICES = "https://mapservices.weather.noaa.gov"
NWS_OUTLOOKS = f"{NWS_MAPSERVICES}/vector/rest/services/outlooks"
NWS_SPC_OUTLOOK_LAYERS = f"{NWS_OUTLOOKS}/SPC_wx_outlks/MapServer"
NWS_SPC_MD_LAYERS = f"{NWS_OUTLOOKS}/spc_mesoscale_discussion/MapServer"
NWS_WWA_LAYERS = f"{NWS_MAPSERVICES}/eventdriven/rest/services/WWA/watch_warn_adv/MapServer"

# ---- damage surveys -----------------------------------------------------------------------------
DAT_DAMAGE_LINES = ("https://services.dat.noaa.gov/arcgis/rest/services/"
                    "nws_damageassessmenttoolkit/DamageViewer/FeatureServer/1")
NCEI_STORM_EVENTS_CSV = "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles"

# ---- model soundings ----------------------------------------------------------------------------
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_HISTORICAL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

# ---- live satellite fallback ------------------------------------------------------------------
NOWCOAST_SATELLITE_WMS = "https://nowcoast.noaa.gov/geoserver/satellite/wms"

# ---- routing ---------------------------------------------------------------------------------------
OPENROUTESERVICE_DRIVING = "https://api.openrouteservice.org/v2/directions/driving-car"
NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"

# ---- base maps ------------------------------------------------------------------------------------
OPENFREEMAP_TILEJSON = "https://tiles.openfreemap.org/planet"          # online base map (ui/map/html.py)
NATIONALMAP_SERVICES = "https://basemap.nationalmap.gov/arcgis/rest/services"   # scripts/make_satellite_mbtiles.py


def all_endpoints() -> list[tuple[str, str]]:
    """(what, URL to probe) for the checker: a cheap request that answers
    while the endpoint exists (an API may answer 4xx without parameters or a
    key -- that still counts; 404/410 or no answer doesn't)."""
    out = [
        ("AWS NEXRAD Level 2", f"{NEXRAD_LEVEL2_BUCKET}/?list-type=2&max-keys=1"),
        ("AWS GOES-16", f"{s3_bucket_url(GOES16_BUCKET)}/?list-type=2&max-keys=1"),
        ("AWS GOES-19", f"{s3_bucket_url(GOES19_BUCKET)}/?list-type=2&max-keys=1"),
        ("IEM METAR stations", f"{IEM_METAR_GEOJSON}?only_new=1"),
        ("IEM ASOS currents", f"{IEM_CURRENTS}?station=OMA"),
        ("IEM soundings", f"{IEM_RAOB}?ts=202405171200&station=KOAX"),
        ("IEM AFOS text", f"{IEM_AFOS}?pil=SWOMCD&limit=1&fmt=text"),
        ("IEM VTEC event", IEM_VTEC_EVENT),
        ("IEM storm-based warnings", f"{IEM_STORM_BASED_WARNINGS}?sts=2024-05-17T20:00Z&ets=2024-05-17T20:05Z"),
        ("IEM SPC watches", f"{IEM_SPC_WATCHES}?ts=2024-05-17T20:00Z"),
        ("IEM SPC MD GIS", IEM_SPC_MD_GIS),
        ("IEM ASOS history", f"{IEM_ASOS_HISTORY}?station=OUN&data=tmpf&year1=2024&month1=5&day1=17"
                             "&year2=2024&month2=5&day2=17&tz=UTC&format=onlycomma"),
        ("IEM GOES-East WMS", f"{IEM_GOES_EAST_WMS}?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetCapabilities"),
        ("SPC soundings", f"{SPC_SOUNDINGS}/"),
        ("SPC outlook archive", f"{SPC_OUTLOOK_ARCHIVE}/2024/day1otlk_20240517_1300_cat.lyr.geojson"),
        ("SPC day 4-8 archive", f"{SPC_EXTENDED_OUTLOOK_ARCHIVE}/2024/day4prob_20240517.lyr.geojson"),
        ("NWS SPC outlook layers", f"{NWS_SPC_OUTLOOK_LAYERS}?f=json"),
        ("NWS SPC MD layers", f"{NWS_SPC_MD_LAYERS}?f=json"),
        ("NWS warnings/watches layers", f"{NWS_WWA_LAYERS}?f=json"),
        ("DAT damage surveys", f"{DAT_DAMAGE_LINES}?f=json"),
        ("NCEI storm events", f"{NCEI_STORM_EVENTS_CSV}/"),
        ("open-meteo forecast", f"{OPEN_METEO_FORECAST}?latitude=41&longitude=-96&hourly=temperature_2m&forecast_days=1"),
        ("open-meteo historical", f"{OPEN_METEO_HISTORICAL}?latitude=41&longitude=-96&hourly=temperature_2m"
                                  "&start_date=2024-05-17&end_date=2024-05-17"),
        ("nowCOAST satellite WMS", f"{NOWCOAST_SATELLITE_WMS}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities"),
        ("OpenRouteService", OPENROUTESERVICE_DRIVING),
        ("Nominatim", f"{NOMINATIM_SEARCH}?q=Norman%2C+OK&format=json&limit=1"),
        ("OpenFreeMap base map", OPENFREEMAP_TILEJSON),
        ("National Map services", f"{NATIONALMAP_SERVICES}?f=json"),
    ]
    return out
