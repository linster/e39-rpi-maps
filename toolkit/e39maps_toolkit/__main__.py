"""e39-rpi map toolkit.

  toolkit/map-toolkit.sh                     the window (default)
  toolkit/map-toolkit.sh build [--output F]  download extracts, build the map database
  toolkit/map-toolkit.sh info [F]            show a map database's facts
  toolkit/map-toolkit.sh push [--target T]   live swap on QEMU (default) or host[:port]
  toolkit/map-toolkit.sh update-image [--machine M] [--no-build]
  toolkit/map-toolkit.sh publish            publish the local map as a GitHub release
  toolkit/map-toolkit.sh releases           list the published maps
  toolkit/map-toolkit.sh pin TAG            pin a published map for image builds (map-release.json)
  toolkit/map-toolkit.sh fetch [TAG]        download a published map (default: the pinned one)
  toolkit/map-toolkit.sh usb DIR            copy a map to a USB stick (DIR/RPIMAPS)
  toolkit/render-tiles.sh                   render offline map tiles (OSM style; podman)

push and usb take --release TAG to use a published map (downloaded first)
instead of the local one. Logging in to GitHub: gh auth login.

Settings (region, categories, paths, targets) come from the window's saved
settings (~/.config/e39maps-toolkit/settings.json).
"""

import argparse
import json
import sys
from pathlib import Path

from mapdata import schema

from . import extracts
from .build import build, extract_timestamp
from .config import MACHINES, QEMU_TARGET, Settings, routing_cache_dir, toolkit_version
from .push import PushError, copy_to_usb, live_swap, update_image
from mapdata import release
from .config import REPO
from . import versions


def run_build(settings, output=None, log=print, progress=None, status=None, cancelled=lambda: False):
    """Download (progress(done, total) bytes) and build (status(file, POIs so far))."""
    out = Path(output or settings.output)
    if out.exists():
        try:
            schema.check(out, quick_check=False)
        except schema.InvalidMapDatabase as exc:
            raise SystemExit(f"Not replacing {out}: it isn't a map database built by this toolkit "
                             f"({exc}). Move it away or choose another output.")
    paths = [extracts.fetch(e, settings.cache_dir, log=log, progress=progress, cancelled=cancelled)
             for e in settings.region.extracts]
    routing = None
    if settings.routing:
        from . import routing as valhalla_routing

        sources = [{"id": e, "osm_timestamp": extract_timestamp(p), "size": p.stat().st_size}
                   for e, p in zip(settings.region.extracts, paths)]
        routing = valhalla_routing.build_tiles(paths, sources, routing_cache_dir(settings.cache_dir),
                                               log=log, cancelled=cancelled)
    raster_files, raster_info = [], None
    if settings.raster.get("include", True):
        from . import rastertiles

        work = render_work(settings)[0]
        raster_files = rastertiles.rendered_files(work)
        if raster_files:
            raster_info = rastertiles.summarize(raster_files)
            sources = work / "sources.json"
            if sources.exists():
                dates = sorted(s["osm_timestamp"] for s in json.loads(sources.read_text()) if s.get("osm_timestamp"))
                if dates:
                    raster_info["tiles_osm_timestamp"] = dates[0]
            log(f"Offline map tiles: {len(raster_files)} file(s), {raster_info['tiles_count']:,} tiles "
                f"(zooms {raster_info['tiles_zooms']}), rendered for this region")
        else:
            log("No offline map tiles rendered for this region (toolkit/render-tiles.sh): "
                "the map will have none")
    from . import timezones

    boundaries = timezones.fetch_boundaries(settings.cache_dir, log=log, progress=progress, cancelled=cancelled)
    return build(settings.region, settings.categories, out, paths, log=log, progress=status, cancelled=cancelled,
                 toolkit_version=toolkit_version(), routing=routing, raster_files=raster_files,
                 raster_info=raster_info, timezone_boundaries=boundaries)


def published_dir(settings, tag):
    """Where downloaded releases are kept (one folder per tag)."""
    return Path(settings.cache_dir).parent / "published" / tag


def fetch_published(settings, tag=None, dest=None, log=print, progress=None, force=False):
    """Download a published map; returns its database path. tag None: the
    one map-release.json pins, else the newest."""
    gh = release.GitHub()
    if tag is None:
        pinned = release.read_manifest(REPO)
        if pinned:
            tag = pinned["tag"]
            log(f"The pinned map ({release.MANIFEST_NAME}): {tag}")
        else:
            found = gh.list_releases()
            if not found:
                raise release.ReleaseError(f"no published maps on {gh.repo}")
            tag = found[0]["tag_name"]
            log(f"The newest published map: {tag}")

    def bytes_progress(name, done, total):
        if progress:
            progress(done / total if total else 0, f"{name}: {done / 1e6:.0f} / {total / 1e6:.0f} MB")

    return release.fetch_release(tag, dest or published_dir(settings, tag), gh=gh, log=log,
                                 progress=bytes_progress, force=force)


def render_work(settings):
    """The render work folder for the region: keyed by the circle and which
    extracts it uses, not by their dates, so Geofabrik's daily updates don't
    orphan a render (tiles from last week's OSM data are fine); the data a
    render used is in its sources.json, and render-tiles.sh --refresh
    redoes it with the current extracts."""
    import hashlib

    from .build import extract_timestamp
    from .config import render_root

    paths = [extracts.local_path(settings.cache_dir, e) for e in settings.region.extracts]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise SystemExit(f"extracts not downloaded yet (build the map first): {', '.join(missing)}")
    r = settings.region
    key = hashlib.sha1(json.dumps({
        "circle": [r.center_lat, r.center_lon, r.radius_km],
        "extracts": sorted(r.extracts),
    }, sort_keys=True).encode()).hexdigest()[:12]
    return render_root(settings.cache_dir) / key, paths


def render_prepare(settings, log=print):
    """Clip the extracts to the circle (+10 km) and merge them into
    <work>/region.osm.pbf with osmium-tool, find the towns. Prints the work
    folder last (render-tiles.sh reads it)."""
    import subprocess

    from . import rastertiles
    from .regions import circle

    work, paths = render_work(settings)
    work.mkdir(parents=True, exist_ok=True)
    region_pbf = work / "region.osm.pbf"
    r = settings.region
    if not region_pbf.exists():
        ring = [[lon, lat] for lat, lon in circle(r.center_lat, r.center_lon, r.radius_km + 10, 180)]
        ring.append(ring[0])
        (work / "circle.geojson").write_text(json.dumps(
            {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [ring]}}))
        log(f"Clipping {len(paths)} extract(s) to the circle (osmium)...")
        merged = work / "merged.osm.pbf"
        subprocess.run(["osmium", "merge", *map(str, paths), "-o", str(merged), "--overwrite"], check=True)
        subprocess.run(["osmium", "extract", "-p", str(work / "circle.geojson"), "-s", "smart",
                        str(merged), "-o", str(region_pbf) + ".part", "-f", "pbf", "--overwrite"], check=True)
        merged.unlink()
        Path(str(region_pbf) + ".part").rename(region_pbf)
        from .build import extract_timestamp

        (work / "sources.json").write_text(json.dumps(
            [{"id": e, "osm_timestamp": extract_timestamp(p)} for e, p in zip(r.extracts, paths)]))
    towns_file = work / "towns.json"
    if not towns_file.exists():
        towns = rastertiles.find_towns([region_pbf], r.center_lat, r.center_lon, r.radius_km)
        towns_file.write_text(json.dumps(towns, ensure_ascii=False))
    log(f"Prepared {region_pbf} ({region_pbf.stat().st_size / 1e6:.0f} MB)")
    print(work)
    return work


def render_plan_lines(settings, work, zoom=None):
    """The plan's tiles not in the MBTiles yet, one per 8x8 metatile, as
    "x y z" lines for renderd's render_list (it draws whole metatiles)."""
    from . import rastertiles

    plan = rastertiles.TilePlan.from_dict(settings.raster)
    towns = json.loads((Path(work) / "towns.json").read_text())
    tiles = rastertiles.plan_tiles(plan, settings.region, towns)
    done = rastertiles.MBTilesWriter.existing(Path(work), "tiles")
    metas = sorted({(z, x // rastertiles.METATILE * rastertiles.METATILE, y // rastertiles.METATILE * rastertiles.METATILE)
                    for z, x, y in tiles if (z, x, y) not in done and (zoom is None or z == zoom)})
    return [f"{x} {y} {z}" for z, x, y in metas], sorted({t[0] for t in tiles})


def render_tiles(settings, server, work=None, log=print, progress=None, cancelled=lambda: False):
    """Render the plan's tiles from the local tile server into
    <work>/tiles-NN.mbtiles."""
    from . import rastertiles

    work = Path(work) if work else render_work(settings)[0]
    plan = rastertiles.TilePlan.from_dict(settings.raster)
    towns = json.loads((work / "towns.json").read_text())
    tiles = rastertiles.plan_tiles(plan, settings.region, towns)
    counts, est = rastertiles.estimate(tiles)
    log(f"Plan: {len(tiles):,} tiles (~{est / 1e9:.1f} GB), per zoom {counts}; {len(towns)} towns")
    r = settings.region
    writer = rastertiles.MBTilesWriter(work, "tiles", {
        "name": r.name, "format": "png", "type": "baselayer", "version": "1",
        "description": "OpenStreetMap (openstreetmap-carto), rendered by the e39-rpi map toolkit",
        "attribution": "(c) OpenStreetMap contributors",
        "minzoom": min(counts), "maxzoom": max(counts),
        "center": f"{r.center_lon},{r.center_lat},{min(counts) + 4}",
    })
    try:
        rastertiles.render(tiles, server, writer, log=log, progress=progress, cancelled=cancelled)
    finally:
        files = writer.close()
    for f in files:
        log(f"  {f.name}: {f.stat().st_size / 1e6:.0f} MB")
    return files


def describe(path):
    meta = schema.check(path, quick_check=False)
    lines = [f"{path}: {Path(path).stat().st_size / 1e6:.1f} MB"]
    lines += [f"  {k}: {v}" for k, v in meta.items()]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="map-toolkit", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("gui")
    p = sub.add_parser("build")
    p.add_argument("--output")
    p = sub.add_parser("info")
    p.add_argument("db", nargs="?")
    p = sub.add_parser("push")
    p.add_argument("--target", default=None, help=f"host[:port] (default: the saved target, {QEMU_TARGET})")
    p.add_argument("--db")
    p.add_argument("--release", metavar="TAG", help="a published map instead of the local one")
    p = sub.add_parser("publish")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--pin", dest="pin", action="store_true", default=None,
                   help="pin it in map-release.json even if the pinned map is another region")
    g.add_argument("--no-pin", dest="pin", action="store_false",
                   help="don't pin it (default: pin when nothing is pinned or it's the same region)")
    sub.add_parser("releases")
    p = sub.add_parser("pin", help="pin a published map for image builds (writes map-release.json)")
    p.add_argument("tag")
    p = sub.add_parser("fetch")
    p.add_argument("tag", nargs="?", help="default: the pinned map (map-release.json), else the newest")
    p.add_argument("--dest", help="folder (default: the toolkit's cache, published/<tag>)")
    p.add_argument("--force", action="store_true", help="replace a different map in --dest")
    p = sub.add_parser("render-prepare")
    p.add_argument("--dry-run", action="store_true", help="only print the work folder")
    p = sub.add_parser("render-plan", help="metatiles still to draw, as render_list input")
    p.add_argument("--work", required=True)
    p.add_argument("--zoom", type=int)
    p.add_argument("--zooms", action="store_true", help="just list the plan's zoom levels")
    p = sub.add_parser("render-tiles")
    p.add_argument("--server", required=True, help="the tile server's URL, with {z} {x} {y}")
    p.add_argument("--work")
    p = sub.add_parser("usb")
    p.add_argument("dir", help="the USB stick's mount point")
    p.add_argument("--db")
    p.add_argument("--release", metavar="TAG", help="a published map instead of the local one")
    p = sub.add_parser("update-image")
    p.add_argument("--machine", choices=MACHINES)
    p.add_argument("--yocto")
    p.add_argument("--db")
    p.add_argument("--no-build", action="store_true", help="only install into the build's map data")
    args = parser.parse_args(argv)
    settings = Settings.load()
    if args.command in ("build", "push", "update-image"):
        for problem in versions.problems(settings.yocto_dir):
            print(f"WARNING: {problem}", file=sys.stderr)

    try:
        if args.command in (None, "gui"):
            from .gui import run
            run(settings)
        elif args.command == "build":
            run_build(settings, args.output)
        elif args.command == "info":
            print(describe(args.db or settings.output))
        elif args.command == "push":
            db = fetch_published(settings, args.release) if args.release else (args.db or settings.output)
            live_swap(db, args.target or settings.target, settings.ssh_user, settings.ssh_password)
        elif args.command == "publish":
            release.publish(settings.output, REPO, pin=args.pin)
        elif args.command == "pin":
            release.pin_release(args.tag, REPO)
        elif args.command == "releases":
            pinned = (release.read_manifest(REPO) or {}).get("tag")
            for r in release.GitHub().list_releases():
                size = sum(a["size"] for a in r.get("assets", []))
                mark = "  <- pinned (map-release.json)" if r["tag_name"] == pinned else ""
                print(f"{r['tag_name']}  {r['name']}  {size / 1e6:.0f} MB{mark}")
        elif args.command == "fetch":
            print(fetch_published(settings, args.tag, args.dest, force=args.force))
        elif args.command == "render-prepare":
            if args.dry_run:
                print(render_work(settings)[0])
            else:
                render_prepare(settings, log=lambda m: print(m, file=sys.stderr))
        elif args.command == "render-plan":
            lines, zooms = render_plan_lines(settings, args.work, args.zoom)
            print(" ".join(map(str, zooms)) if args.zooms else "\n".join(lines))
        elif args.command == "render-tiles":
            def show(done, total, rate, z):
                print(f"  {done:,}/{total:,} tiles, {rate:.1f}/s, zoom {z}", flush=True)
            render_tiles(settings, args.server, args.work, progress=show)
        elif args.command == "usb":
            db = fetch_published(settings, args.release) if args.release else (args.db or settings.output)
            copy_to_usb(db, args.dir)
        elif args.command == "update-image":
            update_image(args.db or settings.output, args.yocto or settings.yocto_dir,
                         args.machine or settings.machine, build=not args.no_build)
    except (PushError, schema.InvalidMapDatabase, release.ReleaseError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
