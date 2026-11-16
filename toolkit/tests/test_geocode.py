"""Geocoding: the build's address tables (e39maps_toolkit.addresses) and
lookups on them (mapdata.geocode)."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from e39maps_toolkit.build import Region, build
from mapdata import geocode as gc

# Ottawa (city) with Bank Street (two road segments; even 100-110 and odd
# 101-111 addressed, with postal codes; 200 is a building), Elgin Street
# (a road, no addresses) and Boulevard Saint-Laurent; Smiths Falls (town,
# ~70 km away) with its own Main Street whose addresses have no addr:city
# (assigned to the nearest town); Ottawa has a Main Street too; Kanata is a
# suburb.
NODES = []
WAYS = []


def node(nid, lat, lon, **tags):
    tag_xml = "".join(f'<tag k="{k.replace("__", ":")}" v="{v}"/>' for k, v in tags.items())
    NODES.append(f'<node id="{nid}" lat="{lat}" lon="{lon}" version="1">{tag_xml}</node>')


def way(wid, refs, **tags):
    nds = "".join(f'<nd ref="{r}"/>' for r in refs)
    tag_xml = "".join(f'<tag k="{k.replace("__", ":")}" v="{v}"/>' for k, v in tags.items())
    WAYS.append(f'<way id="{wid}" version="1">{nds}{tag_xml}</way>')


node(1, 45.4215, -75.6972, place="city", name="Ottawa", population="1017449")
node(2, 44.9040, -76.0222, place="town", name="Smiths Falls", population="9254")
node(3, 45.3088, -75.8987, place="suburb", name="Kanata")
# Bank Street: two segments along lon -75.6980.
for i, lat in enumerate((45.400, 45.405, 45.410, 45.415), start=10):
    node(i, lat, -75.6980)
way(100, [10, 11, 12], highway="secondary", name="Bank Street")
way(101, [12, 13], highway="secondary", name="Bank Street")
# Addresses: even on the west side, odd on the east, numbers rising north.
for n, lat in ((100, 45.4000), (102, 45.4010), (110, 45.4050)):
    node(1000 + n, lat, -75.6982, addr__housenumber=n, addr__street="Bank Street", addr__city="Ottawa",
         addr__postcode="K1S 3V8")
for n, lat in ((101, 45.4002), (111, 45.4052)):
    node(1000 + n, lat, -75.6978, addr__housenumber=n, addr__street="Bank St", addr__city="Ottawa",
         addr__postcode="K1S 3V9")
# 200 Bank Street: a building (closed way).
for i, (lat, lon) in enumerate(((45.4100, -75.6984), (45.4100, -75.6982), (45.4102, -75.6982), (45.4102, -75.6984)), 20):
    node(i, lat, lon)
way(200, [20, 21, 22, 23, 20], building="yes", addr__housenumber="200", addr__street="Bank Street",
    addr__city="Ottawa")
# Elgin Street: a road only.
node(30, 45.4180, -75.6900)
node(31, 45.4230, -75.6905)
way(300, [30, 31], highway="tertiary", name="Elgin Street")
# Boulevard Saint-Laurent (Ottawa), with one address.
node(40, 45.4300, -75.6400)
node(41, 45.4400, -75.6350)
way(400, [40, 41], highway="primary", name="Boulevard Saint-Laurent")
node(1400, 45.4350, -75.6377, addr__housenumber="1200", addr__street="Boulevard Saint-Laurent", addr__city="Ottawa")
# Main Street in Smiths Falls (no addr:city) and in Ottawa.
for n, lat in ((10, 44.9000), (20, 44.9010), (30, 44.9020)):
    node(2000 + n, lat, -76.0200, addr__housenumber=n, addr__street="Main Street")
node(50, 44.8990, -76.0200)
node(51, 44.9030, -76.0200)
way(500, [50, 51], highway="residential", name="Main Street")
node(3010, 45.3500, -75.6500, addr__housenumber="10", addr__street="Main Street", addr__city="Ottawa")

# Municipalities: Lanark County (level 6) around the Township of Rideau Lakes
# (level 8), with an address (no addr:city) and a road inside the township.
def square_way(wid, first_node, min_lat, min_lon, max_lat, max_lon, **tags):
    corners = ((min_lat, min_lon), (min_lat, max_lon), (max_lat, max_lon), (max_lat, min_lon))
    for i, (lat, lon) in enumerate(corners):
        node(first_node + i, lat, lon)
    way(wid, [first_node, first_node + 1, first_node + 2, first_node + 3, first_node], **tags)


square_way(600, 60, 44.90, -76.50, 45.10, -76.30, boundary="administrative", admin_level="6", name="Lanark County")
square_way(700, 70, 44.95, -76.45, 45.05, -76.35, boundary="administrative", admin_level="8",
           name="Township of Rideau Lakes")
node(4001, 45.0000, -76.4000, addr__housenumber="5", addr__street="Lakeview Lane")
node(80, 44.9990, -76.4010)
node(81, 45.0010, -76.3990)
way(800, [80, 81], highway="residential", name="Lakeview Lane")

OSM = ("<?xml version='1.0' encoding='UTF-8'?>\n<osm version=\"0.6\" generator=\"test\">\n"
       + "\n".join(NODES + WAYS) + "\n</osm>\n")


class GeocodeTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        (cls.dir / "a.osm").write_text(OSM)
        cls.out = cls.dir / "map.sqlite"
        cls.meta = build(Region("Test", 45.4215, -75.6972, 100, ["a"]), ["fuel"], cls.out, [cls.dir / "a.osm"],
                         log=lambda *_: None)
        cls.conn = sqlite3.connect(cls.out)

    def best(self, **kw):
        found = gc.geocode(self.conn, **kw)
        self.assertTrue(found, kw)
        return found[0]

    def test_tables_and_facts(self):
        self.assertEqual(self.meta["geocode_addresses"], "12")
        # Ottawa, Smiths Falls, Kanata, and the township (a municipality
        # without a place node); not the county (it has a municipality in it).
        self.assertEqual(self.meta["geocode_places"], "4")
        streets = {r[0]: r[1] for r in self.conn.execute("SELECT name, place FROM map_street")}
        self.assertEqual(streets["Elgin Street"], "Ottawa")
        # Main Street's addresses had no addr:city: the nearest town.
        places = {r[0] for r in self.conn.execute("SELECT place FROM map_street WHERE norm = 'main st'")}
        self.assertEqual(places, {"Smiths Falls", "Ottawa"})
        codes = {r[0] for r in self.conn.execute("SELECT code FROM map_postcode")}
        self.assertEqual(codes, {"K1S3V8", "K1S3V9", "K1S"})

    def test_exact_address_free_text(self):
        c = self.best(query="102 Bank St, Ottawa")
        self.assertEqual((c.precision, c.label), (gc.PRECISION_ADDRESS, "102 Bank Street, Ottawa"))
        self.assertAlmostEqual(c.latitude, 45.4010, places=4)
        self.assertEqual(c.postcode, "K1S 3V8")

    def test_building_address(self):
        c = self.best(street="Bank Street", number="200", locality="Ottawa")
        self.assertEqual(c.precision, gc.PRECISION_ADDRESS)
        self.assertAlmostEqual(c.latitude, 45.4101, places=3)

    def test_interpolated_on_the_same_side(self):
        c = self.best(query="106 Bank Street, Ottawa")
        self.assertEqual(c.precision, gc.PRECISION_INTERPOLATED)
        # Between 102 (45.4010) and 110 (45.4050), even side.
        self.assertAlmostEqual(c.latitude, 45.4030, places=4)
        self.assertAlmostEqual(c.longitude, -75.6982, places=4)
        c = self.best(query="105 Bank St")
        self.assertAlmostEqual(c.longitude, -75.6978, places=4)

    def test_unknown_number_falls_back_to_the_street(self):
        c = self.best(query="9000 Bank Street, Ottawa")
        self.assertEqual((c.precision, c.label), (gc.PRECISION_STREET, "Bank Street, Ottawa"))

    def test_street_without_addresses(self):
        c = self.best(query="Elgin St, Ottawa")
        self.assertEqual((c.precision, c.label), (gc.PRECISION_STREET, "Elgin Street, Ottawa"))

    def test_abbreviations_and_french(self):
        c = self.best(query="1200 St. Laurent Blvd, Ottawa")
        self.assertEqual((c.precision, c.label), (gc.PRECISION_ADDRESS, "1200 Boulevard Saint-Laurent, Ottawa"))

    def test_locality_picks_the_right_main_street(self):
        self.assertEqual(self.best(query="20 Main Street, Smiths Falls").label, "20 Main Street, Smiths Falls")
        self.assertEqual(self.best(query="10 Main St, Ottawa").place, "Ottawa")
        # Without commas: the place at the end is recognised.
        self.assertEqual(self.best(query="10 Main St Smiths Falls").place, "Smiths Falls")
        # A suburb of the city: streets within its reach count as in it.
        self.assertEqual(self.best(query="10 Main Street", locality="Kanata").place, "Ottawa")

    def test_near_breaks_ties(self):
        self.assertEqual(self.best(query="10 Main Street", near=(44.90, -76.02)).place, "Smiths Falls")
        self.assertEqual(self.best(query="10 Main Street", near=(45.35, -75.65)).place, "Ottawa")

    def test_postcode_and_place(self):
        c = self.best(query="K1S 3V9")
        self.assertEqual((c.precision, c.label), (gc.PRECISION_POSTCODE, "K1S 3V9"))
        c = self.best(query="K1S")
        self.assertEqual(c.precision, gc.PRECISION_POSTCODE)
        c = self.best(query="Smiths Falls")
        self.assertEqual((c.precision, c.label), (gc.PRECISION_PLACE, "Smiths Falls"))
        # A missing street with a locality: the place.
        c = self.best(query="12 Nowhere Road, Smiths Falls")
        self.assertEqual(c.precision, gc.PRECISION_PLACE)

    def test_missing_city(self):
        c = self.best(query="110 Bank Street")
        self.assertEqual((c.precision, c.label), (gc.PRECISION_ADDRESS, "110 Bank Street, Ottawa"))

    def test_municipality_for_addresses_without_a_city(self):
        c = self.best(query="5 Lakeview Lane")
        self.assertEqual((c.precision, c.label), (gc.PRECISION_ADDRESS, "5 Lakeview Lane, Rideau Lakes"))
        self.assertEqual(self.best(query="Lakeview Lane, Rideau Lakes").place, "Rideau Lakes")
        c = self.best(query="Rideau Lakes")
        self.assertEqual((c.precision, c.extra["kind"]), (gc.PRECISION_PLACE, "municipality"))

    def test_map_without_geocoding(self):
        out = self.dir / "old.sqlite"
        build(Region("Test", 45.4215, -75.6972, 100, ["a"]), ["fuel"], out, [self.dir / "a.osm"],
              log=lambda *_: None, geocoding=False)
        conn = sqlite3.connect(out)
        with self.assertRaises(gc.GeocodingUnavailable):
            gc.geocode(conn, query="102 Bank St")


if __name__ == "__main__":
    unittest.main()
