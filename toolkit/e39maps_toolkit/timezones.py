"""Time zones in a map.

- The map's zone: the IANA zone at the centre of its circle ("America/Toronto"
  for Ottawa + 530 km), the fact `timezone`. The device uses it in automatic
  mode when it has no position (e39-rpi-yocto: e39rpi-timezone's auto.d hook
  50-e39maps-timezone from meta-e39rpi-maps).
- The zone boundaries of the map's region (the table timezone_area,
  mapdata.schema): a circle can span several zones (Edmonton + 1000 km:
  Pacific, Mountain, Saskatchewan's CST), so the device looks the zone up at
  the car's position (mapdata.timezones.zone_at, e39maps-timezone-follow).

Both from timezone-boundary-builder (OpenStreetMap-based): the centre with
timezonefinder (built from it), the boundaries from a pinned release of its
GeoJSON, downloaded once into the toolkit's cache and checked.
"""

import hashlib
import json
import math
import time
import urllib.request
import zipfile
from pathlib import Path

# The boundaries' release: the same tz database version as the image's tzdata
# (e39-rpi-yocto e39rpi-timezone-version.inc), so every zone named exists on
# the device. "timezones" keeps every zone (America/Toronto, Winnipeg,
# Regina, ...); the "-now" variant would merge Toronto into New_York.
BOUNDARIES_RELEASE = "2026e"
BOUNDARIES_ASSET = "timezones.geojson.zip"
BOUNDARIES_SHA256 = "a75361c760d6de2fc95c63e97e0b1086d5dfe6fdb2bc6d96efeb3a94673aaa01"
BOUNDARIES_URL = (f"https://github.com/evansiroky/timezone-boundary-builder/releases/download/"
                  f"{BOUNDARIES_RELEASE}/{BOUNDARIES_ASSET}")

# Kept beyond the circle (positions just outside it still resolve), and the
# simplification (degrees; ~100 m). Shared borders stay shared
# (shapely.coverage_simplify), so simplifying opens no gaps between zones.
MARGIN_KM = 20
SIMPLIFY_DEGREES = 0.001
# Stored coordinates: 6 decimals (~0.1 m).
COORDINATE_DECIMALS = 6

EARTH_RADIUS_KM = 6371.0088


def zone_at(lat, lon):
    """The IANA time zone name at (lat, lon), or None if unknown."""
    from timezonefinder import TimezoneFinder

    return TimezoneFinder().timezone_at(lat=lat, lng=lon)


def boundaries_path(cache_dir):
    """Where the boundaries are kept: beside the extract cache."""
    return Path(cache_dir).parent / "timezones" / f"{BOUNDARIES_RELEASE}-{BOUNDARIES_ASSET}"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(4 << 20):
            h.update(chunk)
    return h.hexdigest()


def fetch_boundaries(cache_dir, log=print, progress=None, cancelled=lambda: False, url=BOUNDARIES_URL,
                     sha256=BOUNDARIES_SHA256):
    """The local path of the pinned boundaries, downloaded if missing; raises
    ValueError if the download doesn't match the pinned checksum."""
    path = boundaries_path(cache_dir)
    if path.exists() and _sha256(path) == sha256:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    log(f"Time zone boundaries {BOUNDARIES_RELEASE}: downloading {url}")
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": "e39maps-toolkit"})
    with urllib.request.urlopen(request, timeout=60) as resp, open(part, "wb") as out:
        size, done = int(resp.headers.get("Content-Length", 0)), 0
        while chunk := resp.read(1 << 20):
            if cancelled():
                part.unlink(missing_ok=True)
                raise InterruptedError("cancelled")
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, size)
    actual = _sha256(part)
    if actual != sha256:
        part.unlink()
        raise ValueError(f"time zone boundaries: checksum {actual} isn't the pinned {sha256}")
    part.replace(path)
    log(f"Time zone boundaries: {path.stat().st_size / 1e6:.0f} MB in {time.monotonic() - started:.0f} s")
    return path


def read_features(path):
    """The GeoJSON features (each {"properties": {"tzid"}, "geometry"}) from
    the release's zip (one .json inside) or a plain GeoJSON file."""
    path = Path(path)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith(".json") or n.endswith(".geojson"))
            return json.load(z.open(name))["features"]
    return json.loads(path.read_text())["features"]


def circle(lat, lon, radius_km, points=180):
    """The circle around (lat, lon) as a shapely Polygon in (lon, lat),
    from great-circle destination points."""
    from shapely.geometry import Polygon

    p1, l1, d = math.radians(lat), math.radians(lon), radius_km / EARTH_RADIUS_KM
    ring = []
    for i in range(points):
        b = 2 * math.pi * i / points
        p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
        l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
        ring.append((math.degrees(l2), math.degrees(p2)))
    return Polygon(ring)


def _polygonal(geom):
    """Only the polygons of geom (an intersection can add lines and points
    where it touches the circle), as a Polygon/MultiPolygon, or None."""
    from shapely.geometry import MultiPolygon, Polygon

    if geom.is_empty:
        return None
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    parts = []
    for g in getattr(geom, "geoms", []):
        p = _polygonal(g)
        if p is not None:
            parts.extend(p.geoms if isinstance(p, MultiPolygon) else [p])
    return MultiPolygon(parts) if parts else None


def _rounded(coords):
    if isinstance(coords[0], (int, float)):
        return [round(c, COORDINATE_DECIMALS) for c in coords]
    return [_rounded(c) for c in coords]


def areas(features, center_lat, center_lon, radius_km, margin_km=MARGIN_KM, tolerance=SIMPLIFY_DEGREES):
    """The zones around the circle: [(zone, (min_lat, min_lon, max_lat,
    max_lon), GeoJSON geometry)], clipped to the circle + margin_km and
    simplified, one entry per zone, by zone name."""
    import shapely
    from shapely.geometry import mapping, shape

    area = circle(center_lat, center_lon, radius_km + margin_km)
    zones, clipped = [], []
    for feature in features:
        geom = shape(feature["geometry"])
        if not geom.intersects(area):
            continue
        part = _polygonal(geom.intersection(area))
        if part is not None:
            zones.append(feature["properties"]["tzid"])
            clipped.append(part)
    simplified = shapely.coverage_simplify(clipped, tolerance) if clipped else []
    result = []
    for zone, geom in sorted(zip(zones, simplified), key=lambda zg: zg[0]):
        geom = _polygonal(geom)
        if geom is None:
            continue
        min_lon, min_lat, max_lon, max_lat = geom.bounds
        g = mapping(geom)
        result.append((zone, (min_lat, min_lon, max_lat, max_lon),
                       {"type": g["type"], "coordinates": _rounded(json.loads(json.dumps(g["coordinates"])))}))
    return result
