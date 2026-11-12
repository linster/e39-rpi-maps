# e39-rpi-maps

Django + GeoDjango project to give routing and POIs a backend. Targets a
Raspberry Pi running Yocto Linux.

Current state: hello-world Django app with a styled page, a `/healthz`
endpoint, and a production-shaped settings file (env-driven config, SQLite
tuned for concurrent readers, WhiteNoise for static files).

## Quick start

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements/base.txt

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
manage.py
e39maps/            project package (settings, URLs, WSGI/ASGI entry points)
core/               hello-world app (view, template, static CSS, tests)
requirements/       base.txt (runtime), prod.txt (+ gunicorn)
.env.example        configuration reference
```

Future apps (`maps`, `routing`, `pois`, `api`) slot in next to `core`.

## Configuration

All configuration is read from the environment; see `.env.example`. The
important ones:

| Variable | Default | Notes |
| --- | --- | --- |
| `DJANGO_DEBUG` | `1` | Set to `0` in production. |
| `DJANGO_SECRET_KEY` | dev-only value | **Required** when `DJANGO_DEBUG=0`. |
| `DJANGO_SECRET_KEY_FILE` | unset | Read the secret key from this file instead of the environment. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | Comma-separated. |
| `DJANGO_DB_PATH` | `<project>/db.sqlite3` | Put writable state on persistent storage. |
| `DJANGO_STATIC_ROOT` | `<project>/staticfiles` | Writable location for `collectstatic`. |
| `DJANGO_HTTPS` | `0` | Set to `1` to enable HSTS and secure cookies. |

`DJANGO_DEBUG=0` also enables WhiteNoise's compressed, hashed static storage,
so run `collectstatic` as part of the build/install step.

## Running in production (on the device)

```sh
pip install -r requirements/prod.txt
export DJANGO_DEBUG=0
export DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
export DJANGO_ALLOWED_HOSTS=e39.local
python manage.py migrate
python manage.py collectstatic --noinput
gunicorn e39maps.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 1 --threads 2 \
  --max-requests 1000 --max-requests-jitter 100 \
  --access-logfile - --error-logfile -
```

`--workers 1 --threads 2` is deliberate: Django's sync workers are processes,
and each one costs tens of MB of RSS. Tune only after measuring on the target.

---

## Packaging plan for the Raspberry Pi / Yocto

The guiding constraint is that the Pi has little RAM (512 MB–2 GB), slow
storage (SD card), and limited CPU. Everything below is chosen to avoid
running heavy services and to avoid doing expensive work on the device.

### 1. How the recipe gets the code

Keep this repository standalone and let the recipe fetch it; don't rely on a
git submodule inside a layer for the actual build (submodules are awkward for
BitBake reproducibility, and `externalsrc` should stay a dev-only shortcut).

```bitbake
SRC_URI = "gitsm://github.com/<you>/e39-rpi-maps.git;branch=main;protocol=https"
SRCREV = "<commit sha>"
```

`gitsm://` recursively fetches this repo's own submodules if/when we add any.
Pin `SRCREV`; never build a moving branch. For local iteration, a
`devtool modify e39-rpi-maps` workflow beats committing a submodule pointer.

The project does not need to become a PyPI package. A simple recipe can copy
the source tree to `${datadir}/e39maps` and depend on OE-provided Python
packages. If we later want an installable wheel, add a `pyproject.toml` and
use the PEP 517 / `python_setuptools_build_meta` classes.

### 2. Python dependencies: use OE recipes, not pip

Do not create a virtualenv or run `pip` on the target. Options, in order of
preference:

1. **OE packages** (`meta-python`): `python3-django`, `python3-gunicorn`,
   `python3-whitenoise` (if present). Zero extra build cost, but versions lag
   upstream — pin the Django LTS that matches the image's Python (e.g. Python
   3.12 + Django 5.2 LTS).
2. **Custom recipes** for anything missing, using `python_pep517` /
   `setuptools3` and the upstream sdist. Cross-compiled wheels are the
   exception, not the rule; pure-Python packages build in seconds.
3. **`pip bundle`/vendored wheels**: last resort. It bypasses OE's dependency
   tracking and licensing metadata.

`RDEPENDS:${PN}` should list the runtime packages explicitly so the image
size and license manifest stay accurate.

### 3. Static files and the web server

- Run `collectstatic` at **image build time** in `do_install`, writing to
  `${D}${datadir}/e39maps/static`, then set `DJANGO_STATIC_ROOT` at runtime.
  Hashed filenames are generated once on the build host, not on the Pi.
- Serve static files with **WhiteNoise from the gunicorn process**. That
  avoids nginx: one less process, roughly 5–15 MB of RAM saved, and no
  separate config to maintain. If we later need TLS or caching, put a small
  reverse proxy in front, but don't add it by default.
- The root filesystem can be read-only. Keep the SQLite DB, WAL files and any
  uploads in `/var/lib/e39maps` (a `systemd` `StateDirectory`), not next to the
  code.

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
  database (or a compressed artifact unpacked on first boot) as part of the
  image. Importing on a Pi is slow and can exhaust RAM.
- If the dataset is read-only, open it with SQLite `immutable=1` / read-only
  mode and put only the mutable admin/state DB in `/var/lib`. Avoid WAL churn
  on the SD card for data that never changes.
- Keep the prebuilt DB out of the git repo; publish it as a release artifact
  or a separate BitBake `SRC_URI` with its own checksum.

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
# /lib/systemd/system/e39maps.service (sketch)
[Service]
User=e39maps
StateDirectory=e39maps
Environment=DJANGO_DEBUG=0
Environment=DJANGO_DB_PATH=/var/lib/e39maps/db.sqlite3
Environment=DJANGO_STATIC_ROOT=/usr/share/e39maps/static
ExecStart=/usr/bin/gunicorn e39maps.wsgi:application \
  --bind 0.0.0.0:8000 --workers 1 --threads 2 \
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
5. Write the BitBake recipe + systemd units and test on the Pi.
