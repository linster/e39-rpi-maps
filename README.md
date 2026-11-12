# e39-rpi-maps

Django + GeoDjango project to give routing and POIs a backend. Targets a
Raspberry Pi running Yocto Linux.

Current state: hello-world Django app with a styled page, a `/healthz`
endpoint, and a production-shaped settings file (env-driven config, SQLite
tuned for concurrent readers, WhiteNoise for static files), on **Django
5.0.11** (the Yocto image's version) with **two databases**: read-only map
data and writable state (see "Databases").

The HMI will talk to this backend over **gRPC** for POIs and routes (routing:
**Valhalla** through its Python bindings); map tiles stay plain HTTP.

## Quick start

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r src/requirements/base.txt

cd src
python manage.py migrate
python manage.py runserver
```

- Page: <http://127.0.0.1:8000/>
- Health probe: <http://127.0.0.1:8000/healthz>
- Admin: <http://127.0.0.1:8000/admin/> (create a user with
  `python manage.py createsuperuser`)

Run the tests with:

```sh
python manage.py test
```

## Layout

```
src/
├── manage.py
├── e39maps/        project package (settings, URLs, WSGI/ASGI entry points,
│                   database router, SQLite connection setup)
├── core/           hello-world app (view, template, static CSS, tests)
├── mapdata/        models of the read-only map database (unmanaged)
├── requirements/   base.txt (runtime), prod.txt (+ gunicorn)
└── .env.example    configuration reference
output/db/          pre-populated map database (Git LFS) + ODbL attribution
```

The split matters for the Yocto build: the recipe installs `src/` as the
application and ships `output/db/` as a separate `e39maps-data` package, so a
large database does not rebuild the code (and vice versa).

Future apps (`maps`, `routing`, `pois`, `api`) slot in next to `core`.

The database in `output/db/` is stored with **Git LFS** (there is no artifact
server); see [`output/db/README.md`](output/db/README.md). Run `git lfs install`
once per machine and `git lfs pull` after cloning.

## Configuration

All configuration is read from the environment; see `.env.example`. The
important ones:

| Variable | Default | Notes |
| --- | --- | --- |
| `DJANGO_DEBUG` | `1` | Set to `0` in production. |
| `DJANGO_SECRET_KEY` | dev-only value | **Required** when `DJANGO_DEBUG=0`. |
| `DJANGO_SECRET_KEY_FILE` | unset | Read the secret key from this file instead of the environment. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | Comma-separated. |
| `DJANGO_STATE_DB_PATH` | `src/db.sqlite3` | Writable state database; put it on persistent storage. (`DJANGO_DB_PATH`: old name, still read.) |
| `DJANGO_MAP_DB_PATH` | `output/db/e39maps-osm.sqlite` | Read-only map database, opened in place. |
| `DJANGO_STATIC_ROOT` | `src/staticfiles` | Writable location for `collectstatic`. |
| `DJANGO_HTTPS` | `0` | Set to `1` to enable HSTS and secure cookies. |

`DJANGO_DEBUG=0` also enables WhiteNoise's compressed, hashed static storage,
so run `collectstatic` as part of the build/install step.

## Databases

Two SQLite databases (`e39maps/settings.py`, `e39maps/routers.py`):

| Alias | What | Lifetime |
| --- | --- | --- |
| `default` (**state**) | users, admin, sessions, user-made POIs | writable; lives in `/var/lib/e39maps` on the device and **survives map updates** |
| `mapdata` (**map data**) | everything built from OSM by the map toolkit | read-only; opened in place as `file:...?mode=ro&immutable=1` (no writes, no `-wal`/`-shm` files), **replaced as a whole** on a map update |

- Models of the `mapdata` app read from the map database. They're unmanaged:
  the toolkit owns that schema, Django never migrates or writes it (a write
  fails with "attempt to write a readonly database"). Relations across the two
  databases aren't allowed; refer to map objects by id.
- Every map database has a `e39maps_meta (key, value)` table with facts like
  `schema_version`, `built_at` (ISO 8601, UTC) and `region`; `/healthz`
  reports them (or why the map database can't be read: the app runs without
  one).
- Django 5.0 has no SQLite `OPTIONS["init_command"]` (5.1+), so WAL,
  `synchronous=NORMAL` and foreign keys are set per connection in
  `e39maps/sqlite.py`, on the state database only.

## Running in production (on the device)

```sh
pip install -r src/requirements/prod.txt
export DJANGO_DEBUG=0
export DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
export DJANGO_ALLOWED_HOSTS=e39.local
cd src
python manage.py migrate
python manage.py collectstatic --noinput
gunicorn e39maps.wsgi:application \
  --bind 0.0.0.0:8080 \
  --workers 1 --threads 2 \
  --max-requests 1000 --max-requests-jitter 100 \
  --access-logfile - --error-logfile -
```

`--workers 1 --threads 2` is deliberate: Django's sync workers are processes,
and each one costs tens of MB of RSS. Tune only after measuring on the target.

---

## Packaging plan for the Raspberry Pi / Yocto

> The concrete recipe contract (submodule layout, unit files, dependency
> names, database provisioning, acceptance checks) lives in
> [`Yocto_instructions.md`](Yocto_instructions.md).

The guiding constraint is that the Pi has little RAM (512 MB–2 GB), slow
storage (SD card), and limited CPU. Everything below is chosen to avoid
running heavy services and to avoid doing expensive work on the device.

### 1. How the recipe gets the code

This repository is a **git submodule of `e39-rpi-yocto`**, and the recipe
builds it with `externalsrc` — the same pattern `e39rpi-hmi.bb` uses for the
`e39-rpi` HMI. Add it at the top of the Yocto repo:

```sh
cd ~/e39-rpi-yocto
git submodule add https://github.com/linster/e39-rpi-maps.git e39-rpi-maps
```

Then set `E39_MAPS_SRC_DEFAULT` in the maps layer's `layer.conf` to
`<yocto-root>/e39-rpi-maps`. The parent repo's commit pins the submodule
commit, so the image is reproducible; `externalsrc` also picks up uncommitted
edits, which is intended for development only. `devtool modify e39maps` works
for local iteration.

The project does not need to become a PyPI package. The recipe copies
`src/{manage.py,e39maps,core}` to `${datadir}/e39rpi-maps` and depends on
OE-provided Python packages. If we later want an installable wheel, add a
`pyproject.toml` and use the PEP 517 / `python_setuptools_build_meta` classes.

### 2. Python dependencies: use OE recipes, not pip

Do not create a virtualenv or run `pip` on the target. Options, in order of
preference:

1. **OE packages** (`meta-python`): `python3-django`, `python3-gunicorn`,
   `python3-whitenoise` (if present). Zero extra build cost, but versions lag
   upstream. The app is pinned to the image's versions (Django 5.0.11,
   asgiref 3.8.1, sqlparse 0.4.4, WhiteNoise 6.6.0, gunicorn 21.2.0 in
   scarthgap's meta-python; `src/requirements/`), so no extra recipes.
2. **Custom recipes** for anything missing, using `python_pep517` /
   `setuptools3` and the upstream sdist. Cross-compiled wheels are the
   exception, not the rule; pure-Python packages build in seconds.
3. **`pip bundle`/vendored wheels**: last resort. It bypasses OE's dependency
   tracking and licensing metadata.

`RDEPENDS:${PN}` should list the runtime packages explicitly so the image
size and license manifest stay accurate.

### 3. Static files and the web server

- Run `collectstatic` on first boot into `/var/lib/e39maps/static` (the init
  systemd unit). The image's `meta-python` has no native WhiteNoise, so
  build-time collection would need extra native recipes; first boot costs a
  few seconds once. Hashed filenames are generated then.
- Serve static files with **WhiteNoise from the gunicorn process**. That
  avoids nginx: one less process, roughly 5–15 MB of RAM saved, and no
  separate config to maintain. If we later need TLS or caching, put a small
  reverse proxy in front, but don't add it by default.
- The `e39-rpi` image's rootfs is writable ext4 and grows to fill the SD card
  on first boot, but state still belongs in `/var/lib/e39maps` (a `systemd`
  `StateDirectory`), not next to the code, so package upgrades and dataset
  updates don't touch it.

### 4. Database and OSM data

This is the biggest memory/space decision and should be made before importing
data:

| Option | RAM / disk | Fit for the Pi |
| --- | --- | --- |
| **SQLite + SpatiaLite** | Low; single file | Best for read-mostly OSM data. GeoDjango supports it (`django.contrib.gis.db.backends.spatialite`). Needs `libspatialite`, GEOS, PROJ, GDAL in the image. |
| **PostgreSQL + PostGIS** | High (server + shared buffers, typically 150–300 MB RSS) | Better concurrency and query features, but a lot of a 1 GB Pi before Django/gRPC even start. |

Recommendation: **SpatiaLite for the on-device dataset**, and only move to
PostGIS if we hit a feature that truly needs it.

Do the expensive work off-device:

- Import a **regional OSM extract** (Geofabrik `.osm.pbf`), not the planet.
- Run `osm2pgsql`/import on the x86 build host, then ship the resulting
  database as a **separate `e39maps-data` package**: a zstd-compressed SQLite
  file with a checksum, installed read-only under
  `/usr/share/e39rpi-maps/data/`, and installed into `/var/lib/e39maps/` by a
  first-boot oneshot. Importing on a Pi is slow and can exhaust RAM.
- Build the artifact with the image's library versions (`libspatialite` 5.0.1,
  GEOS 3.12.1, PROJ 9.3.1) — a SpatiaLite file from a newer library may not
  open on the device.
- OSM-derived data is **ODbL-1.0**, not MPL-2.0; ship the attribution notice
  with the artifact.
- If the dataset is read-only, open it with SQLite `immutable=1` / read-only
  mode and put only the mutable admin/state DB in `/var/lib`. Avoid WAL churn
  on the SD card for data that never changes. (The first iteration can use one
  writable DB; see `Yocto_instructions.md` §8 for the split.)
- Keep the prebuilt DB in `output/db/`, tracked by **Git LFS**. The data
  recipe reads it from the submodule checkout, compresses it with host `zstd`,
  and derives a content-hash `VERSION`, so a changed database is a package
  upgrade. Build hosts need `git-lfs` and must smudge the submodule checkout
  (`git lfs pull`); `externalsrc` does not fetch LFS objects itself.

### 5. gRPC

- `grpcio` is a native extension with a large C++ build. Check whether
  `meta-python` provides a matching `python3-grpcio` first; otherwise a custom
  recipe is needed and it will dominate build time. Build it on the x86 build
  host, never on the Pi.
- A pure-Python server such as `grpclib` is dramatically easier to package but
  slower per request. For a handful of streaming clients on a Pi that may be an
  acceptable trade; benchmark before committing.
- Run the gRPC server as a **separate systemd service** from the WSGI app
  (e.g. a `manage.py run_grpc_server` management command). That isolates
  crashes and lets each service get its own memory limit. The alternative —
  embedding gRPC in the web process — saves one interpreter but couples
  failure modes.
- Long-lived streams pin threads/connections. Prefer a small number of
  streams and a bounded queue; don't size the server for "many clients".

### 6. Runtime shape on the image

```ini
# /usr/lib/systemd/system/e39maps.service (sketch)
[Service]
User=e39maps
StateDirectory=e39maps
Environment=DJANGO_DEBUG=0
Environment=DJANGO_STATE_DB_PATH=/var/lib/e39maps/state.sqlite3
Environment=DJANGO_MAP_DB_PATH=/usr/share/e39rpi-maps/data/e39maps-osm.sqlite
Environment=DJANGO_STATIC_ROOT=/var/lib/e39maps/static
Environment=DJANGO_SECRET_KEY_FILE=/var/lib/e39maps/secret_key
WorkingDirectory=/usr/share/e39rpi-maps
ExecStart=/usr/bin/gunicorn e39maps.wsgi:application \
  --bind 0.0.0.0:8080 --workers 1 --threads 2 \
  --max-requests 1000 --max-requests-jitter 100
MemoryMax=192M
Restart=on-failure
```

- Log to stdout/stderr and let journald handle rotation; no log files on the
  SD card.
- Set `MemoryMax=` (and optionally `CPUQuota=`) per service so a runaway
  import or stream can't take down the device.
- Run `python manage.py check --deploy` at build time to catch misconfiguration.
- Ship migrations in the package and run `manage.py migrate` from a systemd
  `ExecStartPre` (or an init-time oneshot) against the persistent DB.

### 7. Rough memory budget (idle, 1 GB Pi)

| Component | RSS |
| --- | --- |
| gunicorn + Django (1 worker) | ~50–70 MB |
| gRPC service (separate process) | ~40–60 MB |
| SQLite/SpatiaLite page cache | tunable, keep small (~16–32 MB) |
| systemd + base Yocto image | ~100–150 MB |

That leaves plenty of headroom on 1 GB and is tight but workable on a 512 MB
Pi Zero 2 W. PostGIS would consume most of the remaining budget, which is why
it's not the default.

### Roadmap

1. Hello world page and health endpoint. ✅
2. Add GeoDjango + SpatiaLite, a `places` model, admin registration.
3. Import a regional OSM extract on the build host.
4. Add the gRPC service for streaming POIs/routes.
5. Test the BitBake recipe + systemd units on the Pi.
