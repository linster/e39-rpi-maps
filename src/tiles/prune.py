"""Keeping the tile cache under a size limit (settings.TILE_CACHE_MAX_MB).

A cached tile's mtime is when it was fetched or last revalidated with the
upstream (sources.py), and tiles in use are revalidated weekly, so the
oldest mtimes are the tiles nobody has looked at for longest: those go
first. When the cache is over the limit it's pruned to 90% of it, so it
isn't pruned again after every few new tiles. A tile's .json (ETag) goes
with it; empty directories are removed.
"""

import os
from pathlib import Path

LOW_WATER = 0.9


def scan(cache_dir):
    """[(mtime, bytes incl. its .json, png path)] for every cached tile."""
    tiles = []
    for dirpath, _dirs, files in os.walk(cache_dir):
        for name in files:
            if not name.endswith(".png"):
                continue
            png = Path(dirpath) / name
            try:
                st = png.stat()
            except OSError:
                continue
            size = st.st_size
            try:
                size += png.with_suffix(".json").stat().st_size
            except OSError:
                pass
            tiles.append((st.st_mtime, size, png))
    return tiles


def prune(cache_dir, max_bytes):
    """Returns (tiles before, bytes before, tiles removed, bytes removed)."""
    cache_dir = Path(cache_dir)
    if not cache_dir.is_dir():
        return 0, 0, 0, 0
    tiles = scan(cache_dir)
    total = sum(t[1] for t in tiles)
    removed = freed = 0
    if total > max_bytes:
        target = max_bytes * LOW_WATER
        for _mtime, size, png in sorted(tiles, key=lambda t: t[0]):
            if total - freed <= target:
                break
            for path in (png, png.with_suffix(".json")):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            removed += 1
            freed += size
        # Bottom-up; emptiness checked now (os.walk listed the subdirectories
        # before their contents were removed).
        for dirpath, _dirs, _files in os.walk(cache_dir, topdown=False):
            if Path(dirpath) != cache_dir and not os.listdir(dirpath):
                try:
                    os.rmdir(dirpath)
                except OSError:
                    pass
    return len(tiles), total, removed, freed
