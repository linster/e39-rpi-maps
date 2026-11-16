"""The time zone at a position, from the map database's timezone_area table
(mapdata.schema; written by the toolkit, e39maps_toolkit.timezones).

Plain Python, no Django and no native geometry library: the device's
e39maps-timezone-follow imports it (like e39maps-mapdb imports
mapdata.schema), and the backend's /timezone endpoint uses it.

Point in polygon by ray casting, with holes and MultiPolygons. Edges use the
half-open rule, so a point on a border shared by two zones (the toolkit keeps
shared borders identical) belongs to exactly one of them.
"""

import json
import sqlite3

# Where an answer came from.
SOURCE_BOUNDARIES = "boundaries"     # a zone's area contains the position
SOURCE_MAP_CENTRE = "map-centre"     # no area does (or the map has none): the map's zone


def _in_ring(ring, x, y):
    """Ray casting: whether (x, y) is inside the closed ring of [x, y]."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _in_polygon(rings, x, y):
    """rings: the outer ring, then holes (GeoJSON Polygon coordinates)."""
    if not rings or not _in_ring(rings[0], x, y):
        return False
    return not any(_in_ring(hole, x, y) for hole in rings[1:])


def contains(geometry, lat, lon):
    """Whether the GeoJSON Polygon/MultiPolygon geometry (dict or JSON text,
    (lon, lat) coordinates) contains (lat, lon)."""
    if isinstance(geometry, str):
        geometry = json.loads(geometry)
    kind, coords = geometry.get("type"), geometry.get("coordinates") or []
    if kind == "Polygon":
        return _in_polygon(coords, lon, lat)
    if kind == "MultiPolygon":
        return any(_in_polygon(polygon, lon, lat) for polygon in coords)
    return False


def has_areas(conn):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'timezone_area'").fetchone() is not None


def area_zone_at(conn, lat, lon):
    """The zone whose area contains (lat, lon), or None (outside them all, or
    a map without timezone_area). conn: an sqlite3 connection to the map
    database."""
    if not has_areas(conn):
        return None
    candidates = conn.execute(
        "SELECT zone, geometry FROM timezone_area "
        "WHERE min_lat <= ? AND max_lat >= ? AND min_lon <= ? AND max_lon >= ? ORDER BY id",
        (lat, lat, lon, lon))
    for zone, geometry in candidates:
        if contains(geometry, lat, lon):
            return zone
    return None


def map_zone(conn):
    """The map's zone (the fact timezone: the zone at its circle's centre), or None."""
    try:
        row = conn.execute("SELECT value FROM e39maps_meta WHERE key = 'timezone'").fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row and row[0] else None


def zone_at(conn, lat, lon):
    """(zone, source) at (lat, lon): from the zone areas (SOURCE_BOUNDARIES),
    else the map's zone (SOURCE_MAP_CENTRE), else (None, None)."""
    zone = area_zone_at(conn, lat, lon)
    if zone:
        return zone, SOURCE_BOUNDARIES
    zone = map_zone(conn)
    if zone:
        return zone, SOURCE_MAP_CENTRE
    return None, None
