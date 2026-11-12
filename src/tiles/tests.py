import os
import sqlite3
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from . import sources

PNG = b"\x89PNG\r\n\x1a\n" + b"upstream-tile"


class FakeTileServer:
    """Counts requests, answers PNGs with an ETag, 304 for a matching
    If-None-Match; can be switched off (connection refused)."""

    def __init__(self):
        self.requests, self.user_agents, self.active, self.max_active = [], [], 0, 0
        self.delay = 0
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                with fake.lock:
                    fake.active += 1
                    fake.max_active = max(fake.max_active, fake.active)
                    fake.requests.append(self.path)
                    fake.user_agents.append(self.headers.get("User-Agent"))
                time.sleep(fake.delay)
                with fake.lock:
                    fake.active -= 1
                if self.path.startswith("/notpng/"):
                    body, ctype = b"<html>", "text/html"
                elif self.headers.get("If-None-Match") == '"v1"':
                    self.send_response(304)
                    self.end_headers()
                    return
                else:
                    body, ctype = PNG + self.path.encode(), "image/png"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("ETag", '"v1"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}" + "/{z}/{x}/{y}.png"


class TileTests(SimpleTestCase):

    def setUp(self):
        self.fake = FakeTileServer()
        self.addCleanup(self.fake.server.shutdown)
        self.cache = Path(tempfile.mkdtemp())
        self.override = override_settings(
            TILE_CACHE_DIR=self.cache, TILE_UPSTREAM_URL=self.fake.url,
            TILES_MBTILES_PATH=self.cache / "none.mbtiles", TILE_CACHE_MAX_AGE=3600,
            TILE_UPSTREAM_TIMEOUT=5)
        self.override.enable()
        self.addCleanup(self.override.disable)
        sources._upstream_slots = None

    def get(self, z, x, y):
        return self.client.get(f"/tiles/{z}/{x}/{y}.png")

    def test_fetches_upstream_then_serves_from_the_cache(self):
        r = self.get(12, 1189, 1467)
        self.assertEqual((r.status_code, r["Content-Type"], r["X-Tile-Source"]), (200, "image/png", "upstream"))
        self.assertEqual(r.content, PNG + b"/12/1189/1467.png")
        self.assertTrue((self.cache / "12" / "1189" / "1467.png").exists())
        self.assertIn("e39-rpi maps backend", self.fake.user_agents[0])
        r = self.get(12, 1189, 1467)
        self.assertEqual(r["X-Tile-Source"], "cache")
        self.assertEqual(len(self.fake.requests), 1)

    def test_old_cached_tile_is_revalidated(self):
        self.get(5, 1, 2)
        path = self.cache / "5" / "1" / "2.png"
        os.utime(path, (time.time() - 7200, time.time() - 7200))
        r = self.get(5, 1, 2)                        # 304: still current
        self.assertEqual((r.status_code, r["X-Tile-Source"]), (200, "cache"))
        self.assertEqual(len(self.fake.requests), 2)
        self.assertGreater(path.stat().st_mtime, time.time() - 60)

    def test_offline_serves_stale_or_404(self):
        self.get(5, 1, 2)
        os.utime(self.cache / "5" / "1" / "2.png", (0, 0))
        self.fake.server.shutdown()
        self.fake.server.server_close()
        r = self.get(5, 1, 2)
        self.assertEqual((r.status_code, r["X-Tile-Source"]), (200, "stale"))
        self.assertEqual(self.get(5, 3, 3).status_code, 404)
        with self.settings(TILE_UPSTREAM_URL=""):
            self.assertEqual(self.get(5, 1, 2)["X-Tile-Source"], "stale")
            self.assertEqual(self.get(6, 1, 1).status_code, 404)

    def test_bad_coordinates_and_non_png(self):
        self.assertEqual(self.get(3, 8, 0).status_code, 400)       # x >= 2^z
        self.assertEqual(self.get(20, 0, 0).status_code, 400)
        with self.settings(TILE_UPSTREAM_URL=self.fake.url.replace("/{z}", "/notpng/{z}")):
            self.assertEqual(self.get(4, 1, 1).status_code, 404)
        self.assertFalse((self.cache / "4").exists())

    def test_mbtiles_first_with_tms_rows(self):
        mb = self.cache / "tiles.mbtiles"
        conn = sqlite3.connect(mb)
        conn.execute("CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)")
        conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
        conn.execute("INSERT INTO metadata VALUES ('name', 'test')")
        conn.execute("INSERT INTO tiles VALUES (3, 2, 7 - 1, ?)", (b"\x89PNGmbtiles",))   # y=1 in XYZ
        conn.commit()
        conn.close()
        with self.settings(TILES_MBTILES_PATH=mb):
            r = self.get(3, 2, 1)
            self.assertEqual((r["X-Tile-Source"], r.content), ("mbtiles", b"\x89PNGmbtiles"))
            self.assertEqual(self.get(3, 2, 2)["X-Tile-Source"], "upstream")     # not in the file
            self.assertEqual(sources.mbtiles_info(), {"name": "test"})
        self.assertEqual(len(self.fake.requests), 1)

    def test_at_most_two_upstream_connections(self):
        self.fake.delay = 0.2
        threads = [threading.Thread(target=self.get, args=(10, i, 0)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.fake.requests), 8)
        self.assertLessEqual(self.fake.max_active, 2)
