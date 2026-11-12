# Yocto contract for `e39-rpi-maps`

How the e39-rpi image builds and runs this app, and what the app must keep
stable for that. The recipes themselves are in `e39-rpi-yocto`,
`layers/meta-e39rpi-maps` (see its `README.md`; branch `claude-maps`); this
repository is the `e39-rpi-maps` git submodule there, built with
`externalsrc` (uncommitted edits included).

When the app's runtime shape changes (new settings, paths, processes,
dependencies, data files), update this file **and** the recipes.

## What the image provides

| Item | Value |
| --- | --- |
| Yocto | scarthgap, Python 3.12 |
| Django stack (meta-python) | Django 5.0.11, asgiref 3.8.1, sqlparse 0.4.4, WhiteNoise 6.6.0, gunicorn 21.2.0; `src/requirements/` pins the same |
| gRPC (meta-python) | grpcio 1.62.2, protobuf 4.25.3; the generated code in `src/e39maps_api` must come from `grpcio-tools` 1.62.2 (`src/requirements/dev.txt`) |
| Extra stdlib packages | `python3-sqlite3`, `python3-zoneinfo`, `python3-statistics`, `tzdata-core` (UTC only) |
| Not available | pip/venv on the device, GDAL (GeoDjango), PostGIS |

New Python dependencies need a recipe (meta-python or our layer) and an
`RDEPENDS` entry in `e39maps.bb`. Standard-library modules are split into
packages in Yocto; a newly imported one may need adding too.

## What the app must keep

| Item | Value |
| --- | --- |
| Installed files | `src/manage.py` and every top-level package in `src/` (a directory with `__init__.py`: `e39maps`, `core`, `mapdata`, `pois`, `api`, `e39maps_api`, ...) |
| WSGI entry | `e39maps.wsgi:application`, run from the app directory |
| Management | `python3 manage.py migrate --noinput`, `collectstatic --noinput --clear`, `run_grpc_server` (runs until SIGTERM) |
| Settings | environment variables only (below) |
| Map data | `output/db/e39maps-osm.sqlite` (Git LFS) + `output/db/ATTRIBUTION.txt` |

Environment on the device (`/etc/default/e39maps`):

| Variable | Device value |
| --- | --- |
| `DJANGO_DEBUG` | `0` |
| `DJANGO_SECRET_KEY_FILE` | `/var/lib/e39maps/secret_key` (generated on first boot, 0600) |
| `DJANGO_ALLOWED_HOSTS` | `*` (localhost for the HMI, any address on the car's network) |
| `DJANGO_STATE_DB_PATH` | `/var/lib/e39maps/state.sqlite3` (writable) |
| `DJANGO_MAP_DB_PATH` | `/usr/share/e39rpi-maps/data/e39maps-osm.sqlite` (read-only, immutable) |
| `DJANGO_STATIC_ROOT` | `/var/lib/e39maps/static` |
| `DJANGO_DEV_ADMIN` | `admin:e39rpi`, development images only (`/etc/default/e39maps-dev-admin`) |
| `DJANGO_GRPC_BIND` | `0.0.0.0:50051` (default) |

## How it runs

- **User** `e39maps`; code read-only in `/usr/share/e39rpi-maps/app`
  (byte-compiled at build time), state in `/var/lib/e39maps`.
- **`e39maps-init.service`** (oneshot): secret key; `migrate` and
  `collectstatic` only when the app changed (`BUILD_ID`, a hash of the
  installed files) or their results are missing.
- **`e39maps.service`**: gunicorn on `0.0.0.0:8080`, 1 worker, 2 threads,
  `MemoryMax=192M`, `ProtectSystem=strict` (no writes outside
  `/var/lib/e39maps`). Port 8000 is MPD's.
- **`e39maps-grpc.service`**: `manage.py run_grpc_server` on `0.0.0.0:50051`
  (POIs, routes for the HMI), same user, settings and hardening, its own
  `MemoryMax`, after `e39maps-init`.
- **Map data** is a separate package (`e39maps-data`, ODbL). A map update
  replaces only that file; the state database keeps users and user POIs.
  Data over 2 GB: build machine path (`E39_MAPS_DATA_DIR`) instead of LFS.

## Rules

- Never write to the map database; it is opened `mode=ro&immutable=1`.
- No relations between map models and state models (two databases).
- No file logging: stdout/stderr go to the journal.
- Heavy work (OSM import, routing tiles, tile rendering) happens off-device,
  in the map toolkit, never on the Pi.

## Still to come

Valhalla (Python bindings, routing tiles from the toolkit; then
`RouteService`); pre-rendered tiles over HTTP; the
map toolkit (build, push to QEMU/device over ssh); the map database timestamp
in the HMI's `version.conf`.
