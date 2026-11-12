"""The gRPC API, through a real server and client stubs.

The server runs in-process on a free port; its calls run on the server's
threads with their own database connections, so these are
TransactionTestCases (a TestCase's transaction isn't visible to them).
"""

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
        # Map POIs don't exist yet in v1.
        self.assertEqual(len(self.search(sources=[pois_pb2.POI_SOURCE_MAP])), 0)

    def test_near_needs_a_positive_radius(self):
        self.assertStatus(grpc.StatusCode.INVALID_ARGUMENT, self.pois.SearchPois, pois_pb2.SearchPoisRequest(
            near=pois_pb2.SearchPoisRequest.Near(center=latlng(45, -75))))


class RouteTests(GrpcTestCase):

    def test_compute_route_is_unimplemented_until_valhalla(self):
        err = self.assertStatus(grpc.StatusCode.UNIMPLEMENTED, self.routes.ComputeRoute,
                                routes_pb2.ComputeRouteRequest(origin=latlng(45, -75), destination=latlng(46, -75)))
        self.assertIn("Valhalla", err.details())
