"""e39-rpi map toolkit.

  toolkit/map-toolkit.sh                     the window (default)
  toolkit/map-toolkit.sh build [--output F]  download extracts, build the map database
  toolkit/map-toolkit.sh info [F]            show a map database's facts
  toolkit/map-toolkit.sh push [--target T]   live swap on QEMU (default) or host[:port]
  toolkit/map-toolkit.sh update-image [--machine M] [--no-build]

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
from .push import PushError, live_swap, update_image


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
    p = sub.add_parser("update-image")
    p.add_argument("--machine", choices=MACHINES)
    p.add_argument("--yocto")
    p.add_argument("--db")
    p.add_argument("--no-build", action="store_true", help="only install into the build's map data")
    args = parser.parse_args(argv)
    settings = Settings.load()

    try:
        if args.command in (None, "gui"):
            from .gui import run
            run(settings)
        elif args.command == "build":
            run_build(settings, args.output)
        elif args.command == "info":
            print(describe(args.db or settings.output))
        elif args.command == "push":
            live_swap(args.db or settings.output, args.target or settings.target,
                      settings.ssh_user, settings.ssh_password)
        elif args.command == "update-image":
            update_image(args.db or settings.output, args.yocto or settings.yocto_dir,
                         args.machine or settings.machine, build=not args.no_build)
    except (PushError, schema.InvalidMapDatabase, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
