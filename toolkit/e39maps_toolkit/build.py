"""Build the map database from OpenStreetMap extracts.

Reads each extract once with pyosmium: tag filtering, node locations and
area assembly (closed ways, multipolygon relations) happen in C++, so only
matching objects reach Python. Nodes become POIs at their location, areas
at the centroid of their largest outer ring. POIs outside the region's
circle are dropped; objects in several extracts (provinces overlap a little
at their borders) are stored once.
"""

import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import osmium
from osmium.filter import TagFilter

from mapdata import schema

from . import categories as cats
from . import naming
from . import timezones

EARTH_RADIUS_KM = 6371.0088


def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@dataclass
class Region:
    name: str
    center_lat: float
    center_lon: float
    radius_km: float
    extracts: list = field(default_factory=list)

    def contains(self, lat, lon):
        return distance_km(self.center_lat, self.center_lon, lat, lon) <= self.radius_km


def ring_centroid(ring):
    """Area-weighted centroid of a closed ring of (lat, lon), planar (fine
    for placing a POI). Returns (abs area, lat, lon)."""
    a = cx = cy = 0.0
    for (y0, x0), (y1, x1) in zip(ring, ring[1:]):
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-15:      # degenerate: average of the points
        lat = sum(p[0] for p in ring) / len(ring)
        lon = sum(p[1] for p in ring) / len(ring)
        return 0.0, lat, lon
    return abs(a / 2), cy / (3 * a), cx / (3 * a)


def area_point(area):
    best = None
    for ring in area.outer_rings():
        points = [(n.lat, n.lon) for n in ring if n.location.valid()]
        if len(points) < 3:
            continue
        candidate = ring_centroid(points)
        if best is None or candidate[0] > best[0]:
            best = candidate
    return None if best is None else best[1:]


def extract_timestamp(path):
    header = osmium.io.Reader(str(path), osmium.osm.osm_entity_bits.NOTHING).header()
    return header.get("osmosis_replication_timestamp") or header.get("timestamp") or ""


def _place(src, dest):
    """Hard link src to dest (the toolkit's caches are usually on the same
    filesystem), else copy; atomically."""
    if dest.exists() and os.path.samefile(src, dest):
        # Already this file (a hard link from an earlier build): renaming a
        # second link onto it would be a no-op that leaves the temporary
        # name behind (POSIX rename of two links to one file).
        return
    tmp = dest.with_name(f".{dest.name}.placing")
    tmp.unlink(missing_ok=True)
    try:
        os.link(src, tmp)
    except OSError:
        import shutil
        shutil.copyfile(src, tmp)
    os.replace(tmp, dest)


def place_companions(out_path, routing, raster_files, raster_info=None, log=print):
    """Put the routing tiles and the raster tile files beside the database
    (schema.routing_name_for / tiles_name_for), remove ones from an earlier
    build that this one doesn't have, and return their facts for
    e39maps_meta."""
    facts = {}
    routing_dest = out_path.with_name(schema.routing_name_for(out_path.name))
    if routing is None:
        routing_dest.unlink(missing_ok=True)
    else:
        tiles, version = routing
        _place(tiles, routing_dest)
        log(f"Routing tiles: {routing_dest.name}; checksumming...")
        facts.update({
            "routing_file": routing_dest.name,
            "routing_size": str(routing_dest.stat().st_size),
            "routing_sha256": schema.sha256_file(routing_dest),
            "valhalla_version": version,
        })

    stem = out_path.name.rsplit(".", 1)[0]
    wanted = set()
    entries = []
    for i, src in enumerate(raster_files or [], 1):
        dest = out_path.with_name(schema.tiles_name_for(out_path.name, i))
        _place(src, dest)
        wanted.add(dest.name)
        log(f"Map tiles: {dest.name} ({dest.stat().st_size / 1e6:.0f} MB); checksumming...")
        entries.append({"name": dest.name, "size": dest.stat().st_size, "sha256": schema.sha256_file(dest)})
    for old in out_path.parent.glob(f"{stem}.tiles-*.mbtiles"):
        if old.name not in wanted:
            old.unlink()
    if entries:
        facts["tiles_files"] = json.dumps(entries)
        facts.update({k: str(v) for k, v in (raster_info or {}).items()})
    return facts


def write_timezone_areas(conn, region, boundaries, log=print):
    """The region's time zones into timezone_area, from the boundaries
    (timezones.fetch_boundaries' file, or GeoJSON). Returns the facts."""
    started = time.monotonic()
    log("Time zone boundaries: clipping to the region...")
    areas = timezones.areas(timezones.read_features(boundaries), region.center_lat, region.center_lon,
                            region.radius_km)
    rows = [(zone, *bbox, json.dumps(geometry, separators=(",", ":"))) for zone, bbox, geometry in areas]
    conn.executemany("INSERT INTO timezone_area (zone, min_lat, min_lon, max_lat, max_lon, geometry) "
                     "VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    size = sum(len(r[-1]) for r in rows)
    log(f"Time zones in the region: {', '.join(r[0] for r in rows) or 'none'} "
        f"({size / 1e3:.0f} kB of boundaries, {time.monotonic() - started:.0f} s)")
    return {"timezone_areas": str(len(rows)), "timezone_boundaries": timezones.BOUNDARIES_RELEASE}


def build(region, categories, out_path, extract_paths, log=print, progress=None,
          cancelled=lambda: False, toolkit_version="", routing=None, raster_files=None, raster_info=None,
          timezone_boundaries=None):
    """Write the map database to out_path (replaced atomically). routing:
    (tiles tar, Valhalla version) to pair with it, or None; raster_files:
    rendered MBTiles files (raster_info: tiles_zooms, tiles_count);
    timezone_boundaries: the time zone boundaries (timezones.fetch_boundaries),
    or None for a map without them. Returns the facts written to
    e39maps_meta."""
    categories = [c for c in schema.CATEGORIES if c in set(categories)]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(f".{out_path.name}.building")
    tmp.unlink(missing_ok=True)

    conn = sqlite3.connect(tmp)
    schema.create(conn)
    tag_filter = TagFilter(*cats.tag_pairs(categories))
    # Cities and towns too (nodes only), for naming the map (naming.py), in
    # the same pass.
    node_filter = TagFilter(*cats.tag_pairs(categories), ("place", "city"), ("place", "town"))
    places = []
    counts = {c: 0 for c in categories}
    sources = []
    started = time.monotonic()

    for path in extract_paths:
        path = Path(path)
        log(f"Reading {path.name} ({path.stat().st_size / 1e6:.0f} MB)...")
        sources.append({"file": path.name, "osm_timestamp": extract_timestamp(path)})
        found = kept = 0
        rows = []
        processor = osmium.FileProcessor(str(path)).with_areas(tag_filter).with_filter(node_filter)
        for obj in processor:
            if cancelled():
                conn.close()
                tmp.unlink(missing_ok=True)
                raise InterruptedError("cancelled")
            if obj.is_node():
                osm_type, osm_id = "node", obj.id
                if not obj.location.valid():
                    continue
                point = (obj.location.lat, obj.location.lon)
            elif obj.is_area():
                osm_type = "way" if obj.from_way() else "relation"
                osm_id = obj.orig_id()
                point = area_point(obj)
                if point is None:
                    continue
            else:
                continue        # ways/relations: their areas come separately
            tags = {t.k: t.v for t in obj.tags}
            if osm_type == "node" and tags.get("place") in ("city", "town"):
                places.append((tags.get("name") or tags.get("name:en") or "", tags["place"],
                               naming.population(tags.get("population")), point[0], point[1]))
            match = cats.classify(tags, categories)
            if match is None:
                continue
            found += 1
            category, kind = match
            name = cats.poi_name(tags, category, schema.CATEGORIES)
            if name is None or not region.contains(*point):
                continue
            details = {"kind": kind, **{k: tags[k] for k in cats.DETAIL_KEYS if k in tags}}
            rows.append((schema.poi_id(osm_type, osm_id), osm_type, osm_id, category, name,
                         round(point[0], 7), round(point[1], 7), json.dumps(details, ensure_ascii=False)))
            kept += 1
            if len(rows) >= 5000:
                conn.executemany("INSERT OR IGNORE INTO map_poi VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
                rows.clear()
                if progress:
                    progress(path.name, kept)
        conn.executemany("INSERT OR IGNORE INTO map_poi VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
        log(f"  {path.name}: {found} matching objects, {kept} named and inside the region "
            f"({time.monotonic() - started:.0f} s so far)")

    for category, count in conn.execute("SELECT category, count(*) FROM map_poi GROUP BY category"):
        counts[category] = count
    total = sum(counts.values())
    label = naming.metro_label(places, region.center_lat, region.center_lon, region.radius_km) or region.name
    log(f"Named after the largest city at the centre: {label}")
    zone = timezones.zone_at(region.center_lat, region.center_lon)
    log(f"Time zone at the centre: {zone or 'unknown'}")
    zone_facts = write_timezone_areas(conn, region, timezone_boundaries, log) if timezone_boundaries else {}
    meta = {
        "schema_version": str(schema.SCHEMA_VERSION),
        # Release tags and titles use these (map-<slug>-<built_at>).
        "region_label": label,
        "region_slug": naming.slug(label),
        # The device's automatic time zone (e39rpi-timezone) without a
        # position; with one, timezone_area (zone_facts).
        "timezone": zone or "",
        **zone_facts,
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "region": region.name,
        "center": f"{region.center_lat},{region.center_lon}",
        "radius_km": f"{region.radius_km:g}",
        "extracts": json.dumps([{"id": e, **s} for e, s in zip(region.extracts, sources)]
                               if len(region.extracts) == len(sources) else sources),
        "categories": json.dumps(counts),
        "poi_count": str(total),
        "toolkit_version": toolkit_version,
        "attribution": "Map data (c) OpenStreetMap contributors, ODbL 1.0. "
                       "https://www.openstreetmap.org/copyright",
        # The companion files go beside the database first, so the database
        # never names files that aren't there.
        **place_companions(out_path, routing, raster_files, raster_info, log),
    }
    conn.executemany("INSERT INTO e39maps_meta VALUES (?, ?)", meta.items())
    conn.commit()
    # Read-only on the device (opened immutable): no WAL, compact, with
    # statistics for the query planner.
    conn.execute("ANALYZE")
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute("VACUUM")
    conn.close()
    schema.check(tmp)
    os.replace(tmp, out_path)
    log(f"Wrote {out_path} ({out_path.stat().st_size / 1e6:.1f} MB): {total} POIs "
        f"in {time.monotonic() - started:.0f} s"
        + (f", with routing tiles {meta['routing_file']}" if "routing_file" in meta else ", no routing tiles")
        + (f", {len(json.loads(meta['tiles_files']))} map tile file(s)" if "tiles_files" in meta else ""))
    for category, count in counts.items():
        log(f"  {schema.CATEGORIES[category]}: {count}")
    return meta
