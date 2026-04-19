
STATIONS: dict[str, dict] = {
    "TFX": {"name": "Great Falls, MT",       "lat": 47.46, "lon": -111.38, "elev": 1115},
    "GGW": {"name": "Glasgow, MT",           "lat": 48.21, "lon": -106.63, "elev":  638},
    "MLS": {"name": "Miles City, MT",        "lat": 46.43, "lon": -105.87, "elev":  806},
    "RIW": {"name": "Riverton, WY",          "lat": 43.06, "lon": -108.47, "elev": 1694},
    "PIH": {"name": "Pocatello, ID",         "lat": 42.91, "lon": -112.60, "elev": 1352},
    "SLC": {"name": "Salt Lake City, UT",    "lat": 40.77, "lon": -111.97, "elev": 1288},
    "GJT": {"name": "Grand Junction, CO",    "lat": 39.12, "lon": -108.53, "elev": 1475},
    "DNR": {"name": "Denver, CO",            "lat": 39.75, "lon": -104.87, "elev": 1625},
    "ABQ": {"name": "Albuquerque, NM",       "lat": 35.05, "lon": -106.73, "elev": 1618},
    "EPZ": {"name": "Santa Teresa, NM",      "lat": 31.87, "lon": -106.70, "elev": 1251},
    "TUS": {"name": "Tucson, AZ",            "lat": 32.12, "lon": -110.93, "elev":  779},
    "ISN": {"name": "Williston, ND",         "lat": 48.18, "lon": -103.64, "elev":  582},
    "BIS": {"name": "Bismarck, ND",          "lat": 46.77, "lon": -100.75, "elev":  503},
    "ABR": {"name": "Aberdeen, SD",          "lat": 45.45, "lon":  -98.41, "elev":  396},
    "UNR": {"name": "Rapid City, SD",        "lat": 44.07, "lon": -103.21, "elev":  966},
    "LBF": {"name": "North Platte, NE",      "lat": 41.13, "lon": -100.69, "elev":  851},
    "OAX": {"name": "Omaha (Valley), NE",    "lat": 41.32, "lon":  -96.37, "elev":  357},
    "DDC": {"name": "Dodge City, KS",        "lat": 37.77, "lon":  -99.97, "elev":  790},
    "TOP": {"name": "Topeka, KS",            "lat": 39.07, "lon":  -95.62, "elev":  268},
    "OUN": {"name": "Norman, OK",            "lat": 35.18, "lon":  -97.44, "elev":  357},
    "AMA": {"name": "Amarillo, TX",          "lat": 35.22, "lon": -101.71, "elev": 1093},
    "MAF": {"name": "Midland, TX",           "lat": 31.94, "lon": -102.19, "elev":  872},
    "FWD": {"name": "Fort Worth, TX",        "lat": 32.83, "lon":  -97.30, "elev":  210},
    "SHV": {"name": "Shreveport, LA",        "lat": 32.45, "lon":  -93.84, "elev":   79},
    "LCH": {"name": "Lake Charles, LA",      "lat": 30.12, "lon":  -93.22, "elev":   10},
    "LIX": {"name": "Slidell, LA",           "lat": 30.34, "lon":  -89.83, "elev":   10},
    "INL": {"name": "International Falls, MN","lat": 48.57, "lon":  -93.38, "elev":  361},
    "MPX": {"name": "Chanhassen, MN",        "lat": 44.85, "lon":  -93.57, "elev":  287},
    "DVN": {"name": "Davenport, IA",         "lat": 41.61, "lon":  -90.58, "elev":  229},
    "ILX": {"name": "Lincoln, IL",           "lat": 40.15, "lon":  -89.34, "elev":  178},
    "GRB": {"name": "Green Bay, WI",         "lat": 44.50, "lon":  -88.13, "elev":  214},
    "SGF": {"name": "Springfield, MO",       "lat": 37.23, "lon":  -93.40, "elev":  387},
    "LZK": {"name": "Little Rock, AR",       "lat": 34.84, "lon":  -92.26, "elev":   78},
    "JAN": {"name": "Jackson, MS",           "lat": 32.32, "lon":  -90.08, "elev":  100},
    "BMX": {"name": "Birmingham, AL",        "lat": 33.17, "lon":  -86.77, "elev":  178},
    "MOB": {"name": "Mobile, AL",            "lat": 30.68, "lon":  -88.24, "elev":   67},
    "FFC": {"name": "Peachtree City, GA",    "lat": 33.36, "lon":  -84.57, "elev":  244},
    "GRR": {"name": "Grand Rapids, MI",      "lat": 42.89, "lon":  -85.52, "elev":  245},
    "DTX": {"name": "Detroit, MI",           "lat": 42.70, "lon":  -83.47, "elev":  305},
    "ILN": {"name": "Wilmington, OH",        "lat": 39.42, "lon":  -83.82, "elev":  321},
    "LMK": {"name": "Louisville, KY",        "lat": 38.18, "lon":  -85.66, "elev":  149},
    "BNA": {"name": "Nashville, TN",         "lat": 36.25, "lon":  -86.57, "elev":  182},
}


def build_stations_geojson() -> str:
    """Return a GeoJSON FeatureCollection string of all stations."""
    import json
    features = []
    for sid, info in STATIONS.items():
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [info["lon"], info["lat"]],
            },
            "properties": {
                "id":       sid,
                "name":     info["name"],
                "elevation": info["elev"],
            },
        })
    return json.dumps({"type": "FeatureCollection", "features": features})
