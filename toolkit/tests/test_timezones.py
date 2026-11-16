"""Time zone boundaries in the map (e39maps_toolkit.timezones, the build's
timezone_area) and the lookup on them (mapdata.timezones)."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from e39maps_toolkit import timezones
from e39maps_toolkit.build import Region, build
from mapdata import schema
from mapdata import timezones as lookup

from test_toolkit import OSM_A

CENTER = (45.4215, -75.6972)   # Ottawa


def square(min_lon, min_lat, max_lon, max_lat):
    return [[min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat], [min_lon, max_lat], [min_lon, min_lat]]


def feature(zone, *rings):
    return {"type": "Feature", "properties": {"tzid": zone}, "geometry": {"type": "Polygon", "coordinates": list(rings)}}


# West and East share the border at lon -75.70 (just west of the centre);
# West has a hole that Test/Hole fills; Test/Far is ~500 km away; north of
# lat 46.0 nothing is mapped (a gap).
BOUNDARIES = {"type": "FeatureCollection", "features": [
    feature("Test/West", square(-77.5, 44.5, -75.70, 46.0), square(-76.30, 45.20, -76.10, 45.40)),
    feature("Test/Hole", square(-76.30, 45.20, -76.10, 45.40)),
    feature("Test/East", square(-75.70, 44.5, -74.0, 46.0)),
    feature("Test/Far", square(-70.0, 44.5, -69.0, 46.0)),
]}


class AreasTests(unittest.TestCase):

    def test_clipped_to_the_circle_with_its_margin(self):
        areas = timezones.areas(BOUNDARIES["features"], *CENTER, radius_km=100, margin_km=20)
        self.assertEqual([a[0] for a in areas], ["Test/East", "Test/Hole", "Test/West"])
        circle = timezones.circle(*CENTER, 120)
        min_lon, min_lat, max_lon, max_lat = circle.bounds
        for zone, (lo_lat, lo_lon, hi_lat, hi_lon), geometry in areas:
            self.assertIn(geometry["type"], ("Polygon", "MultiPolygon"))
            # Within the circle's box (+ the simplification), not the whole square.
            self.assertGreaterEqual(lo_lat, min_lat - 0.01)
            self.assertLessEqual(hi_lat, max_lat + 0.01)
            self.assertGreaterEqual(lo_lon, min_lon - 0.01)
            self.assertLessEqual(hi_lon, max_lon + 0.01)

    def test_shared_border_stays_shared(self):
        areas = {zone: geometry for zone, _, geometry in
                 timezones.areas(BOUNDARIES["features"], *CENTER, radius_km=100, tolerance=0.05)}
        # Points along the border: each in exactly one of West/East.
        for lat in (45.0, 45.3, 45.4215, 45.6, 45.9):
            for lon in (-75.7001, -75.70, -75.6999):
                inside = [z for z in ("Test/West", "Test/East") if lookup.contains(areas[z], lat, lon)]
                self.assertEqual(len(inside), 1, (lat, lon, inside))

    def test_nothing_in_range(self):
        self.assertEqual(timezones.areas(BOUNDARIES["features"], 0.0, 0.0, radius_km=100), [])


class FetchTests(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.source = self.dir / "boundaries.zip"
        self.source.write_bytes(b"pretend zip")
        self.cache = self.dir / "cache" / "extracts"

    def test_downloads_once_and_checks(self):
        digest = hashlib.sha256(b"pretend zip").hexdigest()
        path = timezones.fetch_boundaries(self.cache, log=lambda *_: None, url=self.source.as_uri(), sha256=digest)
        self.assertEqual(path, timezones.boundaries_path(self.cache))
        self.assertEqual(path.read_bytes(), b"pretend zip")
        self.source.unlink()     # cached: no second download
        self.assertEqual(timezones.fetch_boundaries(self.cache, log=lambda *_: None, url=self.source.as_uri(),
                                                    sha256=digest), path)

    def test_refuses_a_wrong_checksum(self):
        with self.assertRaises(ValueError):
            timezones.fetch_boundaries(self.cache, log=lambda *_: None, url=self.source.as_uri(), sha256="0" * 64)
        self.assertFalse(timezones.boundaries_path(self.cache).exists())
        self.assertFalse(list(timezones.boundaries_path(self.cache).parent.glob("*.part")))


class BuildWithBoundariesTests(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "a.osm").write_text(OSM_A)
        (self.dir / "boundaries.geojson").write_text(json.dumps(BOUNDARIES))
        self.region = Region("Test", *CENTER, 100, ["a"])
        self.out = self.dir / "map.sqlite"

    def build(self, boundaries=True):
        return build(self.region, ["fuel"], self.out, [self.dir / "a.osm"], log=lambda *_: None,
                     timezone_boundaries=self.dir / "boundaries.geojson" if boundaries else None)

    def test_areas_and_facts(self):
        meta = self.build()
        self.assertEqual(meta["timezone_areas"], "3")
        self.assertEqual(meta["timezone_boundaries"], timezones.BOUNDARIES_RELEASE)
        schema.check(self.out)
        conn = sqlite3.connect(self.out)
        zones = [r[0] for r in conn.execute("SELECT zone FROM timezone_area ORDER BY id")]
        self.assertEqual(zones, ["Test/East", "Test/Hole", "Test/West"])
        # The lookup on the built map.
        self.assertEqual(lookup.zone_at(conn, 45.42, -75.60), ("Test/East", lookup.SOURCE_BOUNDARIES))
        self.assertEqual(lookup.zone_at(conn, 45.42, -75.80), ("Test/West", lookup.SOURCE_BOUNDARIES))
        self.assertEqual(lookup.zone_at(conn, 45.30, -76.20), ("Test/Hole", lookup.SOURCE_BOUNDARIES))
        # The gap north of 46: the map's own zone (timezonefinder at the centre).
        self.assertEqual(lookup.zone_at(conn, 46.2, -75.7), ("America/Toronto", lookup.SOURCE_MAP_CENTRE))

    def test_without_boundaries(self):
        meta = self.build(boundaries=False)
        self.assertNotIn("timezone_areas", meta)
        conn = sqlite3.connect(self.out)
        self.assertEqual(conn.execute("SELECT count(*) FROM timezone_area").fetchone()[0], 0)
        self.assertEqual(lookup.zone_at(conn, 45.42, -75.60), ("America/Toronto", lookup.SOURCE_MAP_CENTRE))


if __name__ == "__main__":
    unittest.main()
