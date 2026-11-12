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
