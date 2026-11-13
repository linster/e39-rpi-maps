from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound
from django.views.decorators.http import require_GET

from .sources import TileUnavailable, get_tile


@require_GET
def tile(request, z, x, y):
    """A 256 px map tile, OSM's z/x/y scheme (see sources.py for where it
    comes from). 404 when it isn't available (offline and not cached).
    X-Tile-Prefetch: 1 (bulk downloads): MBTiles and cache only."""
    prefetch = request.headers.get("X-Tile-Prefetch", "") not in ("", "0")
    try:
        t = get_tile(z, x, y, allow_upstream=not prefetch)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc), content_type="text/plain")
    except TileUnavailable as exc:
        return HttpResponseNotFound(str(exc), content_type="text/plain")
    response = HttpResponse(t.png, content_type="image/png")
    response["Cache-Control"] = f"max-age={t.max_age}"
    response["X-Tile-Source"] = t.source
    return response
