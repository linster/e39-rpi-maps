"""Publishing and fetching maps, against a fake GitHub (local HTTP server).

The fake implements the few release endpoints the toolkit uses, including
the asset download's redirect to separate storage, which must be fetched
without the Authorization header.
"""

import json
import os
import re
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from e39maps_toolkit import push
from e39maps_toolkit.build import Region, build
from mapdata import release, schema

from test_toolkit import OSM_A

TOKEN = "test-token"


class FakeGitHub:

    def __init__(self):
        self.releases, self.assets, self.next_id = {}, {}, 1
        self.commits = {"abc123"}
        self.storage_auth_headers = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, body=None, headers=None):
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def authed(self):
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    self.reply(401, {"message": "Bad credentials"})
                    return False
                return True

            def do_GET(self):
                path = self.path.split("?")[0]
                if path.startswith("/storage/"):
                    fake.storage_auth_headers.append(self.headers.get("Authorization"))
                    data = fake.assets[int(path.rsplit("/", 1)[1])]["data"]
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if not self.authed():
                    return
                if m := re.fullmatch(r"/repos/[^/]+/[^/]+/releases/tags/(.+)", path):
                    r = fake.releases.get(m.group(1))
                    return self.reply(200, fake.public(r)) if r else self.reply(404, {"message": "Not Found"})
                if re.fullmatch(r"/repos/[^/]+/[^/]+/releases", path):
                    page = int(m.group(1)) if (m := re.search(r"[?&]page=(\d+)", self.path)) else 1
                    items = [fake.public(r) for r in fake.releases.values()]
                    return self.reply(200, items if page == 1 else [])
                if m := re.fullmatch(r"/repos/[^/]+/[^/]+/commits/(.+)", path):
                    return self.reply(200 if m.group(1) in fake.commits else 404, {})
                if m := re.fullmatch(r"/repos/[^/]+/[^/]+/releases/assets/(\d+)", path):
                    host = self.headers["Host"]
                    return self.reply(302, None, {"Location": f"http://{host}/storage/{m.group(1)}"})
                self.reply(404, {"message": "Not Found"})

            def do_POST(self):
                if not self.authed():
                    return
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                path = self.path.split("?")[0]
                if re.fullmatch(r"/repos/[^/]+/[^/]+/releases", path):
                    req = json.loads(body)
                    rid = fake.next_id
                    fake.next_id += 1
                    fake.releases[req["tag_name"]] = {"id": rid, "tag_name": req["tag_name"], "name": req["name"],
                                                      "target": req["target_commitish"], "body": req["body"],
                                                      "assets": []}
                    return self.reply(201, {"id": rid, "tag_name": req["tag_name"]})
                if m := re.fullmatch(r"/repos/[^/]+/[^/]+/releases/(\d+)/assets", path):
                    name = re.search(r"name=([^&]+)", self.path).group(1)
                    r = next(r for r in fake.releases.values() if r["id"] == int(m.group(1)))
                    aid = fake.next_id
                    fake.next_id += 1
                    fake.assets[aid] = {"id": aid, "name": name, "size": len(body), "data": body}
                    r["assets"].append(aid)
                    return self.reply(201, {"id": aid, "name": name})
                self.reply(404, {})

            def do_DELETE(self):
                if not self.authed():
                    return
                rid = int(self.path.rsplit("/", 1)[1])
                for tag, r in list(fake.releases.items()):
                    if r["id"] == rid:
                        del fake.releases[tag]
                self.reply(204)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def public(self, r):
        return {**{k: v for k, v in r.items() if k != "assets"},
                "assets": [{k: v for k, v in self.assets[a].items() if k != "data"} for a in r["assets"]]}

    def client(self, tok=TOKEN):
        return release.GitHub("me/maps", api=self.url, uploads=self.url, tok=tok)


class ReleaseTests(unittest.TestCase):

    def setUp(self):
        self.fake = FakeGitHub()
        self.addCleanup(self.fake.server.shutdown)
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "a.osm").write_text(OSM_A)
        tiles = self.dir / "tiles.tar"
        tiles.write_bytes(os.urandom(300_000))
        self.db = self.dir / "local" / "e39maps-osm.sqlite"
        self.meta = build(Region("Test", 45.4215, -75.6972, 100, ["a"]), ["fuel", "park"], self.db,
                          [self.dir / "a.osm"], log=lambda *_: None, routing=(tiles, "3.9.1"))
        (self.db.parent / "ATTRIBUTION.txt").write_text("(c) OSM")
        self.repo = self.dir / "repo"
        self.repo.mkdir()

    def publish(self):
        return release.publish(self.db, self.repo, gh=self.fake.client(), log=lambda *_: None, target="abc123")

    def test_publish_uploads_the_pair_and_pins_it(self):
        manifest = self.publish()
        tag = release.tag_for(self.meta)
        self.assertEqual(manifest["tag"], tag)
        r = self.fake.releases[tag]
        self.assertEqual(r["target"], "abc123")
        names = sorted(self.fake.assets[a]["name"] for a in r["assets"])
        self.assertEqual(names, ["ATTRIBUTION.txt", "e39maps-osm.routing.tar", "e39maps-osm.sqlite",
                                 "map-release.json"])
        self.assertEqual(json.loads((self.repo / "map-release.json").read_text()), manifest)
        self.assertEqual(manifest["files"]["routing"]["sha256"], self.meta["routing_sha256"])
        self.assertIn("Valhalla 3.9.1", r["body"])
        with self.assertRaisesRegex(release.ReleaseError, "exists already"):
            self.publish()

    def test_publish_and_fetch_carry_tile_files(self):
        raster = self.dir / "t1.mbtiles"
        raster.write_bytes(os.urandom(5000))
        tiles = self.dir / "tiles2.tar"
        tiles.write_bytes(os.urandom(3000))
        db = self.dir / "local2" / "e39maps-osm.sqlite"
        build(Region("Test", 45.4215, -75.6972, 100, ["a"]), ["fuel"], db, [self.dir / "a.osm"],
              log=lambda *_: None, routing=(tiles, "3.9.1"), raster_files=[raster],
              raster_info={"tiles_zooms": "6-13", "tiles_count": 7})
        manifest = release.publish(db, self.repo, gh=self.fake.client(), log=lambda *_: None, target="abc123")
        self.assertEqual(sorted(manifest["files"]), ["database", "routing", "tiles-01"])
        self.assertIn("zooms 6-13, 7 tiles in 1 file(s)", self.fake.releases[manifest["tag"]]["body"])
        dest = self.dir / "fetched2"
        release.fetch(manifest, dest, gh=self.fake.client(), log=lambda *_: None)
        self.assertEqual((dest / "e39maps-osm.tiles-01.mbtiles").read_bytes(), raster.read_bytes())

    def test_publish_needs_the_commit_on_github(self):
        with self.assertRaisesRegex(release.ReleaseError, "push e39-rpi-maps first"):
            release.publish(self.db, self.repo, gh=self.fake.client(), log=lambda *_: None, target="notpushed")
        self.assertEqual(self.fake.releases, {})

    def test_fetch_release_downloads_checks_and_skips_storage_auth(self):
        self.publish()
        tag = release.tag_for(self.meta)
        dest = self.dir / "fetched"
        db = release.fetch_release(tag, dest, gh=self.fake.client(), log=lambda *_: None)
        self.assertEqual(db.read_bytes(), self.db.read_bytes())
        meta = schema.check(db)
        schema.check_routing(meta, dest / meta["routing_file"])
        self.assertTrue((dest / "ATTRIBUTION.txt").exists())
        # The token never went to the storage host.
        self.assertTrue(self.fake.storage_auth_headers)
        self.assertEqual(set(self.fake.storage_auth_headers), {None})
        # Again: already there, nothing downloaded.
        before = len(self.fake.storage_auth_headers)
        release.fetch_release(tag, dest, gh=self.fake.client(), log=lambda *_: None)
        self.assertEqual(len(self.fake.storage_auth_headers), before + 1)   # only the manifest

    def test_fetch_refuses_to_replace_a_different_map_and_detects_corruption(self):
        manifest = self.publish()
        dest = self.dir / "other"
        dest.mkdir()
        (dest / "e39maps-osm.sqlite").write_text("not this map")
        with self.assertRaisesRegex(release.ReleaseError, "use --force"):
            release.fetch(manifest, dest, gh=self.fake.client(), log=lambda *_: None)
        # Corrupt the uploaded tiles: the checksum catches it, nothing is left behind.
        aid = next(a for a in self.fake.releases[manifest["tag"]]["assets"]
                   if self.fake.assets[a]["name"].endswith(".tar"))
        data = self.fake.assets[aid]["data"]
        self.fake.assets[aid]["data"] = bytes([data[0] ^ 0xFF]) + data[1:]    # always a different byte
        with self.assertRaisesRegex(release.ReleaseError, "doesn't match the manifest"):
            release.fetch(manifest, dest, gh=self.fake.client(), log=lambda *_: None, force=True)
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["e39maps-osm.sqlite"])
        self.assertEqual((dest / "e39maps-osm.sqlite").read_text(), "not this map")

    def test_bad_token_and_listing(self):
        with self.assertRaisesRegex(release.ReleaseError, "gh auth login"):
            self.fake.client("wrong").list_releases()
        self.publish()
        self.assertEqual([r["tag_name"] for r in self.fake.client().list_releases()],
                         [release.tag_for(self.meta)])

    def test_copy_to_usb(self):
        stick = self.dir / "stick"
        stick.mkdir()
        (stick / "rpimaps").mkdir()          # any case: reuse the existing folder
        folder = push.copy_to_usb(self.db, stick, log=lambda *_: None)
        self.assertEqual(folder.name, "rpimaps")
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["e39maps-osm.routing.tar", "e39maps-osm.sqlite"])
        meta = schema.check(folder / "e39maps-osm.sqlite")
        schema.check_routing(meta, folder / meta["routing_file"])
        with self.assertRaisesRegex(push.PushError, "already"):
            push.copy_to_usb(self.db, stick, log=lambda *_: None)


if __name__ == "__main__":
    unittest.main()
