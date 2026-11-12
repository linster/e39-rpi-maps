"""Routing with Valhalla, in-process (pyvalhalla on the laptop, the Yocto
recipe's python3-valhalla on the device).

The routing tiles are one tar file (settings.ROUTING_TILES_PATH) built by
the map toolkit together with the map database; Valhalla memory-maps it.
One Actor serves all calls, behind a lock: Valhalla's Actor isn't
thread-safe, and routes are requested rarely enough that one at a time is
fine on a Pi.
"""

import json
import logging
import threading
from pathlib import Path

from django.conf import settings

from e39maps_api.v1 import common_pb2, routes_pb2

log = logging.getLogger(__name__)


class RoutingUnavailable(Exception):
    """No routing: Valhalla or the routing tiles are missing."""


class NoRoute(Exception):
    """Valhalla found no route (e.g. a point far from any road)."""


class BadRequest(Exception):
    pass


# Valhalla error codes (https://valhalla.github.io/valhalla/api/turn-by-turn/api-reference/#http-status-codes-and-conditions)
NO_ROUTE_CODES = {171, 442, 443}  # no suitable edges near location; no path found
# pyvalhalla's ValhallaError carries only the text, not the code.
NO_ROUTE_TEXTS = ("no suitable edges", "no path could be found", "unconnected regions",
                  "exceeds the max distance")


def decode_polyline6(encoded):
    """Valhalla's shape: Google's polyline encoding with 6 decimals."""
    coords, index, lat, lon = [], 0, 0, 0
    while index < len(encoded):
        for axis in (0, 1):
            result = shift = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        coords.append((lat / 1e6, lon / 1e6))
    return coords


# Maneuver trigger radius: TRIGGER_SECONDS at the approach speed, clamped.
TRIGGER_SECONDS = 15
TRIGGER_MIN_M = 30
TRIGGER_MAX_M = 1000


def trigger_radius(approach_length_m, approach_seconds):
    """How close to a maneuver the HMI should announce it, from the stretch
    leading to it (the previous maneuver's length and time)."""
    if approach_length_m <= 0 or approach_seconds <= 0:
        return float(TRIGGER_MIN_M)
    radius = min(max(approach_length_m / approach_seconds * TRIGGER_SECONDS, TRIGGER_MIN_M), TRIGGER_MAX_M)
    # Not beyond the previous maneuver (but a short stretch still gets 15 m).
    return float(min(radius, max(approach_length_m, 15)))


class Router:

    def __init__(self, tiles_path, cache_mb=64):
        self.tiles_path = Path(tiles_path)
        self.cache_mb = cache_mb
        self._actor = None
        self._lock = threading.Lock()

    def _get_actor(self):
        if self._actor is None:
            if not self.tiles_path.is_file():
                raise RoutingUnavailable(f"no routing tiles at {self.tiles_path}")
            try:
                import valhalla
            except ImportError as exc:
                raise RoutingUnavailable(f"Valhalla isn't installed ({exc})") from exc
            config = valhalla.get_config(tile_extract=self.tiles_path, tile_dir="")
            config["mjolnir"]["max_cache_size"] = self.cache_mb * 1024 * 1024
            self._actor = valhalla.Actor(config)
            log.info("Valhalla ready: %s", self.tiles_path)
        return self._actor

    def route(self, request: routes_pb2.ComputeRouteRequest) -> routes_pb2.Route:
        points = [request.origin, *request.via, request.destination]
        for p in points:
            if not (-90 <= p.latitude <= 90 and -180 <= p.longitude <= 180):
                raise BadRequest(f"coordinates out of range: {p.latitude}, {p.longitude}")
        if not request.HasField("origin") or not request.HasField("destination"):
            raise BadRequest("a route needs an origin and a destination")

        miles = request.units == routes_pb2.UNITS_MILES
        query = {
            # "break": the via points are stops (each one ends a leg).
            "locations": [{"lat": p.latitude, "lon": p.longitude, "type": "break"} for p in points],
            "costing": "auto",
            "units": "miles" if miles else "kilometers",
            "language": request.language or "en-US",
            "directions_type": "instructions",
        }
        with self._lock:
            actor = self._get_actor()
            try:
                answer = actor.route(json.dumps(query))
            except RuntimeError as exc:
                raise self._error(exc) from exc
        return self._to_pb(json.loads(answer)["trip"], 1609.344 if miles else 1000.0)

    @staticmethod
    def _error(exc):
        text = str(exc)
        try:
            info = json.loads(text)
            code, message = info.get("error_code"), info.get("error") or text
        except ValueError:
            code, message = None, text
        if code in NO_ROUTE_CODES or any(t in message.lower() for t in NO_ROUTE_TEXTS):
            return NoRoute(message)
        if code is not None and 100 <= code < 200:
            return BadRequest(message)
        return exc

    @staticmethod
    def _to_pb(trip, unit_m):
        route = routes_pb2.Route()
        offset = 0
        for leg in trip["legs"]:
            shape = decode_polyline6(leg["shape"])
            # Legs share their joining point: drop it from the later leg.
            skip = 1 if route.shape else 0
            for lat, lon in shape[skip:]:
                route.shape.append(common_pb2.LatLng(latitude=lat, longitude=lon))
            for m in leg["maneuvers"]:
                # Valhalla's type + 1 (0 is "unspecified" in the proto);
                # transit types (31-34 there) are reserved, not values.
                ptype = m.get("type", -1) + 1
                if ptype not in routes_pb2.ManeuverType.values():
                    ptype = routes_pb2.MANEUVER_TYPE_UNSPECIFIED
                route.maneuvers.append(routes_pb2.Maneuver(
                    type=ptype,
                    instruction=m.get("instruction", ""),
                    verbal_pre_transition=m.get("verbal_pre_transition_instruction", ""),
                    street_names=m.get("street_names", []),
                    length_meters=m.get("length", 0.0) * unit_m,
                    duration_seconds=m.get("time", 0.0),
                    begin_shape_index=m.get("begin_shape_index", 0) + offset,
                    end_shape_index=m.get("end_shape_index", 0) + offset,
                    roundabout_exit_count=m.get("roundabout_exit_count", 0),
                ))
            offset += len(shape) - 1
        previous = None
        for m in route.maneuvers:
            if 0 <= m.begin_shape_index < len(route.shape):
                m.location.CopyFrom(route.shape[m.begin_shape_index])
            m.trigger_radius_meters = (trigger_radius(previous.length_meters, previous.duration_seconds)
                                       if previous else float(TRIGGER_MIN_M))
            previous = m
        route.length_meters = trip["summary"]["length"] * unit_m
        route.duration_seconds = trip["summary"]["time"]
        return route


_router = None
_router_lock = threading.Lock()


def get_router():
    global _router
    with _router_lock:
        if _router is None:
            _router = Router(settings.ROUTING_TILES_PATH, settings.ROUTING_CACHE_MB)
        return _router
