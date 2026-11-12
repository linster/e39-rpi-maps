"""Toolkit tests: toolkit/map-toolkit.sh's venv, or any Python with osmium:

    PYTHONPATH=toolkit:src python -m unittest discover -s toolkit/tests
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from e39maps_toolkit import push
from e39maps_toolkit.build import Region, build, distance_km
from mapdata import schema

# Ottawa-ish. Node 7 is ~250 km away (outside a 100 km circle); way 10 is a
# closed way (park), relation 20 a multipolygon (national park) made of
# way 12; node 6 is unnamed fuel (kept: brand), node 8 an unnamed museum
# (dropped), node 9 a building (not a POI).
OSM_A = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <node id="1" lat="45.40" lon="-75.70" version="1"><tag k="amenity" v="fuel"/><tag k="name" v="Esso Bank St"/><tag k="brand" v="Esso"/></node>
  <node id="2" lat="45.41" lon="-75.71" version="1"/>
  <node id="3" lat="45.41" lon="-75.69" version="1"/>
  <node id="4" lat="45.43" lon="-75.69" version="1"/>
  <node id="5" lat="45.43" lon="-75.71" version="1"/>
  <node id="6" lat="45.38" lon="-75.75" version="1"><tag k="amenity" v="fuel"/><tag k="brand" v="Shell"/></node>
  <node id="7" lat="43.65" lon="-79.38" version="1"><tag k="tourism" v="museum"/><tag k="name" v="Far Museum"/></node>
  <node id="8" lat="45.42" lon="-75.70" version="1"><tag k="tourism" v="museum"/></node>
  <node id="9" lat="45.42" lon="-75.70" version="1"><tag k="building" v="yes"/><tag k="name" v="Not a POI"/></node>
  <node id="11" lat="45.42" lon="-75.68" version="1"><tag k="amenity" v="fast_food"/><tag k="name" v="Chip Truck"/><tag k="cuisine" v="burger"/></node>
  <way id="10" version="1"><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="5"/><nd ref="2"/><tag k="leisure" v="park"/><tag k="name" v="Test Park"/></way>
  <way id="12" version="1"><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="2"/></way>
  <relation id="20" version="1"><member type="way" ref="12" role="outer"/><tag k="type" v="multipolygon"/><tag k="boundary" v="national_park"/><tag k="name" v="Rel Park"/></relation>
</osm>
"""
# A second extract overlapping the first (node 1 again) plus a junkyard.
OSM_B = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <node id="1" lat="45.40" lon="-75.70" version="1"><tag k="amenity" v="fuel"/><tag k="name" v="Esso Bank St"/></node>
  <node id="30" lat="45.30" lon="-75.90" version="1"><tag k="industrial" v="scrap_yard"/><tag k="name" v="Pick n Pull"/></node>
</osm>
"""


class BuildTests(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "a.osm").write_text(OSM_A)
        (self.dir / "b.osm").write_text(OSM_B)
        self.region = Region("Test", 45.4215, -75.6972, 100, ["a", "b"])
        self.out = self.dir / "out" / "map.sqlite"

    def run_build(self, categories=tuple(schema.CATEGORIES)):
        return build(self.region, categories, self.out, [self.dir / "a.osm", self.dir / "b.osm"],
                     log=lambda *_: None, toolkit_version="test")

    def rows(self):
        conn = sqlite3.connect(self.out)
        rows = {r[0]: r for r in conn.execute(
            "SELECT name, category, osm_type, osm_id, id, latitude, longitude, details FROM map_poi")}
        conn.close()
        return rows

    def test_pois_from_nodes_ways_and_relations(self):
        meta = self.run_build()
        rows = self.rows()
        self.assertEqual(set(rows), {"Esso Bank St", "Shell", "Chip Truck", "Test Park", "Rel Park", "Pick n Pull"})
        self.assertEqual(rows["Test Park"][1:4], ("park", "way", 10))
        self.assertEqual(rows["Rel Park"][1:4], ("park", "relation", 20))
        self.assertEqual(rows["Pick n Pull"][1], "junkyard")
        # Area-weighted centroid of the square 45.41-45.43 / -75.71--75.69.
        self.assertAlmostEqual(rows["Test Park"][5], 45.42, places=6)
        self.assertAlmostEqual(rows["Test Park"][6], -75.70, places=6)
        self.assertEqual(rows["Esso Bank St"][4], schema.poi_id("node", 1))
        self.assertEqual(json.loads(rows["Chip Truck"][7]), {"kind": "amenity=fast_food", "cuisine": "burger"})
        self.assertEqual(meta["poi_count"], "6")
        self.assertEqual(json.loads(meta["categories"])["park"], 2)

    def test_file_is_a_valid_map_database(self):
        self.run_build()
        meta = schema.check(self.out)
        self.assertEqual(meta["schema_version"], str(schema.SCHEMA_VERSION))
        self.assertEqual(meta["region"], "Test")
        self.assertEqual(meta["radius_km"], "100")
        self.assertEqual(meta["toolkit_version"], "test")
        self.assertTrue(meta["built_at"].endswith("Z"))
        self.assertEqual([e["id"] for e in json.loads(meta["extracts"])], ["a", "b"])
        conn = sqlite3.connect(self.out)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.assertFalse(list(self.out.parent.glob(".*building*")))

    def test_only_chosen_categories(self):
        self.run_build(["fuel"])
        self.assertEqual(set(self.rows()), {"Esso Bank St", "Shell"})

    def test_rebuild_replaces_the_file(self):
        self.run_build(["fuel"])
        self.run_build(["park"])
        self.assertEqual(set(self.rows()), {"Test Park", "Rel Park"})


class SchemaCheckTests(unittest.TestCase):

    def test_rejects_other_files(self):
        d = Path(tempfile.mkdtemp())
        (d / "text.sqlite").write_text("hello")
        with self.assertRaisesRegex(schema.InvalidMapDatabase, "isn't an SQLite database"):
            schema.check(d / "text.sqlite")
        sqlite3.connect(d / "other.sqlite").execute("CREATE TABLE t (x)")
        with self.assertRaisesRegex(schema.InvalidMapDatabase, "isn't an e39 map database"):
            schema.check(d / "other.sqlite")
        conn = sqlite3.connect(d / "empty.sqlite")
        schema.create(conn)
        conn.commit()
        conn.close()
        with self.assertRaisesRegex(schema.InvalidMapDatabase, "lacks schema_version, built_at, poi_count"):
            schema.check(d / "empty.sqlite")


class MiscTests(unittest.TestCase):

    def test_default_radius_reaches_london_and_quebec_city(self):
        from e39maps_toolkit.config import DEFAULT_REGION as r
        for lat, lon in ((42.9849, -81.2453), (46.8139, -71.2080)):
            self.assertLess(distance_km(r.center_lat, r.center_lon, lat, lon), r.radius_km)

    def test_yocto_data_dir(self):
        y = Path(tempfile.mkdtemp())
        self.assertEqual(push.yocto_data_dir(y), y / "e39-rpi-maps" / "output" / "db")
        conf = y / "build_dir" / "build" / "conf"
        conf.mkdir(parents=True)
        (conf / "local.conf").write_text('#E39_MAPS_DATA_DIR = "/nope"\nE39_MAPS_DATA_DIR ?= "/data/maps"\n')
        self.assertEqual(push.yocto_data_dir(y), Path("/data/maps"))

    def test_parse_target(self):
        self.assertEqual(push.parse_target("localhost:2222"), ("localhost", 2222))
        self.assertEqual(push.parse_target("10.0.0.5"), ("10.0.0.5", 22))


if __name__ == "__main__":
    unittest.main()
