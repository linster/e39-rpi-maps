# `output/db`: the map database

This directory holds the pre-populated OSM database that the Yocto image
ships. It is **not** part of the Django source tree (`src/`).

## Not committed

Maps are published as GitHub releases and pinned by `map-release.json` in
the repository root (see [`../../map_updates.md`](../../map_updates.md));
this directory is the local working copy, git-ignored apart from this file
and `ATTRIBUTION.txt`. A build machine fills it with the pinned map:
`scripts/fetch-map.py` (or e39-rpi-yocto's `scripts/fetch-map.sh`).

## Layout expected by the recipes

```
output/db/
├── e39maps-osm.sqlite        # the map database (toolkit build, or fetched)
├── e39maps-osm.routing.tar   # its routing tiles (named in the database)
├── ATTRIBUTION.txt           # ODbL notice (committed normally)
└── README.md                 # this file
```

`e39maps-data.bb` in `meta-e39rpi-maps` (e39-rpi-yocto) packages
`e39maps-osm.sqlite` and `ATTRIBUTION.txt` from this directory as they are;
the backend opens the database read-only in place on the device. A changed
file rebuilds the package.

Without a database the package is empty (the build warns) and the backend
runs without map data. The build also says whether the map here is the
published one `map-release.json` pins, or a locally built one. Elsewhere on
the build machine: set `E39_MAPS_DATA_DIR` in the Yocto build's
`local.conf`.

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
