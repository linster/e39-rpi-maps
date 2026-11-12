"""The gRPC API, through a real server and client stubs.

The server runs in-process on a free port; its calls run on the server's
threads with their own database connections, so these are
TransactionTestCases (a TestCase's transaction isn't visible to them).
"""

import json
import unittest
from pathlib import Path

import grpc
from django.test import TransactionTestCase, override_settings
from google.protobuf.field_mask_pb2 import FieldMask

from e39maps_api.v1 import common_pb2, pois_pb2, pois_pb2_grpc, routes_pb2, routes_pb2_grpc
from pois.models import Poi

from .server import build_server


def latlng(lat, lon):
    return common_pb2.LatLng(latitude=lat, longitude=lon)


# TransactionTestCase flushes after each test, which fires post_migrate:
# without this, every test would recreate the development admin.
@override_settings(DEV_ADMIN="")
class GrpcTestCase(TransactionTestCase):

    def setUp(self):
        self.server, port = build_server("127.0.0.1:0", max_workers=2)
        self.server.start()
        self.channel = grpc.insecure_channel(f"127.0.0.1:{port}")
        self.pois = pois_pb2_grpc.PoiServiceStub(self.channel)
        self.routes = routes_pb2_grpc.RouteServiceStub(self.channel)

    def tearDown(self):
        self.channel.close()
        self.server.stop(grace=None).wait()

    def assertStatus(self, code, call, *args):
        with self.assertRaises(grpc.RpcError) as cm:
            call(*args)
        self.assertEqual(cm.exception.code(), code, cm.exception.details())
        return cm.exception


class PoiCrudTests(GrpcTestCase):

    def create(self, name="Home", lat=45.42, lon=-75.69, **kwargs):
        return self.pois.CreatePoi(pois_pb2.CreatePoiRequest(
            poi=pois_pb2.Poi(name=name, location=latlng(lat, lon), **kwargs)))

    def test_create_get_list_delete(self):
        icon = pois_pb2.PoiIcon(colored_circle=pois_pb2.PoiIcon.ColoredCircle(argb=0xFFFF7A1A))
        created = self.create(icon=icon, visible=True)
        self.assertGreater(created.id, 0)
        self.assertEqual(created.source, pois_pb2.POI_SOURCE_USER)
        self.assertEqual(created.icon.colored_circle.argb, 0xFFFF7A1A)
        self.assertTrue(created.HasField("created") and created.HasField("updated"))
        self.assertEqual(Poi.objects.get(pk=created.id).name, "Home")

        self.assertEqual(self.pois.GetPoi(pois_pb2.GetPoiRequest(id=created.id)), created)

        self.create(name="Work", visible=False)
        names = [p.name for p in self.pois.ListPois(pois_pb2.ListPoisRequest()).pois]
        self.assertEqual(names, ["Home", "Work"])
        visible = self.pois.ListPois(pois_pb2.ListPoisRequest(visible_only=True)).pois
        self.assertEqual([p.name for p in visible], ["Home"])

        self.pois.DeletePoi(pois_pb2.DeletePoiRequest(id=created.id))
        self.assertFalse(Poi.objects.filter(pk=created.id).exists())

    def test_icons_round_trip(self):
        bundled = self.create(icon=pois_pb2.PoiIcon(bundled=pois_pb2.PoiIcon.BundledIcon(
            file_name="poi_fuel", tint_argb=0xFF00FF00)))
        self.assertEqual((bundled.icon.bundled.file_name, bundled.icon.bundled.tint_argb), ("poi_fuel", 0xFF00FF00))
        self.assertEqual(Poi.objects.get(pk=bundled.id).icon_tint, 0xFF00FF00)
        untinted = self.create(icon=pois_pb2.PoiIcon(bundled=pois_pb2.PoiIcon.BundledIcon(file_name="poi_fuel")))
        self.assertEqual(untinted.icon.bundled.tint_argb, 0)
        self.assertIsNone(Poi.objects.get(pk=untinted.id).icon_tint)
        plain = self.create(name="No icon")
        self.assertEqual(plain.icon.WhichOneof("kind"), "none")

    def test_update_with_mask_changes_only_those_fields(self):
        poi = self.create(visible=True)
        updated = self.pois.UpdatePoi(pois_pb2.UpdatePoiRequest(
            poi=pois_pb2.Poi(id=poi.id, name="Cottage", visible=False),
            update_mask=FieldMask(paths=["name"])))
        self.assertEqual(updated.name, "Cottage")
        self.assertTrue(updated.visible)
        self.assertEqual(updated.location, poi.location)

    def test_update_without_mask_replaces_editable_fields(self):
        poi = self.create(visible=True)
        updated = self.pois.UpdatePoi(pois_pb2.UpdatePoiRequest(
            poi=pois_pb2.Poi(id=poi.id, name="Moved", location=latlng(1, 2), visible=False)))
        self.assertEqual((updated.name, updated.location, updated.visible), ("Moved", latlng(1, 2), False))
        self.assertEqual(updated.created, poi.created)

    def test_not_found(self):
        code = grpc.StatusCode.NOT_FOUND
        self.assertStatus(code, self.pois.GetPoi, pois_pb2.GetPoiRequest(id=999))
        self.assertStatus(code, self.pois.DeletePoi, pois_pb2.DeletePoiRequest(id=999))
        self.assertStatus(code, self.pois.UpdatePoi, pois_pb2.UpdatePoiRequest(poi=pois_pb2.Poi(id=999, name="x")))

    def test_invalid_arguments(self):
        code = grpc.StatusCode.INVALID_ARGUMENT
        self.assertStatus(code, self.create, "")
        self.assertStatus(code, self.create, "   ")
        self.assertStatus(code, self.create, "North of north", 91, 0)
        self.assertStatus(code, self.create, "Far east", 0, 181)
        self.assertStatus(code, self.pois.CreatePoi, pois_pb2.CreatePoiRequest(poi=pois_pb2.Poi(name="Nowhere")))
        poi = self.create()
        err = self.assertStatus(code, self.pois.UpdatePoi, pois_pb2.UpdatePoiRequest(
            poi=pois_pb2.Poi(id=poi.id), update_mask=FieldMask(paths=["id"])))
        self.assertIn("not editable: id", err.details())
        self.assertEqual(Poi.objects.count(), 1)


class PoiSearchTests(GrpcTestCase):

    def setUp(self):
        super().setUp()
        # Ottawa area: Parliament, ~1.1 km east, ~5.5 km west, and far away.
        for name, lat, lon, visible in (
            ("Parliament", 45.4236, -75.7009, True),
            ("Rideau Centre", 45.4255, -75.6920, True),
            ("Westboro Shell", 45.3920, -75.7530, False),
            ("Toronto Shell", 43.6532, -79.3832, True),
        ):
            Poi.objects.create(name=name, latitude=lat, longitude=lon, visible=visible)

    def search(self, **kwargs):
        return self.pois.SearchPois(pois_pb2.SearchPoisRequest(**kwargs)).results

    def test_near_sorts_by_distance_and_honours_radius(self):
        near = pois_pb2.SearchPoisRequest.Near(center=latlng(45.4236, -75.7009), radius_meters=10_000)
        results = self.search(near=near)
        self.assertEqual([r.poi.name for r in results], ["Parliament", "Rideau Centre", "Westboro Shell"])
        self.assertAlmostEqual(results[0].distance_meters, 0, places=3)
        self.assertTrue(600 < results[1].distance_meters < 1000, results[1].distance_meters)

        near.radius_meters = 1000
        self.assertEqual([r.poi.name for r in self.search(near=near)], ["Parliament", "Rideau Centre"])

    def test_bounding_box_and_name_and_visibility(self):
        box = common_pb2.BoundingBox(south_west=latlng(45.0, -76.0), north_east=latlng(46.0, -75.0))
        self.assertEqual([r.poi.name for r in self.search(bounding_box=box)],
                         ["Parliament", "Rideau Centre", "Westboro Shell"])
        self.assertEqual([r.poi.name for r in self.search(bounding_box=box, visible_only=True)],
                         ["Parliament", "Rideau Centre"])
        self.assertEqual([r.poi.name for r in self.search(name_contains="shell")],
                         ["Toronto Shell", "Westboro Shell"])
        self.assertEqual([r.poi.name for r in self.search(bounding_box=box, name_contains="SHELL")],
                         ["Westboro Shell"])

    def test_limit_and_sources(self):
        self.assertEqual(len(self.search()), 4)
        self.assertEqual(len(self.search(limit=2)), 2)
        self.assertStatus(grpc.StatusCode.INVALID_ARGUMENT, self.pois.SearchPois,
                          pois_pb2.SearchPoisRequest(limit=-1))
        # No map database in these tests: no map POIs, and no error.
        self.assertEqual(len(self.search(sources=[pois_pb2.POI_SOURCE_MAP])), 0)

    def test_near_needs_a_positive_radius(self):
        self.assertStatus(grpc.StatusCode.INVALID_ARGUMENT, self.pois.SearchPois, pois_pb2.SearchPoisRequest(
            near=pois_pb2.SearchPoisRequest.Near(center=latlng(45, -75))))


class RouteTests(GrpcTestCase):

    def test_unavailable_without_routing_tiles(self):
        from . import routing

        routing._router = None
        with self.settings(ROUTING_TILES_PATH="/nonexistent/e39maps-routing.tar"):
            err = self.assertStatus(grpc.StatusCode.UNAVAILABLE, self.routes.ComputeRoute,
                                    routes_pb2.ComputeRouteRequest(origin=latlng(45, -75), destination=latlng(46, -75)))
        routing._router = None
        self.assertIn("no routing tiles", err.details())

    def test_trigger_radius(self):
        from .routing import trigger_radius

        self.assertAlmostEqual(trigger_radius(2000, 72), 2000 / 72 * 15)      # 100 km/h: ~417 m
        self.assertAlmostEqual(trigger_radius(50000, 1500), 500.0)                  # 120 km/h: 500 m
        self.assertAlmostEqual(trigger_radius(90000, 2000), 675.0)
        self.assertAlmostEqual(trigger_radius(200000, 2000), 1000.0)                # capped
        self.assertAlmostEqual(trigger_radius(400, 60), 100.0)                      # 24 km/h: 100 m
        self.assertAlmostEqual(trigger_radius(300, 100), 45.0)                      # slow: 45 m
        self.assertAlmostEqual(trigger_radius(100, 60), 30.0)                       # minimum 30 m
        self.assertAlmostEqual(trigger_radius(20, 2), 20.0)                         # not past the previous turn
        self.assertAlmostEqual(trigger_radius(5, 1), 15.0)                          # ...but at least 15 m
        self.assertAlmostEqual(trigger_radius(0, 0), 30.0)

    def test_decode_polyline6(self):
        from .routing import decode_polyline6

        # Valhalla's example: Google's "_p~iF~ps|U_ulLnnqC_mqNvxq`@" at 1e5
        # is (38.5, -120.2), (40.7, -120.95), (43.252, -126.453); at 1e6
        # the same text means a tenth of that.
        self.assertEqual(decode_polyline6("_p~iF~ps|U_ulLnnqC_mqNvxq`@"),
                         [(3.85, -12.02), (4.07, -12.095), (4.3252, -12.6453)])


def _valhalla_bin():
    try:
        import valhalla
    except ImportError:
        return None
    path = Path(valhalla.__file__).parent / "bin" / "valhalla_build_tiles"
    return path if path.exists() else None


# A small grid of residential streets west of Parliament Hill (~0.004 deg
# apart, ~300 m), "Main Street" east-west, "First Avenue" north-south, plus
# an isolated street far away (unreachable).
GRID_OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <node id="1" lat="45.420" lon="-75.710" version="1"/>
  <node id="2" lat="45.420" lon="-75.706" version="1"/>
  <node id="3" lat="45.420" lon="-75.702" version="1"/>
  <node id="4" lat="45.424" lon="-75.706" version="1"/>
  <node id="5" lat="45.416" lon="-75.706" version="1"/>
  <node id="6" lat="45.424" lon="-75.702" version="1"/>
  <node id="20" lat="45.500" lon="-75.500" version="1"/>
  <node id="21" lat="45.501" lon="-75.500" version="1"/>
  <way id="10" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/><tag k="highway" v="residential"/><tag k="name" v="Main Street"/></way>
  <way id="11" version="1"><nd ref="5"/><nd ref="2"/><nd ref="4"/><tag k="highway" v="residential"/><tag k="name" v="First Avenue"/></way>
  <way id="12" version="1"><nd ref="4"/><nd ref="6"/><nd ref="3"/><tag k="highway" v="residential"/><tag k="name" v="Second Street"/></way>
  <way id="13" version="1"><nd ref="20"/><nd ref="21"/><tag k="highway" v="residential"/><tag k="name" v="Island Road"/></way>
</osm>
"""


@unittest.skipIf(_valhalla_bin() is None, "pyvalhalla (with valhalla_build_tiles) isn't installed")
class ValhallaRouteTests(GrpcTestCase):
    """ComputeRoute with real Valhalla tiles, built from GRID_OSM."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import subprocess
        import tempfile

        import osmium
        import valhalla

        cls.tmp = Path(tempfile.mkdtemp())
        xml = cls.tmp / "grid.osm"
        xml.write_text(GRID_OSM)
        pbf = cls.tmp / "grid.osm.pbf"
        with osmium.SimpleWriter(str(pbf)) as writer:
            for obj in osmium.FileProcessor(str(xml)):
                writer.add(obj)
        config = valhalla.get_config(tile_extract="", tile_dir="")
        config["mjolnir"].update(tile_dir=str(cls.tmp / "tiles"), tile_extract=str(cls.tmp / "tiles.tar"),
                                 admin="", timezone="", concurrency=1)
        conf = cls.tmp / "valhalla.json"
        conf.write_text(json.dumps(config))
        subprocess.run([str(_valhalla_bin()), "-c", str(conf), str(pbf)], check=True, capture_output=True)
        import sys
        subprocess.run([sys.executable, "-m", "valhalla.valhalla_build_extract", "-c", str(conf)],
                       check=True, capture_output=True)
        cls.tiles = cls.tmp / "tiles.tar"

    def setUp(self):
        super().setUp()
        from . import routing

        routing._router = None
        self.override = self.settings(ROUTING_TILES_PATH=str(self.tiles))
        self.override.enable()

    def tearDown(self):
        from . import routing

        self.override.disable()
        routing._router = None
        super().tearDown()

    def route(self, *points, **kwargs):
        origin, *via, destination = [latlng(*p) for p in points]
        return self.routes.ComputeRoute(routes_pb2.ComputeRouteRequest(
            origin=origin, via=via, destination=destination, **kwargs))

    def test_route_with_turn_instructions(self):
        # West end of Main Street -> north end of First Avenue: east, then left.
        route = self.route((45.420, -75.710), (45.424, -75.706))
        self.assertGreater(len(route.shape), 2)
        self.assertAlmostEqual(route.shape[0].latitude, 45.420, places=3)
        self.assertAlmostEqual(route.shape[-1].latitude, 45.424, places=3)
        self.assertTrue(600 < route.length_meters < 900, route.length_meters)
        self.assertGreater(route.duration_seconds, 0)
        types = [m.type for m in route.maneuvers]
        self.assertEqual(types[0], routes_pb2.MANEUVER_TYPE_START)
        self.assertIn(routes_pb2.MANEUVER_TYPE_LEFT, types)
        self.assertEqual(types[-1], routes_pb2.MANEUVER_TYPE_DESTINATION)
        left = route.maneuvers[types.index(routes_pb2.MANEUVER_TYPE_LEFT)]
        self.assertIn("First Avenue", left.street_names)
        self.assertIn("left", left.instruction.lower())
        self.assertEqual(route.maneuvers[-1].end_shape_index, len(route.shape) - 1)
        # Each maneuver has its point and a trigger radius within the bounds.
        for m in route.maneuvers:
            self.assertEqual(m.location, route.shape[m.begin_shape_index])
            self.assertTrue(15 <= m.trigger_radius_meters <= 1000, m.trigger_radius_meters)
        self.assertAlmostEqual(left.location.latitude, 45.420, places=3)
        self.assertAlmostEqual(left.location.longitude, -75.706, places=3)

    def test_via_point_joins_legs(self):
        direct = self.route((45.420, -75.710), (45.420, -75.702))
        via = self.route((45.420, -75.710), (45.424, -75.706), (45.420, -75.702))
        self.assertGreater(via.length_meters, direct.length_meters)
        indexes = [i for m in via.maneuvers for i in (m.begin_shape_index, m.end_shape_index)]
        self.assertEqual(max(indexes), len(via.shape) - 1)
        self.assertEqual(indexes, sorted(indexes))

    def test_miles_and_french(self):
        km = self.route((45.420, -75.710), (45.424, -75.706))
        mi = self.route((45.420, -75.710), (45.424, -75.706), units=routes_pb2.UNITS_MILES, language="fr-FR")
        self.assertAlmostEqual(km.length_meters, mi.length_meters, delta=5)
        self.assertIn("gauche", " ".join(m.instruction for m in mi.maneuvers).lower())

    def test_no_route_and_bad_input(self):
        self.assertStatus(grpc.StatusCode.NOT_FOUND, self.route, (45.420, -75.710), (45.5005, -75.500))
        self.assertStatus(grpc.StatusCode.INVALID_ARGUMENT, self.route, (95, 0), (45.42, -75.70))


class MapPoiSearchTests(GrpcTestCase):
    """SearchPois with source MAP: POIs from the read-only map database."""

    databases = {"default", "mapdata"}

    def setUp(self):
        super().setUp()
        from django.db import connections

        from mapdata import schema

        ddl = (schema.SCHEMA.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ")
               .replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS "))
        with connections["mapdata"].cursor() as c:
            for statement in filter(str.strip, ddl.split(";")):
                c.execute(statement)
            c.execute("DELETE FROM map_poi")
            c.executemany("INSERT INTO map_poi VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", [
                (schema.poi_id("node", 1), "node", 1, "fuel", "Esso Bank St", 45.4240, -75.7000,
                 '{"kind": "amenity=fuel", "brand": "Esso"}'),
                (schema.poi_id("way", 2), "way", 2, "museum", "Bytown Museum", 45.4260, -75.6970,
                 '{"kind": "tourism=museum"}'),
                (schema.poi_id("node", 3), "node", 3, "fuel", "Far Fuel", 43.65, -79.38, "{}"),
            ])
        Poi.objects.create(name="Parliament", latitude=45.4236, longitude=-75.7009)

    def tearDown(self):
        # The map database isn't flushed between tests (Django doesn't manage
        # it): drop the table so other tests see no map data.
        from django.db import connections

        with connections["mapdata"].cursor() as c:
            c.execute("DROP TABLE IF EXISTS map_poi")
        super().tearDown()

    def search(self, **kwargs):
        return self.pois.SearchPois(pois_pb2.SearchPoisRequest(**kwargs)).results

    def test_near_merges_user_and_map_pois_by_distance(self):
        near = pois_pb2.SearchPoisRequest.Near(center=latlng(45.4236, -75.7009), radius_meters=2000)
        results = self.search(near=near, sources=[pois_pb2.POI_SOURCE_USER, pois_pb2.POI_SOURCE_MAP])
        self.assertEqual([r.poi.name for r in results], ["Parliament", "Esso Bank St", "Bytown Museum"])
        esso = results[1].poi
        self.assertEqual(esso.source, pois_pb2.POI_SOURCE_MAP)
        self.assertEqual(esso.category, pois_pb2.POI_CATEGORY_FUEL)
        self.assertEqual(dict(esso.details), {"kind": "amenity=fuel", "brand": "Esso"})
        self.assertEqual(esso.id, 1 * 4 + 1)
        self.assertEqual(results[0].poi.category, pois_pb2.POI_CATEGORY_UNSPECIFIED)

    def test_default_sources_stay_user_only(self):
        self.assertEqual([r.poi.name for r in self.search()], ["Parliament"])

    def test_categories_filter_map_pois_and_exclude_user_pois(self):
        results = self.search(sources=[pois_pb2.POI_SOURCE_USER, pois_pb2.POI_SOURCE_MAP],
                              categories=[pois_pb2.POI_CATEGORY_FUEL])
        self.assertEqual([r.poi.name for r in results], ["Esso Bank St", "Far Fuel"])

    def test_bounding_box_and_name_across_sources_by_name(self):
        box = common_pb2.BoundingBox(south_west=latlng(45.0, -76.0), north_east=latlng(46.0, -75.0))
        both = [pois_pb2.POI_SOURCE_USER, pois_pb2.POI_SOURCE_MAP]
        self.assertEqual([r.poi.name for r in self.search(bounding_box=box, sources=both)],
                         ["Bytown Museum", "Esso Bank St", "Parliament"])
        self.assertEqual([r.poi.name for r in self.search(name_contains="MUSEUM", sources=both)],
                         ["Bytown Museum"])
        self.assertEqual(len(self.search(sources=both, limit=2)), 2)

    def test_missing_map_database_gives_user_pois_only(self):
        from django.db import connections

        with connections["mapdata"].cursor() as c:
            c.execute("DROP TABLE map_poi")
        results = self.search(sources=[pois_pb2.POI_SOURCE_USER, pois_pb2.POI_SOURCE_MAP])
        self.assertEqual([r.poi.name for r in results], ["Parliament"])
