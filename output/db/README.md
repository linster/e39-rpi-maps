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
├── e39maps-osm.sqlite        # the SpatiaLite/SQLite database (LFS)
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

Do this on the x86 build host, never on the Pi (the map toolkit will do it).
In short:

1. Import a regional Geofabrik `.osm.pbf` into SQLite/SpatiaLite using the
   same library versions as the image (`libspatialite` 5.0.1, GEOS 3.12.1,
   PROJ 9.3.1, SQLite 3.45.3).
2. Checkpoint and clean up:
   `PRAGMA wal_checkpoint(TRUNCATE); VACUUM; ANALYZE;`, then
   `PRAGMA integrity_check;` (must print `ok`).
3. Remove `-wal`/`-shm` files.
4. Copy the database here as `e39maps-osm.sqlite` and commit it with LFS.

## Licensing and attribution

The database is derived from OpenStreetMap and is licensed under the
**Open Database License (ODbL) 1.0** — not the MPL-2.0 that covers the
Django code. `ATTRIBUTION.txt` must stay next to the database and is
installed into `/usr/share/e39rpi-maps/data/` by the data package.

## Updating the database

Replace the file, commit it, and bump the parent `e39-rpi-yocto` submodule
pointer: the next image contains the new database. On a running device the
map toolkit's push (still to come) replaces just this file; the state
database, with users and user POIs, is never touched.

## Quota warning

GitHub's free LFS quota (1 GB storage and 1 GB/month bandwidth) is small.
A regional extract can exceed it quickly, and every build host and CI run
that clones the submodule consumes bandwidth. Check
`git lfs ls-files -s` before committing, keep only one database file, and
consider a smaller extract if the quota becomes a problem.
