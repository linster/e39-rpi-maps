import django
from django.db import DatabaseError
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
