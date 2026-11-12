"""regions.py with a small synthetic Geofabrik index (no network).

Country (0..4 x 0..2 degrees) containing two provinces West (0..2) and
East (2..4); a grouping "Corner" overlapping East (no parent relation, like
Geofabrik's "US Northeast"); a continent around everything; and a region
named like Geofabrik's path-named ones ("us/far-away").
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from e39maps_toolkit import regions

BASE = regions.BASE_URL


def square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def feature(id_, path, name, geometry, parent=None):
    props = {"id": id_, "name": name, "urls": {"pbf": f"{BASE}{path}-latest.osm.pbf"}}
    if parent:
        props["parent"] = parent
    return {"type": "Feature", "properties": props, "geometry": geometry}


INDEX = {"type": "FeatureCollection", "features": [
    feature("continent", "continent", "Continent", square(-10, -10, 10, 10)),
    feature("country", "continent/country", "Country", square(0, 0, 4, 2), "continent"),
    feature("west", "continent/country/west", "West", square(0, 0, 2, 2), "country"),
    feature("east", "continent/country/east", "East", square(2, 0, 4, 2), "country"),
    feature("corner", "continent/corner", "Corner", square(2.5, 0, 6, 2.5), "continent"),
    feature("us/far-away", "continent/us/far-away", "us/far-away", square(8, 8, 9, 9), "continent"),
]}


class RegionTests(unittest.TestCase):

    def setUp(self):
        self.index = regions.Index(INDEX)

    def paths(self, extracts):
        return [e.path.split("/")[-1] for e in extracts]

    def test_suggests_the_smallest_regions_covering_the_circle(self):
        # Centred on the West/East border, ~100 km: inside the country.
        touched, suggested = self.index.overlapping(1.0, 2.0, 100)
        self.assertEqual(self.paths(suggested), ["east", "west"])
        self.assertEqual(sorted(self.paths(touched)), ["continent", "corner", "country", "east", "west"])

    def test_small_circle_inside_one_region(self):
        touched, suggested = self.index.overlapping(1.0, 0.8, 30)
        self.assertEqual(self.paths(suggested), ["west"])
        self.assertNotIn("east", self.paths(touched))

    def test_grouping_used_only_where_nothing_smaller_covers(self):
        # Around (1, 4.5): East ends at 4, the Corner grouping goes to 6.
        _, suggested = self.index.overlapping(1.0, 4.5, 80)
        self.assertEqual(self.paths(suggested), ["corner", "east"])

    def test_labels_search_and_ancestors(self):
        far = self.index.extracts["continent/us/far-away"]
        self.assertEqual(far.name, "US / Far Away")
        east = self.index.extracts["continent/country/east"]
        self.assertEqual(east.label, "Country / East")
        self.assertEqual(self.paths(self.index.search("EAST")), ["east"])
        self.assertEqual(self.index.search("  "), [])

    def test_circle_and_contains(self):
        ring = regions.circle(1.0, 1.0, 50, 8)
        self.assertEqual(len(ring), 8)
        for lat, lon in ring:
            self.assertAlmostEqual(regions.destination(1.0, 1.0, 0, 0)[0], 1.0)
            self.assertTrue(self.index.extracts["continent/country/west"].contains(lon, lat))

    def test_load_uses_the_cache_when_offline(self):
        d = Path(tempfile.mkdtemp())
        cached = d / "geofabrik-index-v1.json"
        cached.write_text(json.dumps(INDEX))
        old = time.time() - 30 * 24 * 3600
        os.utime(cached, (old, old))
        logs = []
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            index = regions.Index.load(d, log=logs.append)
            self.assertIn("continent/country/east", index.extracts)
            self.assertIn("cached copy", logs[0])
            with self.assertRaises(OSError):
                regions.Index.load(Path(tempfile.mkdtemp()))


if __name__ == "__main__":
    unittest.main()
