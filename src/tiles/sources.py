"""Where map tiles (256 px raster PNGs, OSM's z/x/y scheme) come from, in order:

1. MBTiles files (pre-rendered by the map toolkit in OSM's style, shipped
   with the map; split in parts under 2 GiB): settings.TILES_MBTILES_PATH,
   a glob, opened read-only;
2. the on-device cache: settings.TILE_CACHE_DIR/<z>/<x>/<y>.png, writable,
   filled from 3;
3. the upstream tile server (settings.TILE_UPSTREAM_URL, by default
   tile.openstreetmap.org), when the device is online. OSM's tile usage
   policy (https://operations.osmfoundation.org/policies/tiles/): an
   identifying User-Agent, at most 2 connections, cache and honour the
   server's freshness, no bulk downloading. So: TILE_UPSTREAM_CONNECTIONS
   (2) at a time, tiles cached for TILE_CACHE_MAX_AGE, then revalidated
   (If-None-Match / If-Modified-Since); when the upstream can't be reached
   a stale cached tile is served rather than nothing.

TILE_UPSTREAM_URL = "" makes the backend offline: MBTiles and cache only.
So does get_tile(..., allow_upstream=False), for bulk prefetching (the HMI's
"Download Map Tiles" sends X-Tile-Prefetch): OSM's policy forbids bulk
downloads, so prefetching only copies what's already here.
"""

import json
import logging
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

log = logging.getLogger(__name__)

MAX_ZOOM = 19
USER_AGENT = "e39-rpi maps backend (car HMI; +https://github.com/linster/e39-rpi-maps)"


class TileUnavailable(Exception):
    """Not in the MBTiles file or the cache, and the upstream can't provide it."""


@dataclass
class Tile:
    png: bytes
    source: str          # mbtiles, cache, upstream, stale
    max_age: int         # seconds the client may keep it


def valid(z, x, y):
    return 0 <= z <= MAX_ZOOM and 0 <= x < 2 ** z and 0 <= y < 2 ** z


# --- 1. MBTiles ---------------------------------------------------------------

_mbtiles_local = threading.local()


def _mbtiles():
    """Read-only connections to the MBTiles files (per thread; reopened when
    the files change, e.g. after a map update)."""
    import glob

    paths = sorted(glob.glob(str(settings.TILES_MBTILES_PATH)))
    key = tuple((p, os.stat(p).st_mtime) for p in paths)
    if getattr(_mbtiles_local, "key", None) != key:
        for conn in getattr(_mbtiles_local, "conns", []):
            conn.close()
        _mbtiles_local.conns = [sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True,
                                                check_same_thread=False) for p in paths]
        _mbtiles_local.key = key
    return _mbtiles_local.conns


def from_mbtiles(z, x, y):
    for conn in _mbtiles():
        # MBTiles rows are TMS: y counted from the south.
        row = conn.execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                           (z, x, 2 ** z - 1 - y)).fetchone()
        if row:
            return row[0]
    return None


def mbtiles_info():
    conns = _mbtiles()
    if not conns:
        return None
    try:
        info = dict(conns[0].execute("SELECT name, value FROM metadata"))
        info["files"] = len(conns)
        info["tiles"] = sum(c.execute("SELECT count(*) FROM tiles").fetchone()[0] for c in conns)
        return info
    except sqlite3.Error as exc:
        return {"error": str(exc)}


# --- 2. cache -------------------------------------------------------------------

def _cache_path(z, x, y):
    return Path(settings.TILE_CACHE_DIR) / str(z) / str(x) / f"{y}.png"


def _read_cache(z, x, y):
    path = _cache_path(z, x, y)
    try:
        png = path.read_bytes()
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None, None, {}
    meta = {}
    try:
        meta = json.loads(path.with_suffix(".json").read_text())
    except (OSError, ValueError):
        pass
    return png, age, meta


def _write_cache(z, x, y, png, meta):
    path = _cache_path(z, x, y)
    path.parent.mkdir(parents=True, exist_ok=True)
    for target, data in ((path, png), (path.with_suffix(".json"), json.dumps(meta).encode())):
        tmp = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}")
        tmp.write_bytes(data)
        os.replace(tmp, target)


def _touch(z, x, y):
    try:
        os.utime(_cache_path(z, x, y))
    except OSError:
        pass


# --- 3. upstream ------------------------------------------------------------------

_upstream_slots = None
_slots_lock = threading.Lock()


def _slots():
    global _upstream_slots
    with _slots_lock:
        if _upstream_slots is None:
            _upstream_slots = threading.BoundedSemaphore(settings.TILE_UPSTREAM_CONNECTIONS)
        return _upstream_slots


def _fetch_upstream(z, x, y, meta):
    """(png or None if unchanged, new meta). Raises OSError/urllib errors."""
    url = settings.TILE_UPSTREAM_URL.format(z=z, x=x, y=y)
    headers = {"User-Agent": USER_AGENT}
    if meta.get("etag"):
        headers["If-None-Match"] = meta["etag"]
    if meta.get("last_modified"):
        headers["If-Modified-Since"] = meta["last_modified"]
    req = urllib.request.Request(url, headers=headers)
    with _slots():
        try:
            with urllib.request.urlopen(req, timeout=settings.TILE_UPSTREAM_TIMEOUT) as resp:
                ctype = resp.headers.get("Content-Type", "")
                if not ctype.startswith("image/png"):
                    raise urllib.error.URLError(f"upstream sent {ctype or 'no content type'}, not a PNG")
                png = resp.read()
                new_meta = {"etag": resp.headers.get("ETag"),
                            "last_modified": resp.headers.get("Last-Modified"), "url": url}
                return png, new_meta
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return None, meta
            raise


def get_tile(z, x, y, allow_upstream=True):
    if not valid(z, x, y):
        raise ValueError(f"no tile {z}/{x}/{y}")
    max_age = settings.TILE_CLIENT_MAX_AGE

    png = from_mbtiles(z, x, y)
    if png is not None:
        return Tile(png, "mbtiles", max_age)

    cached, age, meta = _read_cache(z, x, y)
    if cached is not None and age < settings.TILE_CACHE_MAX_AGE:
        return Tile(cached, "cache", max_age)

    if not settings.TILE_UPSTREAM_URL or not allow_upstream:
        if cached is not None:
            return Tile(cached, "stale", max_age)
        why = "offline" if allow_upstream else "for prefetching (never fetched from upstream)"
        raise TileUnavailable(f"tile {z}/{x}/{y} isn't available {why}")
    try:
        fresh, new_meta = _fetch_upstream(z, x, y, meta if cached is not None else {})
    except (OSError, urllib.error.URLError) as exc:
        if cached is not None:
            log.info("upstream unavailable for %s/%s/%s (%s): serving the cached tile", z, x, y, exc)
            return Tile(cached, "stale", max_age)
        raise TileUnavailable(f"tile {z}/{x}/{y}: upstream unavailable ({exc})") from exc
    if fresh is None:            # 304: the cached tile is still current
        _touch(z, x, y)
        return Tile(cached, "cache", max_age)
    try:
        _write_cache(z, x, y, fresh, new_meta)
    except OSError as exc:
        log.warning("can't cache tile %s/%s/%s: %s", z, x, y, exc)
    return Tile(fresh, "upstream", max_age)
