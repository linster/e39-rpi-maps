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

`e39maps-data_git.bb` in `meta-e39rpi-maps` looks for the first `*.sqlite` or
`*.sqlite3` in this directory, compresses it with `zstd` on the build host,
and writes a content-hash `VERSION` into the package. No `.zst` needs to be
committed: if you have a pre-compressed `*.sqlite.zst`, the recipe uses it
as-is.

If the directory contains no database, the recipe **skips** instead of
failing, so the app can still be built before the first import.

## Building the database

Do this on the x86 build host, never on the Pi. See
[`../../Yocto_instructions.md`](../../Yocto_instructions.md) §8 for the full
procedure. In short:

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
pointer. The recipe derives `VERSION` from the file content, so a changed
database is a changed package, and the first-boot installer replaces the
installed copy.

## Quota warning

GitHub's free LFS quota (1 GB storage and 1 GB/month bandwidth) is small.
A regional extract can exceed it quickly, and every build host and CI run
that clones the submodule consumes bandwidth. Check
`git lfs ls-files -s` before committing, keep only one database file, and
consider a smaller extract if the quota becomes a problem.
