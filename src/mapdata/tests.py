import json
from django.contrib.auth.models import User
from django.db import connections
from django.test import SimpleTestCase, TestCase

from e39maps.routers import MapDataRouter
from mapdata.models import MapMeta


class RouterTests(SimpleTestCase):
    router = MapDataRouter()

    def test_map_models_read_and_write_the_map_database(self):
        self.assertEqual(self.router.db_for_read(MapMeta), "mapdata")
        self.assertEqual(self.router.db_for_write(MapMeta), "mapdata")

    def test_other_models_use_the_default(self):
        self.assertIsNone(self.router.db_for_read(User))
        self.assertIsNone(self.router.db_for_write(User))

    def test_nothing_is_migrated_into_the_map_database(self):
        self.assertFalse(self.router.allow_migrate("mapdata", "auth"))
        self.assertFalse(self.router.allow_migrate("mapdata", "mapdata"))
        self.assertFalse(self.router.allow_migrate("default", "mapdata"))
        self.assertIsNone(self.router.allow_migrate("default", "auth"))

    def test_no_relations_across_the_databases(self):
        meta, user = MapMeta(key="k", value="v"), User(username="u")
        self.assertFalse(self.router.allow_relation(meta, user))
        self.assertTrue(self.router.allow_relation(meta, MapMeta(key="x", value="y")))
        self.assertTrue(self.router.allow_relation(user, User(username="w")))


class MapMetaTests(TestCase):
    databases = {"mapdata"}

    @classmethod
    def setUpTestData(cls):
        with connections["mapdata"].cursor() as c:
            c.execute("CREATE TABLE IF NOT EXISTS e39maps_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            c.execute("INSERT OR REPLACE INTO e39maps_meta VALUES ('region', 'ontario'), ('schema_version', '1')")

    def test_as_dict(self):
        self.assertEqual(MapMeta.as_dict(), {"region": "ontario", "schema_version": "1"})


class ZoneLookupTests(SimpleTestCase):
    """mapdata.timezones on a map database (plain sqlite3, as on the device)."""

    @staticmethod
    def square(min_lon, min_lat, max_lon, max_lat):
        return [[min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat], [min_lon, max_lat], [min_lon, min_lat]]

    def database(self, areas=True, centre_zone="America/Toronto"):
        import sqlite3

        from mapdata import schema

        conn = sqlite3.connect(":memory:")
        if areas:
            schema.create(conn)
        else:   # a map built before timezone_area
            conn.execute("CREATE TABLE e39maps_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        if centre_zone:
            conn.execute("INSERT INTO e39maps_meta VALUES ('timezone', ?)", (centre_zone,))
        if areas:
            sq = self.square
            # Edmonton (west of -110) and Regina (east), Regina's area made of
            # two polygons, Edmonton's with a hole (no zone in it).
            rows = [
                ("America/Edmonton", {"type": "Polygon", "coordinates": [sq(-120, 49, -110, 60), sq(-115, 53, -114, 54)]}),
                ("America/Regina", {"type": "MultiPolygon", "coordinates": [[sq(-110, 49, -102, 55)], [sq(-110, 55, -102, 60)]]}),
            ]
            for zone, g in rows:
                lons = [p[0] for poly in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]) for p in poly[0]]
                lats = [p[1] for poly in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]) for p in poly[0]]
                conn.execute("INSERT INTO timezone_area (zone, min_lat, min_lon, max_lat, max_lon, geometry) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (zone, min(lats), min(lons), max(lats), max(lons), json.dumps(g)))
        return conn

    def test_inside_each_zone(self):
        from mapdata import timezones

        conn = self.database()
        self.assertEqual(timezones.zone_at(conn, 53.55, -113.49), ("America/Edmonton", timezones.SOURCE_BOUNDARIES))
        self.assertEqual(timezones.zone_at(conn, 50.45, -104.61), ("America/Regina", timezones.SOURCE_BOUNDARIES))
        # The second polygon of the MultiPolygon.
        self.assertEqual(timezones.zone_at(conn, 58.0, -105.0), ("America/Regina", timezones.SOURCE_BOUNDARIES))

    def test_in_a_hole_and_outside(self):
        from mapdata import timezones

        conn = self.database()
        self.assertEqual(timezones.zone_at(conn, 53.5, -114.5), ("America/Toronto", timezones.SOURCE_MAP_CENTRE))
        self.assertEqual(timezones.zone_at(conn, 45.0, -75.0), ("America/Toronto", timezones.SOURCE_MAP_CENTRE))

    def test_shared_border_belongs_to_one_zone(self):
        from mapdata import timezones

        conn = self.database()
        # Lloydminster sits on the -110 border: exactly one answer, the same each time.
        answers = {timezones.area_zone_at(conn, lat, -110.0) for lat in (50.0, 53.28, 59.9)}
        self.assertEqual(len(answers), 1)
        self.assertIn(answers.pop(), ("America/Edmonton", "America/Regina"))

    def test_map_without_areas_or_zone(self):
        from mapdata import timezones

        self.assertEqual(timezones.zone_at(self.database(areas=False), 53.55, -113.49),
                         ("America/Toronto", timezones.SOURCE_MAP_CENTRE))
        self.assertEqual(timezones.zone_at(self.database(areas=False, centre_zone=None), 53.55, -113.49),
                         (None, None))


class AddressNormalizingTests(SimpleTestCase):
    """mapdata.addresses: normal forms and free-text parsing."""

    def test_streets_compare_equal(self):
        from mapdata.addresses import normalize_street as n

        self.assertEqual(n("St. Laurent Blvd"), "saint laurent blvd")
        self.assertEqual(n("Boulevard Saint-Laurent"), "blvd saint laurent")
        self.assertEqual(n("Bank Street"), n("bank st."))
        self.assertEqual(n("Rue de la Montagne"), "rue montagne")
        self.assertEqual(n("Chemin Ste-Foy"), "ch sainte foy")
        self.assertEqual(n("King St W"), n("King Street West"))
        self.assertEqual(n("Rideau Ave"), n("Rideau Avenue"))
        self.assertEqual(n("Montée Saint-François"), "mtee saint francois")
        # A street called just "North": not a direction.
        self.assertEqual(n("North"), "north")

    def test_core_tokens(self):
        from mapdata.addresses import core_tokens, street_tokens

        self.assertEqual(core_tokens(street_tokens("King Street West")), ["king"])
        self.assertEqual(core_tokens(street_tokens("Rue Principale")), ["principale"])

    def test_postcodes(self):
        from mapdata.addresses import normalize_postcode as p

        self.assertEqual(p("k1p 1j1"), "K1P1J1")
        self.assertEqual(p("K1P"), "K1P")
        self.assertEqual(p("13662-1234"), "13662")
        self.assertIsNone(p(" "))

    def test_house_numbers(self):
        from mapdata.addresses import house_number_key as h

        self.assertEqual(h("12a"), (12, "12A"))
        self.assertEqual(h("12-14"), (12, "12-14"))
        self.assertIsNone(h("rear"))
        self.assertIsNone(h("61355512345678"))     # a phone number, not a house number

    def test_parse(self):
        from mapdata.addresses import parse

        self.assertEqual(parse("123 Bank St, Ottawa, ON K1P 1J1"),
                         {"postcode": "K1P1J1", "number": "123", "street": "Bank St", "locality": "ottawa"})
        self.assertEqual(parse("123, rue Principale, Gatineau (QC)"),
                         {"number": "123", "street": "rue Principale", "locality": "gatineau"})
        self.assertEqual(parse("12A Elm Cres"), {"number": "12A", "street": "Elm Cres"})
        self.assertEqual(parse("Bank St 123"), {"street": "Bank St", "number": "123"})
        self.assertEqual(parse("1 Main St, Massena, NY 13662"),
                         {"postcode": "13662", "number": "1", "street": "Main St", "locality": "massena"})
        self.assertEqual(parse("K1P"), {"postcode": "K1P"})
        self.assertEqual(parse("Ottawa"), {"street": "Ottawa"})
