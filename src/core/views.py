import math
import sqlite3

import django
from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.shortcuts import render

from mapdata.models import MapMeta


def index(request):
    return render(request, "core/index.html", {"django_version": django.get_version()})


def healthz(request):
    """Cheap liveness probe for systemd / the Yocto image. Also reports the
    map database: its facts (built_at, region, ...), or why it can't be read.
    A missing map database doesn't make the app unhealthy."""
    try:
        map_db = {"available": True, "meta": MapMeta.as_dict()}
    except DatabaseError as exc:
        map_db = {"available": False, "error": str(exc)}
    from django.conf import settings

    from tiles.sources import mbtiles_info

    tiles = {"mbtiles": mbtiles_info(), "upstream": settings.TILE_UPSTREAM_URL or None,
             "cache_dir": str(settings.TILE_CACHE_DIR)}
    return JsonResponse({"status": "ok", "map_db": map_db, "tiles": tiles})


def timezone(request):
    """The time zone at a position: GET /timezone?lat=45.42&lon=-75.70 ->
    {"zone": "America/Toronto", "source": "boundaries" | "map-centre"}, from
    the map's zone areas, else the map's own zone (mapdata.timezones). 400
    for a bad position; 404 when the map has no zone at all; 503 without a
    readable map database."""
    from mapdata import timezones

    try:
        lat, lon = float(request.GET["lat"]), float(request.GET["lon"])
    except (KeyError, ValueError):
        return JsonResponse({"error": "lat and lon (decimal degrees) are required"}, status=400)
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return JsonResponse({"error": "lat must be in -90..90 and lon in -180..180"}, status=400)
    try:
        conn = connections["mapdata"]
        conn.ensure_connection()
        zone, source = timezones.zone_at(conn.connection, lat, lon)
    except (DatabaseError, sqlite3.Error) as exc:
        return JsonResponse({"error": f"no map database: {exc}"}, status=503)
    if zone is None:
        return JsonResponse({"error": "the map has no time zone"}, status=404)
    return JsonResponse({"zone": zone, "source": source})
