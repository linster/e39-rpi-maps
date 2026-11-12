"""gRPC services for the HMI (proto/e39maps_api/v1)."""

import functools
import logging
import math

import grpc
from django.core.exceptions import ValidationError
from django.db import DatabaseError, close_old_connections

from e39maps_api.v1 import pois_pb2, pois_pb2_grpc, routes_pb2_grpc
from mapdata.models import MapPoi
from pois.models import Poi

from .convert import EDITABLE, apply_pb_to_poi, category_from_pb, map_poi_to_pb, poi_to_pb

log = logging.getLogger(__name__)

SEARCH_DEFAULT_LIMIT = 50
SEARCH_MAX_LIMIT = 500
EARTH_RADIUS_M = 6371008.8


def rpc(method):
    """Run a servicer method like a Django request: gRPC calls run on the
    server's own threads, so release stale database connections around each
    call, and log unexpected errors before gRPC turns them into UNKNOWN."""

    @functools.wraps(method)
    def wrapper(self, request, context):
        close_old_connections()
        try:
            return method(self, request, context)
        except grpc.RpcError:
            raise
        except Exception:
            if context.code() is None:      # not an abort()
                log.exception("%s failed", method.__name__)
            raise
        finally:
            close_old_connections()

    return wrapper


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


class PoiService(pois_pb2_grpc.PoiServiceServicer):

    def _get(self, poi_id, context):
        try:
            return Poi.objects.get(pk=poi_id)
        except Poi.DoesNotExist:
            context.abort(grpc.StatusCode.NOT_FOUND, f"no POI with id {poi_id}")

    def _save(self, poi, context):
        if not poi.name:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "a POI needs a name")
        try:
            poi.full_clean()
        except ValidationError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "; ".join(
                f"{field}: {' '.join(msgs)}" for field, msgs in exc.message_dict.items()))
        poi.save()
        return poi_to_pb(poi)

    @rpc
    def ListPois(self, request, context):
        pois = Poi.objects.all()
        if request.visible_only:
            pois = pois.filter(visible=True)
        return pois_pb2.ListPoisResponse(pois=[poi_to_pb(p) for p in pois])

    @rpc
    def GetPoi(self, request, context):
        return poi_to_pb(self._get(request.id, context))

    @rpc
    def CreatePoi(self, request, context):
        if not request.poi.HasField("location"):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "a POI needs a location")
        poi = Poi()
        apply_pb_to_poi(request.poi, poi)
        return self._save(poi, context)

    @rpc
    def UpdatePoi(self, request, context):
        poi = self._get(request.poi.id, context)
        fields = list(request.update_mask.paths) or list(EDITABLE)
        unknown = [f for f in fields if f not in EDITABLE]
        if unknown:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                          f"not editable: {', '.join(unknown)} (editable: {', '.join(EDITABLE)})")
        apply_pb_to_poi(request.poi, poi, fields)
        return self._save(poi, context)

    @rpc
    def DeletePoi(self, request, context):
        self._get(request.id, context).delete()
        from google.protobuf.empty_pb2 import Empty
        return Empty()

    @rpc
    def SearchPois(self, request, context):
        sources = set(request.sources) or {pois_pb2.POI_SOURCE_USER}
        limit = request.limit or SEARCH_DEFAULT_LIMIT
        if limit < 0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "limit must not be negative")
        limit = min(limit, SEARCH_MAX_LIMIT)
        area = request.WhichOneof("area")
        if area == "near" and request.near.radius_meters <= 0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "near.radius_meters must be > 0")
        categories = {category_from_pb(c) for c in request.categories}

        found = []  # (distance, name, PoiResult) from each source
        if pois_pb2.POI_SOURCE_USER in sources and not categories:
            pois = Poi.objects.all()
            if request.visible_only:
                pois = pois.filter(visible=True)
            found += self._search(pois, request, area, limit, poi_to_pb)
        if pois_pb2.POI_SOURCE_MAP in sources:
            pois = MapPoi.objects.all()
            if categories:
                pois = pois.filter(category__in=categories)
            try:
                found += self._search(pois, request, area, limit, map_poi_to_pb)
            except DatabaseError as exc:
                # No (readable) map database: user POIs only. /healthz says why.
                log.warning("SearchPois: map database unavailable: %s", exc)

        found.sort(key=lambda f: (f[0], f[1].casefold()) if area == "near" else (f[1].casefold(), f[0]))
        return pois_pb2.SearchPoisResponse(results=[r for _, _, r in found[:limit]])

    @staticmethod
    def _search(pois, request, area, limit, to_pb):
        """Apply the name/area filters to a queryset with name, latitude and
        longitude; return up to limit (distance, name, PoiResult)."""
        if request.name_contains:
            pois = pois.filter(name__icontains=request.name_contains)
        if area == "bounding_box":
            sw, ne = request.bounding_box.south_west, request.bounding_box.north_east
            pois = pois.filter(latitude__gte=sw.latitude, latitude__lte=ne.latitude,
                               longitude__gte=sw.longitude, longitude__lte=ne.longitude)
        if area != "near":
            return [(0.0, p.name, pois_pb2.PoiResult(poi=to_pb(p))) for p in pois.order_by("name")[:limit]]

        center, radius = request.near.center, request.near.radius_meters
        # Cheap box first (uses the location index), exact distance after.
        dlat = math.degrees(radius / EARTH_RADIUS_M)
        dlon = dlat / max(math.cos(math.radians(center.latitude)), 1e-6)
        box = pois.filter(latitude__gte=center.latitude - dlat, latitude__lte=center.latitude + dlat,
                          longitude__gte=center.longitude - dlon, longitude__lte=center.longitude + dlon)
        near = sorted(
            ((d, p) for p in box
             if (d := haversine_m(center.latitude, center.longitude, p.latitude, p.longitude)) <= radius),
            key=lambda dp: dp[0])[:limit]
        return [(d, p.name, pois_pb2.PoiResult(poi=to_pb(p), distance_meters=d)) for d, p in near]


class RouteService(routes_pb2_grpc.RouteServiceServicer):

    @rpc
    def ComputeRoute(self, request, context):
        from .routing import BadRequest, NoRoute, RoutingUnavailable, get_router

        try:
            return get_router().route(request)
        except BadRequest as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except NoRoute as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, f"no route: {exc}")
        except RoutingUnavailable as exc:
            context.abort(grpc.StatusCode.UNAVAILABLE, f"routing unavailable: {exc}")
