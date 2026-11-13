"""Offline raster tiles: which tiles to render, and rendering them into
MBTiles files with a local OSM tile server (openstreetmap-carto via
Mapnik, the overv/openstreetmap-tile-server container: render-tiles.sh).

What to render (TilePlan): the region's circle at zooms 6-13, plus zooms
14-16 around every city and town in it (OSM place=city/town nodes), plus
optional extra areas. Rendering everything at 14+ isn't realistic (the
530 km circle is 300,000 tiles at zoom 14 and 1.2 million at 15).

Output: MBTiles files (SQLite; tiles stored TMS, y from the south), a new
file once one reaches MAX_FILE_BYTES, because each must stay under GitHub's
2 GiB limit for release assets. Resumable: tiles already in the output are
skipped. Requests go out grouped by the renderer's 8x8 metatiles, so each
metatile is drawn once.
"""

import math
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

USER_AGENT = "e39maps-toolkit renderer"
MAX_FILE_BYTES = 1_900_000_000
METATILE = 8
EARTH_CIRCUMFERENCE_KM = 40075.016

# Average PNG size per zoom, measured on the first full render (Ottawa +
# 530 km: the circle at 6-13, towns at 14-16; 264,537 tiles, 2.28 GB): rural
# tiles at 11-13 are small, town tiles at 14-16 bigger. For the estimate.
EST_BYTES = {6: 13500, 7: 14500, 8: 17000, 9: 15000, 10: 12200, 11: 8300, 12: 6200,
             13: 5100, 14: 14200, 15: 11700, 16: 8400, 17: 7000, 18: 6000, 19: 5000}


@dataclass
class Area:
    """A circle with its own zoom range."""
    lat: float
    lon: float
    radius_km: float
    min_zoom: int
    max_zoom: int
    name: str = ""


@dataclass
class TilePlan:
    circle_zooms: tuple = (6, 13)
    towns: bool = True
    town_zooms: tuple = (14, 16)
    city_radius_km: float = 8.0
    town_radius_km: float = 3.0
    extra_areas: list = field(default_factory=list)      # [Area as dict]

    @classmethod
    def from_dict(cls, data):
        plan = cls()
        for key, value in (data or {}).items():
            if hasattr(plan, key):
                setattr(plan, key, tuple(value) if key.endswith("zooms") else value)
        return plan

    def to_dict(self):
        return asdict(self)


# --- tile maths -----------------------------------------------------------------

def tile_of(lat, lon, z):
    n = 2 ** z
    lat = max(min(lat, 85.0511), -85.0511)
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return min(x, n - 1), min(y, n - 1)


def tile_center(z, x, y):
    n = 2 ** z
    lon = (x + 0.5) / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / n))))
    return lat, lon


def _distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def tiles_in_circle(lat, lon, radius_km, z):
    """The tiles at zoom z that touch the circle (centre within the radius
    plus half a tile diagonal)."""
    tile_km = EARTH_CIRCUMFERENCE_KM * math.cos(math.radians(lat)) / 2 ** z
    reach = radius_km + tile_km * 0.75
    dlat = math.degrees(reach / 6371.0088)
    dlon = dlat / max(math.cos(math.radians(lat)), 0.01)
    x0, y0 = tile_of(lat + dlat, lon - dlon, z)
    x1, y1 = tile_of(lat - dlat, lon + dlon, z)
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            if _distance_km(lat, lon, *tile_center(z, x, y)) <= reach:
                yield z, x, y


# --- towns -------------------------------------------------------------------------

def find_towns(pbf_paths, lat, lon, radius_km):
    """[(name, place, lat, lon)] of OSM place=city/town nodes in the circle."""
    import osmium
    from osmium.filter import TagFilter

    found = {}
    for path in pbf_paths:
        for node in (osmium.FileProcessor(str(path), osmium.osm.NODE)
                     .with_filter(TagFilter(("place", "city"), ("place", "town")))):
            loc = node.location
            if not loc.valid() or _distance_km(lat, lon, loc.lat, loc.lon) > radius_km:
                continue
            found[node.id] = (node.tags.get("name", ""), node.tags["place"], loc.lat, loc.lon)
    return sorted(found.values())


# --- the plan --------------------------------------------------------------------------

def plan_tiles(plan, region, towns):
    """The set of (z, x, y) to render."""
    tiles = set()
    z0, z1 = plan.circle_zooms
    for z in range(z0, z1 + 1):
        tiles.update(tiles_in_circle(region.center_lat, region.center_lon, region.radius_km, z))
    areas = [Area(**a) if isinstance(a, dict) else a for a in plan.extra_areas]
    if plan.towns:
        for name, place, tlat, tlon in towns:
            r = plan.city_radius_km if place == "city" else plan.town_radius_km
            areas.append(Area(tlat, tlon, r, plan.town_zooms[0], plan.town_zooms[1], name))
    for a in areas:
        for z in range(a.min_zoom, a.max_zoom + 1):
            tiles.update(tiles_in_circle(a.lat, a.lon, a.radius_km, z))
    return tiles


def estimate(tiles):
    """{zoom: count}, estimated bytes."""
    counts = {}
    for z, _x, _y in tiles:
        counts[z] = counts.get(z, 0) + 1
    return dict(sorted(counts.items())), sum(n * EST_BYTES.get(z, 15000) for z, n in counts.items())


# --- MBTiles ------------------------------------------------------------------------

class MBTilesWriter:
    """Writes tiles into out_dir/<prefix>-NN.mbtiles, starting a new file once
    one passes max_bytes. Knows which tiles are there already (resume)."""

    @staticmethod
    def existing(out_dir, prefix):
        """(z, x, y) of the tiles already in out_dir/<prefix>-*.mbtiles."""
        done = set()
        for f in sorted(Path(out_dir).glob(f"{prefix}-*.mbtiles")):
            conn = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
            for z, x, row in conn.execute("SELECT zoom_level, tile_column, tile_row FROM tiles"):
                done.add((z, x, 2 ** z - 1 - row))
            conn.close()
        return done

    def __init__(self, out_dir, prefix, metadata, max_bytes=MAX_FILE_BYTES):
        self.out_dir, self.prefix, self.metadata, self.max_bytes = Path(out_dir), prefix, metadata, max_bytes
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.done = self.existing(self.out_dir, prefix)
        self.files = sorted(self.out_dir.glob(f"{prefix}-*.mbtiles"))
        self.conn = None
        self.bytes = 0
        self._open(self.files[-1] if self.files else None)

    def _open(self, path):
        if self.conn is not None:
            self.conn.commit()
            self.conn.close()
        if path is None:
            path = self.out_dir / f"{self.prefix}-{len(self.files) + 1:02d}.mbtiles"
            self.files.append(path)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER,
                                              tile_data BLOB);
            CREATE UNIQUE INDEX IF NOT EXISTS tile_index ON tiles (zoom_level, tile_column, tile_row);
        """)
        self.conn.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                              [(k, str(v)) for k, v in {**self.metadata, "part": path.stem}.items()])
        self.bytes = path.stat().st_size
        self._since_check = 0

    def _file_bytes(self):
        """The database file's real size (pages in use), including what
        SQLite adds per tile (index, page slack): counting only the PNGs let
        the first full render's file reach 2,031 MB for a 1,900 MB limit."""
        count = self.conn.execute("PRAGMA page_count").fetchone()[0]
        size = self.conn.execute("PRAGMA page_size").fetchone()[0]
        return count * size

    def add(self, z, x, y, png):
        # Measure the file every 5% of the limit (at most 5 MB); start a new
        # file in time.
        if self.bytes + len(png) > self.max_bytes or self._since_check > min(5_000_000, self.max_bytes // 20):
            self.bytes, self._since_check = self._file_bytes(), 0
            if self.bytes + len(png) > self.max_bytes:
                self._open(None)
        self.conn.execute("INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, ?)", (z, x, 2 ** z - 1 - y, png))
        self.bytes += len(png)
        self._since_check += len(png)
        self.done.add((z, x, y))

    def commit(self):
        self.conn.commit()

    def close(self):
        if self.conn is not None:
            self.conn.commit()
            self.conn.execute("VACUUM")
            self.conn.close()
            self.conn = None
        return self.files


def summarize(files):
    """Facts about rendered MBTiles files for the map database."""
    zooms, count = set(), 0
    for f in files:
        conn = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
        for z, n in conn.execute("SELECT zoom_level, count(*) FROM tiles GROUP BY zoom_level"):
            zooms.add(z)
            count += n
        conn.close()
    return {"tiles_zooms": f"{min(zooms)}-{max(zooms)}" if zooms else "", "tiles_count": count}


def rendered_files(work):
    return sorted(Path(work).glob("tiles-*.mbtiles"))


def _fetch(url, attempts=4):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = resp.read()
                if resp.headers.get("Content-Type", "").startswith("image/png"):
                    return data
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(2 ** attempt)
    raise IOError(f"no tile from {url}")


def render(tiles, server_url, writer, workers=12, log=print, progress=None, cancelled=lambda: False):
    """Fetch every tile from the local tile server (server_url with {z},
    {x}, {y}) into writer. Returns the number rendered now."""
    todo = sorted((t for t in tiles if t not in writer.done),
                  key=lambda t: (t[0], t[1] // METATILE, t[2] // METATILE, t[1], t[2]))
    total, done_now, failed = len(todo), 0, 0
    log(f"Rendering {total} tiles ({len(tiles) - total} already done)")
    started, lock = time.monotonic(), threading.Lock()
    batch = 2000
    for start in range(0, total, batch):
        chunk = todo[start:start + batch]
        with ThreadPoolExecutor(workers) as pool:
            futures = {pool.submit(_fetch, server_url.format(z=z, x=x, y=y)): (z, x, y) for z, x, y in chunk}
            for fut in as_completed(futures):
                z, x, y = futures[fut]
                try:
                    png = fut.result()
                except IOError as exc:
                    failed += 1
                    log(f"  failed: {exc}")
                    continue
                with lock:
                    writer.add(z, x, y, png)
                    done_now += 1
        writer.commit()
        if progress:
            rate = done_now / max(time.monotonic() - started, 1)
            progress(done_now, total, rate, chunk[-1][0])
        if cancelled():
            raise InterruptedError("cancelled")
    if failed:
        log(f"{failed} tiles failed (run again to retry them)")
    return done_now
