"""e39-rpi map toolkit.

  toolkit/map-toolkit.sh                     the window (default)
  toolkit/map-toolkit.sh build [--output F]  download extracts, build the map database
  toolkit/map-toolkit.sh info [F]            show a map database's facts
  toolkit/map-toolkit.sh push [--target T]   live swap on QEMU (default) or host[:port]
  toolkit/map-toolkit.sh update-image [--machine M] [--no-build]
  toolkit/map-toolkit.sh publish            publish the local map as a GitHub release
  toolkit/map-toolkit.sh releases           list the published maps
  toolkit/map-toolkit.sh fetch [TAG]        download a published map (default: the pinned one)
  toolkit/map-toolkit.sh usb DIR            copy a map to a USB stick (DIR/RPIMAPS)

push and usb take --release TAG to use a published map (downloaded first)
instead of the local one. Logging in to GitHub: gh auth login.

Settings (region, categories, paths, targets) come from the window's saved
settings (~/.config/e39maps-toolkit/settings.json).
"""

import argparse
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
    return build(settings.region, settings.categories, out, paths, log=log, progress=status, cancelled=cancelled,
                 toolkit_version=toolkit_version(), routing=routing)


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
    sub.add_parser("publish")
    sub.add_parser("releases")
    p = sub.add_parser("fetch")
    p.add_argument("tag", nargs="?", help="default: the pinned map (map-release.json), else the newest")
    p.add_argument("--dest", help="folder (default: the toolkit's cache, published/<tag>)")
    p.add_argument("--force", action="store_true", help="replace a different map in --dest")
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
            release.publish(settings.output, REPO)
        elif args.command == "releases":
            pinned = (release.read_manifest(REPO) or {}).get("tag")
            for r in release.GitHub().list_releases():
                size = sum(a["size"] for a in r.get("assets", []))
                mark = "  <- pinned (map-release.json)" if r["tag_name"] == pinned else ""
                print(f"{r['tag_name']}  {r['name']}  {size / 1e6:.0f} MB{mark}")
        elif args.command == "fetch":
            print(fetch_published(settings, args.tag, args.dest, force=args.force))
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
