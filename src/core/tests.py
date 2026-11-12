import django
from django.db import connections
from django.test import TestCase
from django.urls import reverse


class IndexViewTests(TestCase):
    databases = {"default", "mapdata"}

    @classmethod
    def setUpTestData(cls):
        with connections["mapdata"].cursor() as c:
            c.execute("CREATE TABLE IF NOT EXISTS e39maps_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            c.execute("INSERT OR REPLACE INTO e39maps_meta VALUES ('built_at', '2026-11-12T00:00:00Z')")

    def test_index_renders_hello_world(self):
        response = self.client.get(reverse("core:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hello from Django")
        self.assertContains(response, django.get_version())

    def test_healthz_reports_the_map_database(self):
        response = self.client.get(reverse("core:healthz"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "status": "ok",
            "map_db": {"available": True, "meta": {"built_at": "2026-11-12T00:00:00Z"}},
        })


class StateDatabasePragmaTests(TestCase):
    """The PRAGMAs Django 5.0 can't set via OPTIONS["init_command"]."""

    databases = {"default", "mapdata"}

    def pragma(self, alias, name):
        with connections[alias].cursor() as c:
            c.execute("PRAGMA %s" % name)
            return c.fetchone()[0]

    def test_state_database_gets_the_pragmas(self):
        self.assertEqual(self.pragma("default", "foreign_keys"), 1)
        self.assertEqual(self.pragma("default", "synchronous"), 1)   # NORMAL

    def test_map_database_is_left_alone(self):
        self.assertEqual(self.pragma("mapdata", "synchronous"), 2)   # FULL, the default
