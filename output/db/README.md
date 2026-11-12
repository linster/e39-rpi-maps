# `output/db`: the map database

This directory holds the pre-populated OSM database that the Yocto image
ships. It is **not** part of the Django source tree (`src/`).

## Why Git LFS

The database is far too large for a normal git object, and there is no
artifact server. Git LFS keeps the blob out of the repository history while
still letting the Yocto build pull it from the same commit as the code.

The patterns in the repository root `.gitattributes` send these files to LFS:

```
output/db/*.sqlite
output/db/*.sqlite3
output/db/*.sqlite.zst
output/db/*.sqlite3.zst
output/db/*.pbf
output/db/*.osm.pbf
```

Install the client once per machine (`git lfs install`), then add files
normally; `git lfs ls-files` should list them.

## Layout expected by the recipes

```
output/db/
├── e39maps-osm.sqlite        # the SQLite map database, built by the toolkit (LFS)
├── ATTRIBUTION.txt           # ODbL notice (committed normally)
└── README.md                 # this file
```

`e39maps-data.bb` in `meta-e39rpi-maps` (e39-rpi-yocto) packages
`e39maps-osm.sqlite` and `ATTRIBUTION.txt` from this directory as they are;
the backend opens the database read-only in place on the device. A changed
file rebuilds the package.

Without a database the package is empty (the build warns) and the backend
runs without map data. A Git LFS pointer file (no `git lfs pull`) stops the
build. Over 2 GB: keep the files on the build machine and set
`E39_MAPS_DATA_DIR` in the Yocto build's `local.conf` instead of using LFS.

## Building the database

With the map toolkit, on the laptop, never on the Pi:
`toolkit/map-toolkit.sh` (window) or `toolkit/map-toolkit.sh build` writes
`e39maps-osm.sqlite` here (see the main README, "Map toolkit"). The format
is `src/mapdata/schema.py`; the toolkit refuses to overwrite a file here
that isn't one of its map databases.

## Licensing and attribution

The database is derived from OpenStreetMap and is licensed under the
**Open Database License (ODbL) 1.0** — not the MPL-2.0 that covers the
Django code. `ATTRIBUTION.txt` must stay next to the database and is
installed into `/usr/share/e39rpi-maps/data/` by the data package.

## Updating the database

Replace the file, commit it, and bump the parent `e39-rpi-yocto` submodule
pointer: the next image contains the new database (or use the toolkit's
"Update image", which puts it straight into the Yocto build). On a running
device the toolkit's live swap, or a USB stick with an `RPIMAPS` folder,
replaces just this file; the state database, with users and user POIs, is
never touched.

## Quota warning

GitHub's free LFS quota (1 GB storage and 1 GB/month bandwidth) is small.
A regional extract can exceed it quickly, and every build host and CI run
that clones the submodule consumes bandwidth. Check
`git lfs ls-files -s` before committing, keep only one database file, and
consider a smaller extract if the quota becomes a problem.
