"""Naming maps after the largest city at the centre of their circle, and
what the name does to releases (tags, titles, pinning)."""

import os
import tempfile
import unittest
from pathlib import Path

from e39maps_toolkit.build import Region, build
from e39maps_toolkit.naming import metro_label, population, slug
from mapdata import release, schema

from test_release import FakeGitHub

# (name, place, population, lat, lon)
OTTAWA = ("Ottawa", "city", 1017449, 45.4215, -75.6972)
GATINEAU = ("Gatineau", "city", 291041, 45.4765, -75.7013)
MONTREAL = ("Montréal", "city", 1762949, 45.5017, -73.5673)
KANATA = ("Kanata", "town", 0, 45.3088, -75.8987)

OSM_PLACES = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <node id="1" lat="45.40" lon="-75.70" version="1"><tag k="amenity" v="fuel"/><tag k="name" v="Esso"/></node>
  <node id="2" lat="45.4215" lon="-75.6972" version="1"><tag k="place" v="city"/><tag k="name" v="Ottawa"/><tag k="population" v="1,017,449"/></node>
  <node id="3" lat="45.4765" lon="-75.7013" version="1"><tag k="place" v="city"/><tag k="name" v="Gatineau"/><tag k="population" v="291041"/></node>
  <node id="4" lat="45.3088" lon="-75.8987" version="1"><tag k="place" v="town"/><tag k="name" v="Kanata"/></node>
</osm>
"""


class NamingTests(unittest.TestCase):

    def test_the_largest_city_near_the_centre(self):
        places = [GATINEAU, OTTAWA, MONTREAL, KANATA]
        # Centred between Ottawa and Gatineau: Ottawa is bigger.
        self.assertEqual(metro_label(places, 45.45, -75.70, 530), "Ottawa")
        # Montréal is bigger still, but 165 km from Ottawa: outside the
        # centre zone (a quarter of 530 km, at most 150 km).
        self.assertEqual(metro_label(places, 45.4215, -75.6972, 530), "Ottawa")
        # A huge circle's zone reaches it... capped at 150 km, so still not.
        self.assertEqual(metro_label(places, 45.4215, -75.6972, 2000), "Ottawa")

    def test_the_zone_is_at_least_30_km(self):
        # Radius 40 km: a quarter is 10 km, but Ottawa (6 km from Gatineau's
        # centre) and Gatineau both count; Ottawa is bigger.
        self.assertEqual(metro_label([GATINEAU, OTTAWA], 45.4765, -75.7013, 40), "Ottawa")

    def test_fallbacks(self):
        # Cities without population: the nearest.
        untagged = [("Far City", "city", 0, 45.60, -75.70), ("Near City", "city", 0, 45.43, -75.70)]
        self.assertEqual(metro_label(untagged, 45.42, -75.70, 530), "Near City")
        # No city near the centre: the nearest city in the circle, else town.
        self.assertEqual(metro_label([MONTREAL, KANATA], 45.4215, -75.6972, 530), "Montréal")
        self.assertEqual(metro_label([KANATA], 45.4215, -75.6972, 530), "Kanata")
        self.assertIsNone(metro_label([KANATA], 50.0, -100.0, 100))
        self.assertIsNone(metro_label([], 45.0, -75.0, 100))

    def test_population_and_slug(self):
        self.assertEqual(population("1,017,449"), 1017449)
        self.assertEqual(population("~ 30 000"), 30000)
        self.assertEqual(population("12000;13000"), 12000)
        self.assertEqual(population(None), 0)
        self.assertEqual(slug("Québec"), "quebec")
        self.assertEqual(slug("St. John's"), "st-johns")
        self.assertEqual(slug("Grand Sudbury / Greater Sudbury"), "grand-sudbury-greater-sudbury")
        self.assertEqual(slug("北京"), "map")


class NamedMapTests(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "a.osm").write_text(OSM_PLACES)
        self.repo = self.dir / "repo"
        self.repo.mkdir()

    def build(self, name, lat, lon, radius, sub):
        db = self.dir / sub / "e39maps-osm.sqlite"
        tiles = self.dir / f"{sub}.tar"
        tiles.write_bytes(os.urandom(1000))
        build(Region(name, lat, lon, radius, ["a"]), ["fuel"], db, [self.dir / "a.osm"],
              log=lambda *_: None, routing=(tiles, "3.9.1"))
        return db, schema.check(db)

    def test_build_names_the_map_and_places_are_not_pois(self):
        _db, meta = self.build("My region", 45.45, -75.70, 530, "o")
        self.assertEqual(meta["region_label"], "Ottawa")
        self.assertEqual(meta["region_slug"], "ottawa")
        self.assertEqual(meta["timezone"], "America/Toronto")
        self.assertEqual(meta["region"], "My region")
        self.assertEqual(meta["poi_count"], "1")
        self.assertRegex(release.tag_for(meta), r"^map-ottawa-\d{8}T\d{6}Z$")
        self.assertRegex(release.title_for(meta), r"^Map: Ottawa \(530 km\), \d{4}-\d\d-\d\d$")

    def test_without_cities_the_region_name(self):
        _db, meta = self.build("Nowhere", 60.0, -120.0, 100, "n")
        self.assertEqual(meta["region_label"], "Nowhere")

    def test_zones(self):
        from e39maps_toolkit.timezones import zone_at
        self.assertEqual(zone_at(53.5461, -113.4938), "America/Edmonton")
        self.assertEqual(zone_at(50.4452, -104.6189), "America/Regina")

    def test_old_maps_keep_their_tags(self):
        self.assertEqual(release.tag_for({"built_at": "2026-11-13T04:02:54Z"}), "map-20261113T040254Z")

    def test_publishing_another_region_keeps_the_pin(self):
        fake = FakeGitHub()
        self.addCleanup(fake.server.shutdown)

        def publish(db, **kw):
            return release.publish(db, self.repo, gh=fake.client(), log=lambda *_: None, target="abc123", **kw)

        ottawa, ottawa_meta = self.build("Ottawa", 45.45, -75.70, 530, "o")
        # Other regions: no places in their circles, named after the region.
        other, other_meta = self.build("Nowhere", 60.0, -120.0, 100, "k")
        self.assertEqual(other_meta["region_slug"], "nowhere")

        self.assertTrue(release.should_pin(ottawa_meta, self.repo))  # nothing pinned yet
        (self.repo / "map-release.json").write_text('{"tag": "map-20261113T040254Z"}')
        self.assertTrue(release.should_pin(ottawa_meta, self.repo))  # pinned before names
        first = publish(ottawa)
        self.assertEqual(release.read_manifest(self.repo)["tag"], first["tag"])
        self.assertEqual(first["region_label"], "Ottawa")
        self.assertEqual(first["timezone"], "America/Toronto")
        self.assertIn("- Region: Ottawa + 530 km", fake.releases[first["tag"]]["body"])
        self.assertIn("- Time zone: America/Toronto", fake.releases[first["tag"]]["body"])

        self.assertFalse(release.should_pin(other_meta, self.repo))
        other = publish(other)
        self.assertIn(other["tag"], fake.releases)
        self.assertEqual(release.read_manifest(self.repo)["tag"], first["tag"])

        # A newer Ottawa map would re-pin; another region does when asked.
        self.assertTrue(release.should_pin({**ottawa_meta, "built_at": "2027-01-01T00:00:00Z"}, self.repo))
        far, _meta = self.build("Far away", 50.0, -100.0, 100, "f")
        pinned = publish(far, pin=True)
        self.assertEqual(release.read_manifest(self.repo)["tag"], pinned["tag"])
        self.assertTrue(pinned["tag"].startswith("map-far-away-"))

        # Pinning an existing release back: the same manifest as published.
        release.pin_release(first["tag"], self.repo, gh=fake.client(), log=lambda *_: None)
        self.assertEqual(release.read_manifest(self.repo), first)


if __name__ == "__main__":
    unittest.main()
