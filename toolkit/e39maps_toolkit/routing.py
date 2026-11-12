"""Routing tiles (Valhalla) for the map, built with pyvalhalla's tools.

Car only: footways, cycleways and platforms are left out (the HMI routes a
car), which keeps the tiles small (Ontario + Quebec: ~625 MB). Building
takes ~5 minutes and ~13 GB of RAM on a 12-thread laptop.

The tiles only depend on the extracts and the Valhalla version, not on the
POI categories, so they're cached: changing categories or the circle and
rebuilding reuses them.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Valhalla's build log is chatty; these lines are progress worth showing.
STAGE_WORDS = ("Parsing", "Sorting", "Finished", "Building", "Enhancing", "Validat",
               "Creating", "Tiles", "tile set", "Writing", "Done")


def valhalla_version():
    import valhalla

    return getattr(valhalla, "__version__", "")


def _bin(name):
    import valhalla

    path = Path(valhalla.__file__).parent / "bin" / name
    if not path.exists():
        raise FileNotFoundError(f"pyvalhalla has no {name} (pip install pyvalhalla)")
    return path


def _config(work):
    import valhalla

    config = valhalla.get_config(tile_extract="", tile_dir="")
    config["mjolnir"].update(
        tile_dir=str(work / "tiles"),
        tile_extract=str(work / "tiles.tar"),
        admin="", timezone="",
        include_pedestrian=False, include_bicycle=False, include_platforms=False,
        concurrency=os.cpu_count() or 4,
    )
    return config


def _run(cmd, log, cancelled):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        if cancelled():
            proc.terminate()
            proc.wait()
            raise InterruptedError("cancelled")
        line = line.rstrip()
        if "Remove file" in line:
            continue
        stage = "[INFO]" in line and any(w in line for w in STAGE_WORDS)
        if stage or "[ERROR]" in line or "[WARN]" in line:
            log("  " + line.split("[0m", 1)[-1].strip())
    if proc.wait() != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} failed (exit {proc.returncode})")


def build_tiles(extract_paths, sources, cache_root, log=print, cancelled=lambda: False):
    """Return the path of the routing tiles (tar) for these extracts, building
    them if the cache doesn't have them. sources: the extracts' ids and OSM
    timestamps (the cache key)."""
    version = valhalla_version()
    key = hashlib.sha1(json.dumps({"sources": sources, "valhalla": version, "profile": "car"},
                                  sort_keys=True).encode()).hexdigest()[:16]
    root = Path(cache_root)
    work = root / key
    tar = work / "tiles.tar"
    if tar.exists():
        log(f"Routing tiles: cached ({tar.stat().st_size / 1e6:.0f} MB, Valhalla {version})")
        return tar, version

    # One cached set at a time: each is hundreds of MB.
    if root.exists():
        for old in root.iterdir():
            shutil.rmtree(old, ignore_errors=True)
    work.mkdir(parents=True)
    conf = work / "valhalla.json"
    conf.write_text(json.dumps(_config(work), indent=1))
    log(f"Building routing tiles with Valhalla {version} (car only; a few minutes, ~13 GB RAM)...")
    try:
        _run([str(_bin("valhalla_build_tiles")), "-c", str(conf), *map(str, extract_paths)], log, cancelled)
        log("Packing the routing tiles into one file...")
        _run([sys.executable, "-m", "valhalla.valhalla_build_extract", "-c", str(conf), "-v"], log, cancelled)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    shutil.rmtree(work / "tiles", ignore_errors=True)   # the tar has them all
    log(f"Routing tiles: {tar.stat().st_size / 1e6:.0f} MB")
    return tar, version
