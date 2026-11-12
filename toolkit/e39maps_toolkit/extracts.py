"""Downloading OpenStreetMap extracts from Geofabrik, cached on this machine.

An extract id is its path on download.geofabrik.de without the suffix, e.g.
"north-america/canada/ontario". Files are kept in the cache directory and
only downloaded again when Geofabrik has a newer one (checked with
Last-Modified), and verified against Geofabrik's .md5 file.
"""

import email.utils
import hashlib
import os
import time
import urllib.request
from pathlib import Path

BASE_URL = "https://download.geofabrik.de"
USER_AGENT = "e39maps-toolkit (https://github.com/linster/e39-rpi-maps)"


def default_cache_dir():
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "e39maps-toolkit" / "extracts"


def url_for(extract_id):
    return f"{BASE_URL}/{extract_id}-latest.osm.pbf"


def local_path(cache_dir, extract_id):
    return Path(cache_dir) / (extract_id.replace("/", "_") + "-latest.osm.pbf")


def _request(url, method="GET"):
    return urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})


def _md5(path, progress=None):
    h = hashlib.md5()
    size, done = path.stat().st_size, 0
    with open(path, "rb") as f:
        while chunk := f.read(4 << 20):
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, size)
    return h.hexdigest()


def fetch(extract_id, cache_dir, log=print, progress=None, cancelled=lambda: False):
    """Return the local path of the extract, downloading it if it's missing
    or Geofabrik has a newer one. progress(done_bytes, total_bytes)."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path, url = local_path(cache_dir, extract_id), url_for(extract_id)

    with urllib.request.urlopen(_request(url, "HEAD"), timeout=60) as resp:
        size = int(resp.headers.get("Content-Length", 0))
        modified = email.utils.parsedate_to_datetime(resp.headers["Last-Modified"]).timestamp()
    if path.exists() and path.stat().st_size == size and abs(path.stat().st_mtime - modified) < 1:
        log(f"{extract_id}: up to date in the cache ({size / 1e6:.0f} MB)")
        return path

    log(f"{extract_id}: downloading {size / 1e6:.0f} MB from {url}")
    part = path.with_suffix(".part")
    started = time.monotonic()
    with urllib.request.urlopen(_request(url), timeout=60) as resp, open(part, "wb") as out:
        done = 0
        while chunk := resp.read(1 << 20):
            if cancelled():
                raise InterruptedError("cancelled")
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, size)
    log(f"{extract_id}: downloaded in {time.monotonic() - started:.0f} s; checking MD5")

    with urllib.request.urlopen(_request(url + ".md5"), timeout=60) as resp:
        expected = resp.read().decode().split()[0]
    actual = _md5(part, progress)
    if actual != expected:
        part.unlink()
        raise IOError(f"{extract_id}: MD5 mismatch (got {actual}, Geofabrik says {expected})")
    os.utime(part, (modified, modified))
    part.replace(path)
    return path
