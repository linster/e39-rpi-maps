"""Naming a map after the largest metropolitan area at the centre of its
circle: "Ottawa" for Ottawa + 530 km, "Edmonton" for Edmonton + 1000 km.
The name goes into the map's facts and its release tag
(map-ottawa-20261113T040254Z), so maps of different regions can be told
apart.

The rule: among OSM cities (place=city) near the centre (within a quarter of
the radius, at least 30 km and at most 150 km), the largest by population
(OSM's population tag); cities without one count as smaller than any with.
None near the centre: the nearest city or town in the circle.
"""

import math
import re
import unicodedata

NEAR_FRACTION = 0.25
NEAR_MIN_KM = 30
NEAR_MAX_KM = 150


def _km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def population(value):
    """OSM's population tag is free text: "1017449", "1,017,449", "~ 30 000"."""
    digits = re.sub(r"[^\d]", "", (value or "").split(";")[0])
    return int(digits) if digits else 0


def metro_label(places, lat, lon, radius_km):
    """places: [(name, place, population, lat, lon)]. The name, or None."""
    near = max(NEAR_MIN_KM, min(NEAR_MAX_KM, radius_km * NEAR_FRACTION))
    located = [(_km(lat, lon, plat, plon), name, place, pop) for name, place, pop, plat, plon in places if name]
    cities = [p for p in located if p[2] == "city" and p[0] <= near]
    if cities:
        # Largest population; among equals (e.g. untagged), the nearest.
        return max(cities, key=lambda p: (p[3], -p[0]))[1]
    inside = [p for p in located if p[0] <= radius_km]
    for kind in ("city", "town"):
        nearest = [p for p in inside if p[2] == kind]
        if nearest:
            return min(nearest)[1]
    return None


def slug(label):
    """"Québec" -> "quebec", "St. John's" -> "st-johns": for release tags."""
    ascii_ = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_.lower().replace("'", "")).strip("-") or "map"
