#!/usr/bin/env python3
"""Download the published map that map-release.json pins (or another
release) into a folder, checked by SHA-256. For build machines: plain
Python 3, no toolkit (no pyosmium, no Valhalla) needed.

  scripts/fetch-map.py                         the pinned map into output/db
  scripts/fetch-map.py --dest DIR              ... into DIR (e.g. E39_MAPS_DATA_DIR)
  scripts/fetch-map.py --tag map-...           another published map
  scripts/fetch-map.py --force                 replace a different map in DIR

Needs a GitHub login with access to the repository: gh auth login (or
GH_TOKEN). e39-rpi-yocto's scripts/fetch-map.sh calls this with the build's
map data folder.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mapdata import release, schema  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", default=str(ROOT / "output" / "db"))
    parser.add_argument("--tag", help="a published map (default: the one map-release.json pins)")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        if args.tag:
            release.fetch_release(args.tag, args.dest, force=args.force)
        else:
            manifest = release.read_manifest(ROOT)
            if manifest is None:
                raise release.ReleaseError(f"no {release.MANIFEST_NAME}: no map is published/pinned yet "
                                           "(the toolkit's Publish), or pass --tag")
            release.fetch(manifest, args.dest, force=args.force)
    except (release.ReleaseError, schema.InvalidMapDatabase, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
