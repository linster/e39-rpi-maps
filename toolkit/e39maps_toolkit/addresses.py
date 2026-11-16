"""Geocoding data for the map (mapdata.schema: map_place, map_street,
map_address, map_postcode; looked up by mapdata.geocode).

One more pass over each extract, with node locations (pyosmium):
- addresses: nodes with addr:housenumber + addr:street, and buildings
  (areas, or unclosed ways) with them, at their centroid;
- streets: named roads (highway=*), at the middle node of each way;
- places: named place nodes (city ... neighbourhood), and municipalities;
- municipalities: administrative boundaries (areas).

Rows are staged in temporary tables in the map database (millions of
addresses: Ottawa + 530 km has ~3.8 M), then grouped with SQL:
- an address's locality is addr:city, else its municipality (the
  administrative boundary around it: admin_level 8, or a level-6 area with
  no level-8 municipalities in it, i.e. a single-tier city such as Ottawa
  rather than a county or a Quebec MRC), else the nearest city, town or
  village; official prefixes ("Township of") are dropped;
- streets are grouped by normal name, locality and a ~10 km cell (so two
  Main Streets in one amalgamated city stay apart); a road without
  addresses still makes a street (one can drive to it);
- postal codes are placed at the centre of their addresses.
OSM's addr:interpolation lines aren't stored: the lookup interpolates between
the known numbers of a street instead.
"""

import math
import time
from collections import defaultdict

import osmium
from osmium.filter import KeyFilter

from mapdata import addresses as norm

from .build import area_point

# Places a locality can be (map_place) and those an address without
# addr:city is assigned to (the nearest one).
PLACE_KINDS = ("city", "town", "village", "hamlet", "suburb", "quarter", "neighbourhood")
LOCALITY_KINDS = ("city", "town", "village")
# Roads that make streets (not footways, tracks, ...).
ROAD_KINDS = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
    "living_street", "service", "road",
}
CELL_DEGREES = 0.1
BATCH = 20000

STAGING = """
CREATE TEMP TABLE raw_address (street_norm TEXT, street_name TEXT, number_int INTEGER, number TEXT,
                               place_norm TEXT, place_name TEXT, lat REAL, lon REAL, postcode TEXT, cell TEXT);
CREATE TEMP TABLE raw_road (norm TEXT, name TEXT, place_norm TEXT, place_name TEXT, lat REAL, lon REAL, cell TEXT);
"""


class Places:
    """The nearest locality to a point, from a grid of place nodes."""

    def __init__(self, places, cell=0.5):
        self.cell = cell
        self.grid = defaultdict(list)
        for p in places:
            self.grid[(math.floor(p[3] / cell), math.floor(p[4] / cell))].append(p)

    def nearest(self, lat, lon, max_rings=4):
        ci, cj = math.floor(lat / self.cell), math.floor(lon / self.cell)
        best, best_d = None, None
        k = math.cos(math.radians(lat))
        for ring in range(max_rings + 1):
            for i in range(ci - ring, ci + ring + 1):
                for j in range(cj - ring, cj + ring + 1):
                    if max(abs(i - ci), abs(j - cj)) != ring:
                        continue
                    for p in self.grid.get((i, j), ()):
                        d = (p[3] - lat) ** 2 + ((p[4] - lon) * k) ** 2
                        if best_d is None or d < best_d:
                            best, best_d = p, d
            if best is not None and ring >= 1:
                return best
        return best


def cell_of(lat, lon):
    return f"{math.floor(lat / CELL_DEGREES)},{math.floor(lon / CELL_DEGREES)}"


def _area_geometry(area):
    """An osmium area as a shapely (Multi)Polygon in (lon, lat), or None."""
    from shapely.geometry import MultiPolygon, Polygon

    polygons = []
    for outer in area.outer_rings():
        shell = [(n.lon, n.lat) for n in outer if n.location.valid()]
        if len(shell) < 4:
            continue
        holes = [[(n.lon, n.lat) for n in inner if n.location.valid()] for inner in area.inner_rings(outer)]
        polygons.append(Polygon(shell, [h for h in holes if len(h) >= 4]))
    if not polygons:
        return None
    geom = MultiPolygon(polygons) if len(polygons) > 1 else polygons[0]
    return geom if geom.is_valid else geom.buffer(0)


def localities_from_boundaries(areas):
    """[(name, geometry)] of the municipalities among admin areas [(level,
    name, geometry)]: every level 8, and the level-6 areas with no level 8
    inside (single-tier cities and towns, not counties or MRCs)."""
    level8 = [(name, g) for level, name, g in areas if level == 8]
    inner_points = [g.representative_point() for _, g in level8]
    result = list(level8)
    for level, name, g in areas:
        if level == 6 and not any(g.contains(p) for p in inner_points):
            result.append((name, g))
    return result


def assign_municipalities(conn, table, munis):
    """Set place_norm/place_name of table's rows without one to the
    municipality around them (level 8 first: listed first in munis)."""
    import numpy as np
    import shapely

    if not munis:
        return
    rows = conn.execute(f"SELECT rowid, lat, lon FROM {table} WHERE place_norm IS NULL").fetchall()
    if not rows:
        return
    ids = np.array([r[0] for r in rows])
    points = shapely.points(np.array([r[2] for r in rows]), np.array([r[1] for r in rows]))
    tree = shapely.STRtree([g for _, g in munis])
    point_index, muni_index = tree.query(points, predicate="within")
    chosen = {}
    for p, m in zip(point_index.tolist(), muni_index.tolist()):
        if p not in chosen or m < chosen[p]:
            chosen[p] = m
    names = [(norm.normalize_place(n), n) for n, _ in munis]
    conn.executemany(f"UPDATE {table} SET place_norm = ?, place_name = ? WHERE rowid = ?",
                     [(*names[m], int(ids[p])) for p, m in chosen.items()])


def _way_middle(way):
    nodes = [n for n in way.nodes if n.location.valid()]
    if not nodes:
        return None
    loc = nodes[len(nodes) // 2].location
    return loc.lat, loc.lon


def extract(conn, region, extract_paths, log=print, cancelled=lambda: False):
    """Fill the geocoding tables of the map database conn from the extracts
    (one pass each). Returns the facts."""
    started = time.monotonic()
    conn.executescript(STAGING)
    from .timezones import circle as make_circle

    circle = make_circle(region.center_lat, region.center_lon, region.radius_km)
    municipalities = []
    places = {}
    addr_rows, road_rows = [], []

    def flush():
        conn.executemany("INSERT INTO raw_address VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", addr_rows)
        conn.executemany("INSERT INTO raw_road VALUES (?, ?, ?, ?, ?, ?, ?)", road_rows)
        addr_rows.clear()
        road_rows.clear()

    def add_address(tags, point):
        street = tags.get("addr:street")
        key = norm.house_number_key(tags.get("addr:housenumber"))
        if not street or key is None or not region.contains(*point):
            return
        street_norm = norm.normalize_street(street)
        if not street_norm:
            return
        city = norm.clean_place(tags.get("addr:city") or "")
        city_norm = norm.normalize_place(city) if city else None
        addr_rows.append((street_norm, street, key[0], key[1], city_norm or None, city if city_norm else None,
                          round(point[0], 6), round(point[1], 6), norm.normalize_postcode(tags.get("addr:postcode")),
                          cell_of(*point)))

    for path in extract_paths:
        log(f"Addresses: reading {path.name}...")
        processor = (osmium.FileProcessor(str(path))
                     .with_areas(KeyFilter("addr:housenumber", "boundary"))
                     .with_filter(KeyFilter("addr:housenumber", "highway", "place", "boundary")))
        for obj in processor:
            if cancelled():
                raise InterruptedError("cancelled")
            tags = obj.tags
            if obj.is_node():
                if not obj.location.valid():
                    continue
                point = (obj.location.lat, obj.location.lon)
                if "addr:housenumber" in tags:
                    add_address(tags, point)
                kind = tags.get("place")
                if kind in PLACE_KINDS and tags.get("name") and region.contains(*point):
                    pop = norm.house_number_key(tags.get("population") or "")
                    places[(obj.id)] = (norm.clean_place(tags["name"]), norm.normalize_place(tags["name"]), kind,
                                        point[0], point[1], pop[0] if pop else 0)
            elif obj.is_area():
                if tags.get("boundary") == "administrative" and tags.get("admin_level") in ("6", "8") \
                        and tags.get("name"):
                    geom = _area_geometry(obj)
                    if geom is not None and geom.intersects(circle):
                        municipalities.append((int(tags["admin_level"]), norm.clean_place(tags["name"]), geom))
                if "addr:housenumber" in tags:
                    point = area_point(obj)
                    if point:
                        add_address(tags, point)
            elif obj.is_way():
                if "addr:housenumber" in tags and not obj.is_closed():
                    point = _way_middle(obj)
                    if point:
                        add_address(tags, point)
                if tags.get("highway") in ROAD_KINDS and tags.get("name"):
                    point = _way_middle(obj)
                    road_norm = norm.normalize_street(tags["name"])
                    if point and road_norm and region.contains(*point):
                        road_rows.append((road_norm, tags["name"], None, None, round(point[0], 6), round(point[1], 6),
                                          cell_of(*point)))
            if len(addr_rows) + len(road_rows) >= BATCH:
                flush()
        flush()
        conn.commit()
        log(f"  {path.name}: {time.monotonic() - started:.0f} s so far")

    # Municipalities: they place what has no addr:city, and are places.
    munis = localities_from_boundaries(municipalities)
    seen = {p[1] for p in places.values()}
    for name, geom in munis:
        point = geom.representative_point()
        if norm.normalize_place(name) not in seen and region.contains(point.y, point.x):
            places[("municipality", name)] = (name, norm.normalize_place(name), "municipality", point.y, point.x, 0)
            seen.add(norm.normalize_place(name))
    for table in ("raw_address", "raw_road"):
        assign_municipalities(conn, table, munis)
    log(f"Addresses: {len(munis)} municipalities ({time.monotonic() - started:.0f} s)")

    # Places, and the nearest locality for what's still without one.
    conn.executemany("INSERT INTO map_place (name, norm, kind, latitude, longitude, population) "
                     "VALUES (?, ?, ?, ?, ?, ?)", sorted(places.values(), key=lambda p: (p[1], p[0])))
    localities = Places([p for p in places.values() if p[2] in LOCALITY_KINDS])
    for table in ("raw_address", "raw_road"):
        updates = []
        for rowid, lat, lon in conn.execute(f"SELECT rowid, lat, lon FROM {table} WHERE place_norm IS NULL"):
            p = localities.nearest(lat, lon)
            if p:
                updates.append((p[1], p[0], rowid))
        conn.executemany(f"UPDATE {table} SET place_norm = ?, place_name = ? WHERE rowid = ?", updates)
    conn.execute("DELETE FROM raw_address WHERE place_norm IS NULL")
    conn.execute("DELETE FROM raw_road WHERE place_norm IS NULL")
    log(f"Addresses: localities assigned ({time.monotonic() - started:.0f} s)")

    # Streets: one per (name, locality, cell); the point nearest the
    # members' centre; the most common spelling.
    conn.executescript("""
        CREATE TEMP TABLE member AS
            SELECT street_norm AS norm, street_name AS name, place_norm, place_name, lat, lon, 1 AS is_address,
                   cell FROM raw_address
            UNION ALL
            SELECT norm, name, place_norm, place_name, lat, lon, 0, cell FROM raw_road;
        CREATE INDEX temp.member_key ON member (norm, place_norm, cell);
    """)
    groups = conn.execute("""
        SELECT norm, place_norm, cell, avg(lat), avg(lon), sum(is_address)
        FROM member GROUP BY norm, place_norm, cell""").fetchall()
    streets = []
    for street_norm, place_norm, c, lat, lon, count in groups:
        members = conn.execute(
            "SELECT name, place_name, lat, lon FROM member WHERE norm = ? AND place_norm = ? AND cell = ?",
            (street_norm, place_norm, c)).fetchall()
        names = defaultdict(int)
        for m in members:
            names[m[0]] += 1
        k = math.cos(math.radians(lat))
        nearest = min(members, key=lambda m: (m[2] - lat) ** 2 + ((m[3] - lon) * k) ** 2)
        streets.append((max(names, key=lambda n: (names[n], n)), street_norm, nearest[1], place_norm,
                        nearest[2], nearest[3], count, c))
    streets.sort(key=lambda s: (s[1], s[3], s[7]))
    conn.execute("CREATE TEMP TABLE street_key (id INTEGER PRIMARY KEY, norm TEXT, place_norm TEXT, cell TEXT)")
    for i, s in enumerate(streets, 1):
        conn.execute("INSERT INTO map_street VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (i, *s[:7]))
        conn.execute("INSERT INTO street_key VALUES (?, ?, ?, ?)", (i, s[1], s[3], s[7]))
    conn.execute("CREATE INDEX temp.street_key_key ON street_key (norm, place_norm, cell)")
    conn.execute("INSERT INTO map_street_fts (map_street_fts) VALUES ('rebuild')")
    log(f"Addresses: {len(streets)} streets ({time.monotonic() - started:.0f} s)")

    conn.execute("""
        INSERT OR IGNORE INTO map_address
        SELECT k.id, r.number_int, r.number, CAST(round(r.lat * 1e6) AS INTEGER), CAST(round(r.lon * 1e6) AS INTEGER),
               r.postcode
        FROM raw_address r JOIN street_key k
          ON k.norm = r.street_norm AND k.place_norm = r.place_norm AND k.cell = r.cell
    """)
    conn.executescript("""
        INSERT INTO map_postcode
            SELECT postcode, avg(lat), avg(lon), count(*) FROM raw_address
            WHERE postcode IS NOT NULL GROUP BY postcode;
        INSERT OR IGNORE INTO map_postcode
            SELECT substr(postcode, 1, 3), avg(lat), avg(lon), count(*) FROM raw_address
            WHERE postcode GLOB '[A-Z][0-9][A-Z][0-9][A-Z][0-9]' GROUP BY substr(postcode, 1, 3);
        DROP TABLE raw_address; DROP TABLE raw_road; DROP TABLE member; DROP TABLE street_key;
    """)
    conn.commit()
    counts = {
        "geocode_addresses": conn.execute("SELECT count(*) FROM map_address").fetchone()[0],
        "geocode_streets": len(streets),
        "geocode_postcodes": conn.execute("SELECT count(*) FROM map_postcode").fetchone()[0],
        "geocode_places": len(places),
    }
    log(f"Addresses: {counts['geocode_addresses']} addresses on {counts['geocode_streets']} streets, "
        f"{counts['geocode_postcodes']} postal codes, {counts['geocode_places']} places "
        f"({time.monotonic() - started:.0f} s)")
    return {k: str(v) for k, v in counts.items()}
