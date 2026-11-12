"""Geofabrik's extracts as regions on the map: which ones a circle overlaps,
and which of those to download.

Geofabrik publishes an index of its extracts with their outlines
(index-v1.json, ~4 MB), cached next to the extracts and refreshed weekly.
An extract is named by its download path, e.g.
"north-america/canada/ontario" (as in Region.extracts).

The index nests regions (continent > country > province/state) and also has
overlapping groupings (US Northeast, US Midwest, ...) and US states hanging
straight off North America, so the hierarchy alone can't say which extracts
cover a circle best. Geometry can: sample points across the circle and give
each point to the *smallest* extract containing it. That picks Ontario over
Canada over North America, and New York over US Northeast, wherever they
both cover the point.
"""

import json
import math
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

INDEX_URL = "https://download.geofabrik.de/index-v1.json"
BASE_URL = "https://download.geofabrik.de/"
USER_AGENT = "e39maps-toolkit (https://github.com/linster/e39-rpi-maps)"
REFRESH_SECONDS = 7 * 24 * 3600
EARTH_RADIUS_KM = 6371.0088


@dataclass
class Extract:
    path: str                    # north-america/canada/ontario
    name: str                    # Ontario
    parent: str | None           # Geofabrik id of the parent
    polygons: list               # [(outer ring, [holes])], rings of (lon, lat)
    bbox: tuple = (0, 0, 0, 0)   # min lon, min lat, max lon, max lat
    area: float = 0.0            # rough, deg^2 * cos(lat): only for comparing
    size: int | None = None      # bytes of the .osm.pbf, when known
    ancestors: list = field(default_factory=list)   # names, outermost first

    @property
    def label(self):
        return " / ".join(self.ancestors[1:] + [self.name]) if self.ancestors else self.name

    def contains(self, lon, lat):
        x0, y0, x1, y1 = self.bbox
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            return False
        for outer, holes in self.polygons:
            if _in_ring(lon, lat, outer) and not any(_in_ring(lon, lat, h) for h in holes):
                return True
        return False

    def outline(self, max_points=300):
        """Outer rings as (lat, lon) lists for drawing, thinned out."""
        rings = []
        for outer, _ in self.polygons:
            step = max(1, len(outer) // max_points)
            rings.append([(lat, lon) for lon, lat in outer[::step]])
        return rings


def _in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _area(ring):
    a = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        a += x0 * y1 - x1 * y0
    lat = sum(p[1] for p in ring) / len(ring)
    return abs(a) / 2 * math.cos(math.radians(lat))


def destination(lat, lon, bearing_deg, distance_km):
    """The point distance_km from (lat, lon) along bearing_deg (great circle)."""
    p1, l1, b = math.radians(lat), math.radians(lon), math.radians(bearing_deg)
    d = distance_km / EARTH_RADIUS_KM
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def circle(lat, lon, radius_km, points=72):
    return [destination(lat, lon, 360 * i / points, radius_km) for i in range(points)]


def sample_points(lat, lon, radius_km, per_side=24):
    """A grid of (lat, lon) points inside the circle, plus its centre."""
    pts = [(lat, lon)]
    for i in range(per_side):
        for j in range(per_side):
            dx = (i + 0.5) / per_side * 2 - 1
            dy = (j + 0.5) / per_side * 2 - 1
            r = math.hypot(dx, dy)
            if r <= 1:
                pts.append(destination(lat, lon, math.degrees(math.atan2(dx, dy)), r * radius_km))
    return pts


def _readable(name):
    """Some of Geofabrik's names are paths ("us/new-york"): "US / New York"."""
    if "/" not in name:
        return name
    *prefix, last = name.split("/")
    words = lambda slug: " ".join(w.upper() if len(w) <= 2 else w.capitalize() for w in slug.split("-"))
    return " / ".join([words(p) for p in prefix] + [words(last)])


class Index:

    def __init__(self, data):
        features = data["features"]
        names = {f["properties"]["id"]: f["properties"] for f in features}
        self.extracts = {}
        for f in features:
            props, geom = f["properties"], f.get("geometry")
            url = props.get("urls", {}).get("pbf", "")
            if not geom or not url.startswith(BASE_URL) or not url.endswith("-latest.osm.pbf"):
                continue
            path = url[len(BASE_URL):-len("-latest.osm.pbf")]
            polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
            polygons = [([tuple(p[:2]) for p in poly[0]], [[tuple(p[:2]) for p in h] for h in poly[1:]])
                        for poly in polys]
            xs = [p[0] for outer, _ in polygons for p in outer]
            ys = [p[1] for outer, _ in polygons for p in outer]
            ancestors, parent = [], props.get("parent")
            while parent in names:
                ancestors.insert(0, names[parent]["name"])
                parent = names[parent].get("parent")
            self.extracts[path] = Extract(
                path=path, name=_readable(props["name"]), parent=props.get("parent"), polygons=polygons,
                bbox=(min(xs), min(ys), max(xs), max(ys)),
                area=sum(_area(outer) for outer, _ in polygons), ancestors=ancestors)

    @classmethod
    def load(cls, cache_dir, refresh=False, log=print):
        """The index, from the cache (downloaded when missing or a week old;
        the cached copy if Geofabrik can't be reached)."""
        path = Path(cache_dir) / "geofabrik-index-v1.json"
        fresh = path.exists() and time.time() - path.stat().st_mtime < REFRESH_SECONDS
        if refresh or not fresh:
            try:
                req = urllib.request.Request(INDEX_URL, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = resp.read()
                json.loads(data)
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".part")
                tmp.write_bytes(data)
                tmp.replace(path)
                log("Geofabrik's extract index: downloaded")
            except (OSError, ValueError) as exc:
                if not path.exists():
                    raise
                log(f"Geofabrik's extract index: using the cached copy ({exc})")
        return cls(json.loads(path.read_text()))

    def overlapping(self, lat, lon, radius_km):
        """(extracts overlapping the circle, the suggested ones): suggested =
        for sample points across the circle, the smallest extract containing
        each. Both sorted by name."""
        lats = [p[0] for p in circle(lat, lon, radius_km, 36)]
        lons = [p[1] for p in circle(lat, lon, radius_km, 36)]
        box = (min(lons), min(lats), max(lons), max(lats))
        near = [e for e in self.extracts.values()
                if e.bbox[0] <= box[2] and e.bbox[2] >= box[0] and e.bbox[1] <= box[3] and e.bbox[3] >= box[1]]
        touched, wins = set(), {}
        edge = circle(lat, lon, radius_km, 72)
        points = sample_points(lat, lon, radius_km) + edge
        for plat, plon in points:
            containing = [e for e in near if e.contains(plon, plat)]
            touched.update(e.path for e in containing)
            if containing:
                best = min(containing, key=lambda e: e.area).path
                wins[best] = wins.get(best, 0) + 1
        # A parent next to its own suggested sub-regions only won slivers
        # between their outlines (e.g. England around its counties): drop it.
        suggested = {p for p, n in wins.items()
                     if not (n <= 0.02 * len(points) and any(q.startswith(p + "/") for q in wins))}
        by_name = lambda paths: sorted((self.extracts[p] for p in paths), key=lambda e: e.label)
        return by_name(touched), by_name(suggested)

    def search(self, text, limit=40):
        text = text.strip().lower()
        if not text:
            return []
        found = [e for e in self.extracts.values() if text in e.name.lower() or text in e.path]
        return sorted(found, key=lambda e: (len(e.path), e.label))[:limit]


def download_size(path):
    """Bytes of an extract's .osm.pbf (HTTP HEAD), or None."""
    req = urllib.request.Request(f"{BASE_URL}{path}-latest.osm.pbf", method="HEAD",
                                 headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return int(resp.headers.get("Content-Length", 0)) or None
    except (OSError, ValueError):
        return None


def geocode(query):
    """(lat, lon, display name) of a place, via OpenStreetMap's Nominatim
    (one request per search, as its usage policy asks)."""
    import urllib.parse

    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": query, "format": "jsonv2", "limit": 1})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        hits = json.loads(resp.read())
    if not hits:
        return None
    return float(hits[0]["lat"]), float(hits[0]["lon"]), hits[0].get("display_name", query)
