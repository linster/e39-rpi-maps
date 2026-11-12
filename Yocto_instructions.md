# Yocto instructions for `e39-rpi-maps`

Audience: the engineer/agent writing the BitBake recipe and image integration.
This file is the contract between the Django app and the Yocto image. Read it
before writing the recipe; update it when the app's runtime shape changes.

Last updated against commit
`370f62cd21b7f31acaf9d053dfe3240e09ab9a91` (the hello-world app commit).

---

## 0. TL;DR

- Put the recipe in a new `meta-e39` layer.
- Fetch this repo with `gitsm://` and a **pinned full `SRCREV`**; do not use
  `AUTOREV`, and do not build from a git submodule checked into a layer.
- Do **not** run `pip`, create a venv, or use a wheel on the target. The project
  has no `pyproject.toml`; install the source tree to `/usr/share/e39maps` and
  satisfy dependencies with OE packages.
- Install two systemd units:
  - `e39maps-init.service` (oneshot): generate secret, `migrate`,
    `collectstatic`.
  - `e39maps.service`: gunicorn serving `e39maps.wsgi:application` on port 8000.
- Keep all writable state in `/var/lib/e39maps` (via `StateDirectory`); the
  rootfs may be read-only.
- Verify: `/healthz` returns `{"status": "ok"}`, `/` renders "Hello from
  Django", `/admin/` returns 302, and the hashed CSS returns 200.

---

## 1. Repository facts

| Item | Value |
| --- | --- |
| Git URL | `https://github.com/linster/e39-rpi-maps.git` |
| Branch | `main` |
| License | MPL-2.0 (`LICENSE`, md5 `f75d2927d3c1ed2414ef72048f5ad640`) |
| Python | Requires >= 3.10; developed/tested on 3.12.3 |
| Django | 5.2.19 LTS (supports Python 3.10–3.13) |
| WSGI entry point | `e39maps.wsgi:application` |
| ASGI entry point | `e39maps.asgi:application` (unused so far) |
| Management entry | `manage.py` (must be run with cwd = project root) |
| Runtime deps | `Django`, `whitenoise`; production adds `gunicorn` |
| Packaging | No `pyproject.toml`/`setup.py`; it is a Django *project*, not a wheel |

Project layout (only these paths are needed at runtime):

```
manage.py
e39maps/            settings.py, urls.py, wsgi.py, asgi.py
core/               app: views, urls, templates, static, migrations
requirements/       documentation/reference only (OE supplies the packages)
LICENSE
```

Everything else (`.venv`, `db.sqlite3`, `staticfiles/`, `.git`) must be
excluded from the package.

### Runtime contract

The app reads all configuration from environment variables (see
`.env.example`). Relevant to the recipe:

| Variable | Recipe/runtime value |
| --- | --- |
| `DJANGO_DEBUG` | `0` |
| `DJANGO_SECRET_KEY_FILE` | `/var/lib/e39maps/secret_key` |
| `DJANGO_ALLOWED_HOSTS` | device hostname/IP, e.g. `e39.local,localhost,127.0.0.1` |
| `DJANGO_DB_PATH` | `/var/lib/e39maps/db.sqlite3` |
| `DJANGO_STATIC_ROOT` | `/var/lib/e39maps/static` |
| `DJANGO_HTTPS` | `0` unless TLS is terminated in front of gunicorn |
| `DJANGO_BEHIND_PROXY` | `0` unless a reverse proxy sets `X-Forwarded-Proto` |

`DJANGO_DEBUG=0` without a secret key raises `ImproperlyConfigured` at startup.
The app can read the key from `DJANGO_SECRET_KEY_FILE`, which is what the image
should use so the key never lives in the read-only rootfs or in git.

`DJANGO_DEBUG=0` also switches static file handling to WhiteNoise's
`CompressedManifestStaticFilesStorage`. **The manifest must exist before the
web service starts**, otherwise rendering `{% static %}` raises `ValueError`
and the index page returns 500. This is why `collectstatic` runs in the init
unit, ordered before the web unit.

---

## 2. Ask the human before guessing

If these are unknown, ask; they change the recipe:

1. **Yocto release** (Scarthgap 5.0, Styhead 5.1, Walnascar 5.2, Whinlatter 5.3,
   …). This determines Python version, `LAYERSERIES_COMPAT`, and whether
   `SRCPV` still exists. If forced to assume, use Scarthgap and Python 3.12.
2. **Pi model and RAM** (Pi Zero 2 W 512 MB vs Pi 4/5 1–8 GB). Affects
   `MemoryMax` and whether SpatiaLite/PostGIS is viable.
3. **Image name and hostname** (for `ALLOWED_HOSTS` and documentation).
4. **Read-only rootfs?** If yes, the `/var/lib/e39maps` split below is mandatory.
5. **Where the OSM data artifact will come from** (release asset vs separate
   recipe). Do not put a multi-GB extract in this git repo.
6. **gRPC implementation** (`grpcio` vs `grpclib`). This is not in the code yet;
   do not add gRPC dependencies until the service exists.

---

## 3. Layer layout

```
meta-e39/
├── conf/
│   └── layer.conf
└── recipes-apps/
    └── e39maps/
        ├── e39maps_git.bb
        └── files/
            ├── e39maps.env
            ├── e39maps.service
            └── e39maps-init.service
```

`conf/layer.conf` (adjust compat to the target release):

```bitbake
BBPATH .= ":${LAYERDIR}"
BBFILES += "${LAYERDIR}/recipes-*/*/*.bb ${LAYERDIR}/recipes-*/*/*.bbappend"

BBFILE_COLLECTIONS += "meta-e39"
BBFILE_PATTERN_meta-e39 = "^${LAYERDIR}/"
BBFILE_PRIORITY_meta-e39 = "10"

LAYERSERIES_COMPAT_meta-e39 = "scarthgap styhead walnascar"
```

Add the layer to `bblayers.conf`, then add `e39maps` to the image with
`IMAGE_INSTALL:append = " e39maps"`.

---

## 4. Recipe skeleton

```bitbake
SUMMARY = "Django/GeoDjango backend for e39-rpi-maps"
HOMEPAGE = "https://github.com/linster/e39-rpi-maps"
LICENSE = "MPL-2.0"
LIC_FILES_CHKSUM = "file://LICENSE;md5=f75d2927d3c1ed2414ef72048f5ad640"

SRC_URI = "gitsm://github.com/linster/e39-rpi-maps.git;branch=main;protocol=https \
           file://e39maps.env \
           file://e39maps.service \
           file://e39maps-init.service"
# Full 40-char SHA. Bump deliberately when the app changes; never AUTOREV.
SRCREV = "370f62cd21b7f31acaf9d053dfe3240e09ab9a91"

# On releases where SRCPV still exists you may use "0.1.0+git${SRCPV}".
# On newer releases SRCPV is gone; "0.1.0+git" is fine.
PV = "0.1.0+git"
S = "${WORKDIR}/git"

inherit systemd useradd

USERADD_PACKAGES = "${PN}"
USERADD_PARAM:${PN} = "--system --home /var/lib/e39maps \
    --shell /sbin/nologin --user-group e39maps"

do_install() {
    install -d ${D}${datadir}/e39maps
    cp -R --no-preserve=ownership \
        ${S}/manage.py ${S}/e39maps ${S}/core \
        ${D}${datadir}/e39maps/

    # Never ship local state or build artifacts.
    find ${D}${datadir}/e39maps -type d -name '__pycache__' -prune -exec rm -rf {} +
    find ${D}${datadir}/e39maps -name '*.py[co]' -delete
    rm -rf ${D}${datadir}/e39maps/db.sqlite3* \
           ${D}${datadir}/e39maps/staticfiles

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/e39maps.service ${D}${systemd_system_unitdir}/
    install -m 0644 ${WORKDIR}/e39maps-init.service ${D}${systemd_system_unitdir}/

    install -d ${D}${sysconfdir}
    install -m 0644 ${WORKDIR}/e39maps.env ${D}${sysconfdir}/e39maps.env

    install -Dm0644 ${S}/.env.example ${D}${docdir}/${PN}/env.example
}

FILES:${PN} += " \
    ${datadir}/e39maps \
    ${systemd_system_unitdir}/e39maps.service \
    ${systemd_system_unitdir}/e39maps-init.service \
    ${sysconfdir}/e39maps.env \
"
CONFFILES:${PN} += "${sysconfdir}/e39maps.env"

SYSTEMD_SERVICE:${PN} = "e39maps-init.service e39maps.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

# Verify exact Python module package names for the target release; OE splits
# modules differently across releases. These are the expected providers.
RDEPENDS:${PN} += " \
    python3-core \
    python3-json \
    python3-logging \
    python3-sqlite3 \
    python3-django \
    python3-asgiref \
    python3-sqlparse \
    python3-whitenoise \
    python3-gunicorn \
    python3-packaging \
    tzdata \
"
```

Notes:

- `gitsm://` is correct even though the repo has no submodules today; it keeps
  working if any are added later. Do not use `externalsrc` for production
  builds.
- If `python3-whitenoise` or `python3-gunicorn` is missing from the target
  release's `meta-python`, add small `python_pep517` recipes. Use the PyPI
  sdist and **real** `sha256sum` values; do not invent checksums.
- If `meta-python` ships an older Django, add
  `python3-django_5.2.19.bb` with `inherit python_pep517 python_setuptools_build_meta`
  and `RDEPENDS:${PN} += "python3-asgiref python3-sqlparse"`. Django 5.2 needs
  Python >= 3.10, so a Kirkstone (3.10) image is technically acceptable; check
  that the packaged version is actually 5.2.x.
- `python3-sqlite3` is the module package on most releases, but confirm with
  `oe-pkgdata-util find-path '*/sqlite3/*.so'`. The same applies to
  `python3-json`/`python3-logging`; if they are folded into `python3-misc` on
  your release, depend on that instead.

---

## 5. Configuration file

`files/e39maps.env` (installed to `/etc/e39maps.env`):

```sh
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=e39.local,localhost,127.0.0.1
DJANGO_DB_PATH=/var/lib/e39maps/db.sqlite3
DJANGO_STATIC_ROOT=/var/lib/e39maps/static
DJANGO_SECRET_KEY_FILE=/var/lib/e39maps/secret_key
DJANGO_HTTPS=0
DJANGO_BEHIND_PROXY=0
```

`ALLOWED_HOSTS` must include whatever name/IP clients use, or Django returns
HTTP 400. Tell the user to edit this file on the device if the hostname differs.

---

## 6. systemd units

### `e39maps-init.service`

Runs once per boot (cheap; idempotent) and before the web service. `migrate`
and `collectstatic` are both idempotent.

```ini
[Unit]
Description=e39-rpi-maps first-boot initialization
After=local-fs.target
Before=e39maps.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=e39maps
Group=e39maps
StateDirectory=e39maps
StateDirectoryMode=0750
EnvironmentFile=/etc/e39maps.env
WorkingDirectory=/usr/share/e39maps

# Generate the secret once, mode 0600, on persistent storage.
ExecStartPre=/bin/sh -c 'test -s /var/lib/e39maps/secret_key || { umask 077; python3 -c "import secrets; print(secrets.token_urlsafe(50))" > /var/lib/e39maps/secret_key; }'

ExecStart=/usr/bin/python3 manage.py migrate --noinput
ExecStart=/usr/bin/python3 manage.py collectstatic --noinput

[Install]
WantedBy=multi-user.target
```

`StateDirectory=e39maps` creates `/var/lib/e39maps` owned by `e39maps` before
`ExecStartPre`, so this works even with a read-only rootfs.

### `e39maps.service`

```ini
[Unit]
Description=e39-rpi-maps web (gunicorn)
Wants=network-online.target
After=network-online.target e39maps-init.service
Requires=e39maps-init.service

[Service]
User=e39maps
Group=e39maps
StateDirectory=e39maps
EnvironmentFile=/etc/e39maps.env
WorkingDirectory=/usr/share/e39maps

ExecStart=/usr/bin/gunicorn e39maps.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 1 --threads 2 \
    --max-requests 1000 --max-requests-jitter 100 \
    --access-logfile - --error-logfile -

Restart=on-failure
RestartSec=5
TimeoutStopSec=20

# Resource guard for a 512 MB–1 GB device. Tune after measuring.
MemoryMax=192M
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/e39maps

[Install]
WantedBy=multi-user.target
```

Notes:

- `Requires=` + `After=` on the init unit is deliberate: it prevents the
  manifest-missing 500 described in §1.
- `WorkingDirectory=/usr/share/e39maps` is what makes `e39maps.wsgi` and
  `core` importable; gunicorn inserts the cwd on `sys.path`.
- Verify the gunicorn path. If `python3-gunicorn` installs to `${bindir}` and
  that is `/usr/bin`, the unit is correct; otherwise adjust.
- Keep `--workers 1 --threads 2`. Each extra sync worker is a full Django
  process (tens of MB). Do not raise this without measuring on the target.
- Logs go to the journal. Do not add file logging on the SD card.
- If the rootfs is writable and you prefer not to use `ProtectSystem=strict`,
  keep the `StateDirectory` and `ReadWritePaths` anyway; it documents intent.

---

## 7. Static files: choose one strategy

The app uses `CompressedManifestStaticFilesStorage` when `DJANGO_DEBUG=0`.
Hashed filenames and `staticfiles.json` must exist at runtime.

**Recommended: first-boot `collectstatic` (already in the init unit).**

- Pros: no build-host Django needed; no version-matching headaches; works with
  read-only rootfs because output goes to `/var/lib/e39maps/static`.
- Cons: a few seconds on first boot; static output is not in the image until
  then. Acceptable on a Pi.
- The init unit is ordered before the web unit, so requests never race it.

**Alternative: build-time `collectstatic`.**

- Requires Django + WhiteNoise runnable on the **build host** (native sysroot),
  e.g. `python3-django-native` / `python3-whitenoise-native` recipes. OE does
  not provide these by default; creating them is extra work.
- Then run in `do_install` with `DJANGO_DEBUG=0`, a throwaway secret, and
  `DJANGO_STATIC_ROOT=${D}${datadir}/e39maps/static`, and set the runtime
  `DJANGO_STATIC_ROOT` to `/usr/share/e39maps/static`.
- Only do this if boot time becomes a measured problem. Do not use a host venv
  for it: that makes the build non-reproducible.

**Anti-pattern:** setting `DJANGO_DEBUG=1` in production to avoid the manifest.
It exposes debug pages and changes static handling. Don't.

---

## 8. Database and OSM data

Current state: one SQLite database for Django's own tables (auth, sessions,
admin) plus future app models. It is created by `migrate` in the init unit at
`/var/lib/e39maps/db.sqlite3` and uses WAL mode.

- Never package `db.sqlite3` from the source tree; it is gitignored and local.
- Migrations ship with the source tree; the init unit applies them.
- SD-card wear: WAL is fine for a small state DB. Keep logs off the card.
- Future GeoDjango: switch `DATABASES` to
  `django.contrib.gis.db.backends.spatialite`, and add
  `RDEPENDS:${PN} += "libspatialite geos proj gdal"` plus the Python GIS
  module. Validate with `SELECT spatialite_version();` and
  `python3 -c "from django.contrib.gis import gdal"` on the target.
- OSM dataset: keep it in a **separate recipe/artifact** (e.g.
  `e39maps-data`), not in this repo and not in this package. Import a regional
  Geofabrik extract on the x86 build host; never run `osm2pgsql`
  on the Pi.
- Prefer read-only/immutable access for the OSM database and keep only mutable
  state in `/var/lib/e39maps`. The current settings unconditionally set
  `journal_mode=WAL`; a read-only SpatiaLite dataset will need a second
  `DATABASES` alias or a settings tweak when that work lands. Do not force WAL
  onto a file on read-only media.
- Watch image size: a regional extract can be hundreds of MB. Put it on the
  data partition or ship it as a compressed artifact, not in the rootfs.

---

## 9. gRPC (not implemented yet)

The repository does not contain gRPC code yet. When it lands:

- Prefer `python3-grpcio` from `meta-python`. If absent, a custom recipe is a
  large C++ build; budget for it and use system libraries, e.g.
  `GRPC_PYTHON_BUILD_SYSTEM_OPENSSL=1`,
  `GRPC_PYTHON_BUILD_SYSTEM_ZLIB=1`,
  `GRPC_PYTHON_BUILD_WITH_CYTHON=1`. Build on the x86 host only.
- Generate `*_pb2.py` / `*_pb2_grpc.py` in CI and commit them. Do not run
  `grpcio-tools` on the device.
- Run the server as `e39maps-grpc.service` (separate process, port 50051, own
  `MemoryMax`, same `User`, `StateDirectory`, and `EnvironmentFile`). Add
  `Requires=e39maps-init.service` and `After=` it.
- `grpclib` (pure Python) is the fallback if `grpcio` packaging is too costly;
  benchmark before choosing it.
- Do not add these dependencies or the unit until the management command
  (e.g. `manage.py run_grpc_server`) actually exists.

---

## 10. Build and acceptance checks

Build:

```sh
bitbake e39maps
oe-pkgdata-util list-pkg-files -p e39maps
bitbake core-image-<image>
```

On the target (or `runqemu`):

```sh
systemctl status e39maps-init.service e39maps.service
journalctl -u e39maps-init.service -u e39maps.service --no-pager
curl -fsS http://127.0.0.1:8000/healthz          # {"status": "ok"}
curl -fsS http://127.0.0.1:8000/ | grep "Hello from Django"
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/admin/   # 302
ls -l /var/lib/e39maps/                          # db, secret_key, static/
test ! -e /usr/share/e39maps/db.sqlite3          # no local state packaged
test ! -d /usr/share/e39maps/.venv               # no venv packaged
systemctl show -p MemoryCurrent e39maps.service
```

Also verify:

- The CSS URL from the rendered page returns 200 and a hashed filename
  (e.g. `/static/core/site.<hash>.css`).
- `/var/lib/e39maps/secret_key` is mode `0600`.
- A reboot does not regenerate the secret key and does not fail the init unit.
- `DJANGO_DEBUG=0` and `DJANGO_ALLOWED_HOSTS` match how clients reach the box.

`python3 manage.py check --deploy` with `DJANGO_HTTPS=1` should be clean. With
plain HTTP (`DJANGO_HTTPS=0`) expect four warnings (`W004`, `W008`, `W012`,
`W016`); that is intentional for a LAN device without TLS. Do not enable HSTS
on plain HTTP.

---

## 11. Pitfalls checklist

- [ ] `gitsm://`, not `git://`, and a full pinned `SRCREV`.
- [ ] No `pip`, venv, or `pip bundle` on the target.
- [ ] `cp --no-preserve=ownership` (build runs as root; files must be root-owned).
- [ ] Remove `__pycache__`, `*.pyc`, `db.sqlite3*`, `staticfiles/` from `${D}`.
- [ ] `WorkingDirectory=/usr/share/e39maps` on both units.
- [ ] Init unit runs before web unit (`Requires` + `After`).
- [ ] Secret generated at first boot, never baked into the image.
- [ ] `DJANGO_ALLOWED_HOSTS` includes the device hostname/IP.
- [ ] Static output under `/var/lib/e39maps/static`, not the read-only rootfs.
- [ ] `MemoryMax` set; one gunicorn worker.
- [ ] Logs to journald, not files.
- [ ] Do not add gRPC/GIS dependencies before that code exists.
- [ ] Python module package names verified for the chosen Yocto release.

---

## 12. Useful commands

```sh
git -C /path/to/e39-rpi-maps rev-parse HEAD     # SRCREV to pin
md5sum /path/to/e39-rpi-maps/LICENSE            # LIC_FILES_CHKSUM
bitbake-layers show-recipes 'python3-*' | grep -E 'django|gunicorn|whitenoise|grpcio'
oe-pkgdata-util find-path '*/django/__init__.py'
oe-pkgdata-util list-pkg-files python3-django | head -40
bitbake -e e39maps | grep -E '^(RDEPENDS|FILES|SYSTEMD_SERVICE)'
devtool modify e39maps                          # local iteration only
```

---

## 13. Current limitations (do not paper over)

- No GeoDjango, no SpatiaLite/PostGIS, no OSM models or import yet.
- No gRPC service, generated stubs, or `run_grpc_server` command yet.
- No custom user model; admin uses Django's default.
- No CI, no `pyproject.toml`, no versioned release artifact yet. If the project
  becomes a wheel later, revisit the recipe and install into
  `${PYTHON_SITEPACKAGES_DIR}` instead of `${datadir}`; until then, source-tree
  installation is intentional and simplest.
