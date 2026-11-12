# Yocto instructions for `e39-rpi-maps`

Audience: the engineer/agent writing the BitBake recipe and image integration
for the `e39-rpi-yocto` repository. This file is the contract between the
Django app and the image. Update it when the app's runtime shape changes.

App commit this was written against:
`370f62cd21b7f31acaf9d053dfe3240e09ab9a91`.

Yocto facts checked against `~/e39-rpi-yocto`:

| Item | Value |
| --- | --- |
| Release | Scarthgap 5.0.7 (`poky` submodule), Python 3.12 |
| Distro | `e39rpi`, systemd, X11 + JWM |
| Images | `e39rpi-hmi-image` (extends `e39rpi-base-image`) |
| Machines | `raspberrypi4-64` (SD/WIC), `qemuarm64`, `qemux86-64` |
| Rootfs | ext4, **writable**, grown to fill the card on first boot by `e39rpi-growroot` |
| Rootfs slack | `IMAGE_ROOTFS_EXTRA_SPACE = "524288"` (512 MiB) |
| Maps layer | `layers/meta-e39rpi-maps`, collection `e39rpimaps`, currently empty |
| Source convention | git submodule at the top of `e39-rpi-yocto` + `externalsrc` (same as `e39-rpi` HMI) |

The rootfs is **not** read-only. The `/var/lib` split is still used for state
that must survive package upgrades, but you do not need to design around a
read-only `/`.

---

## 0. TL;DR

- Put the recipe in the existing `meta-e39rpi-maps` layer, not a new layer.
- Follow the project convention: add this repo as a git submodule of
  `e39-rpi-yocto` and build it with `externalsrc`, exactly like
  `e39rpi-hmi.bb` builds the `e39-rpi` submodule. Do not invent a `gitsm://`
  recipe unless the human asks for a standalone/release build.
- Install the app to `/usr/share/e39rpi-maps`; keep writable state in
  `/var/lib/e39maps` (systemd `StateDirectory`).
- Ship the pre-populated OSM database as a **separate `e39maps-data` package**
  (compressed artifact + checksum), installed under
  `/usr/share/e39rpi-maps/data/`. A first-boot oneshot installs it into
  `/var/lib/e39maps/` (or opens it read-only in place).
- Run Django as gunicorn on **port 8080** (8000 is MPD's HTTP stream).
- Three systemd units: `e39maps-data.service` → `e39maps-init.service`
  (`migrate` + `collectstatic`) → `e39maps.service` (gunicorn).
- Do not run `pip` or `osm2pgsql` on the device; `collectstatic` runs once on
  first boot. The DB artifact is built on the x86 host.

---

## 1. Repository facts

| Item | Value |
| --- | --- |
| Git URL | `https://github.com/linster/e39-rpi-maps.git` |
| Branch | `main` |
| License | MPL-2.0 (`LICENSE`, md5 `f75d2927d3c1ed2414ef72048f5ad640`) |
| Python | Requires >= 3.10; tested on 3.12.3 |
| Django | 5.2.19 LTS (supports Python 3.10–3.13) |
| WSGI entry point | `e39maps.wsgi:application` |
| Management entry | `manage.py` (run with cwd = project root) |
| Runtime deps | `Django`, `whitenoise`; production adds `gunicorn` |
| Packaging | No `pyproject.toml`/`setup.py`; a Django *project*, not a wheel |

Runtime paths:

```
/usr/share/e39rpi-maps/          app source (manage.py, e39maps/, core/)
/usr/share/e39rpi-maps/data/     read-only dataset + VERSION (e39maps-data)
/var/lib/e39maps/                writable state (StateDirectory)
/var/lib/e39maps/db.sqlite3      Django state DB (v1: the installed dataset)
/var/lib/e39maps/data/osm.sqlite split read-only OSM DB (later)
/var/lib/e39maps/static/         collectstatic output
/etc/e39maps.env                 non-secret configuration
```

### The app's configuration contract

All settings come from environment variables. Relevant to the recipe:

| Variable | Value on the device |
| --- | --- |
| `DJANGO_DEBUG` | `0` |
| `DJANGO_SECRET_KEY_FILE` | `/var/lib/e39maps/secret_key` |
| `DJANGO_ALLOWED_HOSTS` | device hostname/IP, e.g. `e39.local,localhost,127.0.0.1` |
| `DJANGO_DB_PATH` | `/var/lib/e39maps/db.sqlite3` |
| `DJANGO_STATIC_ROOT` | `/var/lib/e39maps/static` |
| `DJANGO_HTTPS` | `0` (no TLS in the current image) |
| `DJANGO_BEHIND_PROXY` | `0` |

`DJANGO_DEBUG=0` without a key raises `ImproperlyConfigured`. Use
`DJANGO_SECRET_KEY_FILE` so the key is generated on the device and never baked
into the image.

`DJANGO_DEBUG=0` also selects WhiteNoise's
`CompressedManifestStaticFilesStorage`. The manifest must exist before gunicorn
starts, or `{% static %}` raises `ValueError` and `/` returns 500. That is why
`collectstatic` runs in the init unit before the web unit.

---

## 2. What is already in the Yocto tree

Use these; do not duplicate them:

- `layers/meta-e39rpi-maps/conf/layer.conf` — collection `e39rpimaps`,
  depends `e39rpibase e39rpigui`, `LAYERSERIES_COMPAT = "scarthgap"`.
  It is already in `bblayers.conf`.
- `layers/meta-e39rpi-gui/recipes-hmi/e39rpi-hmi/e39rpi-hmi.bb` — the pattern
  to copy for `externalsrc`, `file://` units, `tmpfiles.d`, and systemd.
- `layers/meta-e39rpi-base/recipes-core/e39rpi-growroot/` — first-boot root
  partition growth. Order data installation after it.
- `layers/meta-e39rpi-base/recipes-core/hmi-user/` — existing `hmi` user.
  A dedicated `e39maps` system user is still recommended for the backend.
- `meta-openembedded/meta-python` has: Django **5.0.11**, gunicorn 21.2.0,
  whitenoise 6.6.0, grpcio 1.62.2, grpcio-tools 1.62.2.
- `meta-openembedded/meta-oe` has: libspatialite **5.0.1**, GEOS 3.12.1,
  PROJ 9.3.1. **GDAL is not present**; Django's `django.contrib.gis` app
  imports GDAL at startup, so plan a geospatial layer (or a GDAL recipe) when
  GeoDjango lands.
- `poky/meta/files/common-licenses/ODbL-1.0` exists (md5
  `498d26a501c10739d57fca1c9d80bbc2`).

### Django version mismatch (important)

`meta-python` ships Django 5.0.11, but the app uses
`DATABASES["default"]["OPTIONS"]["init_command"]`, which Django added in 5.1.
With 5.0.11 the SQLite backend passes `init_command` to `sqlite3.connect()` and
fails with `TypeError`.

Pick one:

1. **Recommended:** add `python3-django_5.2.19.bb` to `meta-e39rpi-maps` by
   copying `python3-django_5.0.11.bb`, changing `require python-django.inc`
   to `require recipes-devtools/python/python-django.inc` (the inc lives in
   meta-python), and updating the checksum. The upstream sdist is
   `https://files.pythonhosted.org/packages/02/29/a487a108caa76430c8b616aa90a6d09cf431306112c14093e6f0a7454e17/django-5.2.19.tar.gz`
   with sha256
   `ad03d8abc51dbff95ebf34b27123cf0c26a3b62db7ba0cd520783686362533d1`.
   `python-django.inc` already has `BBCLASSEXTEND = "native nativesdk"`.
2. Use 5.0.11 and change the app to apply PRAGMAs via the
   `connection_created` signal instead of `init_command`. Do not do this
   silently; it is an app change.

gunicorn 21.2.0 and whitenoise 6.6.0 are fine for the current code. If you
package gunicorn 27 instead, add `--no-control-socket` to the unit: its default
control socket is `/run/user/<uid>/gunicorn.ctl`, which a system user does not
have.

---

## 3. Add the source as a submodule

`e39-rpi-yocto` already vendors `e39-rpi` this way. Add this repo alongside it:

```sh
cd ~/e39-rpi-yocto
git submodule add https://github.com/linster/e39-rpi-maps.git e39-rpi-maps
git submodule update --init --recursive
```

Then in `layers/meta-e39rpi-maps/conf/layer.conf`:

```bitbake
# The Django app source: the e39-rpi-maps git submodule at the top of this repo.
# Override E39_MAPS_SRC in local.conf to build another checkout.
E39_MAPS_SRC_DEFAULT := "${@os.path.normpath(d.getVar('LAYERDIR') + '/../../e39-rpi-maps')}"

# The recipes need meta-python (Django/gunicorn/whitenoise) and meta-oe
# (libspatialite/GEOS/PROJ).
LAYERDEPENDS_e39rpimaps = "e39rpibase e39rpigui meta-python openembedded-layer"

# Remove this once the layer has recipes.
BBFILE_PATTERN_IGNORE_EMPTY_e39rpimaps = "1"
```

The parent repo's commit pins the submodule commit, so the image is
reproducible as long as the submodule pointer is committed. `externalsrc`
builds uncommitted edits in the submodule, which is intended for development;
do not ship an image built from a dirty submodule without recording it.

---

## 4. Layer layout

```
meta-e39rpi-maps/
├── conf/
│   └── layer.conf
└── recipes-maps/
    ├── e39maps/
    │   ├── e39maps_git.bb
    │   └── files/
    │       ├── e39maps.env
    │       ├── e39maps.service
    │       ├── e39maps-init.service
    │       ├── e39maps-data.service
    │       └── e39maps-install-data
    └── e39maps-data/
        └── e39maps-data_git.bb
```

Add a packagegroup (or a plain `IMAGE_INSTALL:append`) so
`e39rpi-hmi-image` gets the app. Follow the media layer's conditional style if
you want the maps layer to remain optional:

```bitbake
# layers/meta-e39rpi-maps/recipes-core/images/e39rpi-hmi-image.bbappend
IMAGE_INSTALL:append = " packagegroup-e39rpi-maps"
```

QEMU port forwarding (add to the maps `layer.conf`; 8000 is MPD):

```bitbake
QB_SLIRP_OPT:append = ",hostfwd=tcp:127.0.0.1:8080-:8080,hostfwd=tcp:127.0.0.1:50051-:50051"
```

---

## 5. App recipe skeleton

```bitbake
SUMMARY = "Django/GeoDjango backend for e39-rpi-maps"
HOMEPAGE = "https://github.com/linster/e39-rpi-maps"
LICENSE = "MPL-2.0"
LIC_FILES_CHKSUM = "file://LICENSE;md5=f75d2927d3c1ed2414ef72048f5ad640"

PV = "0.1.0+git"

# Build straight from the e39-rpi-maps git submodule (or E39_MAPS_SRC from
# local.conf), like e39rpi-hmi.bb does for e39-rpi.
E39_MAPS_SRC ?= "${E39_MAPS_SRC_DEFAULT}"
EXTERNALSRC = "${E39_MAPS_SRC}"
EXTERNALSRC_SYMLINKS = ""

inherit externalsrc systemd useradd

SRC_URI = " \
    file://e39maps.env \
    file://e39maps.service \
    file://e39maps-init.service \
    file://e39maps-data.service \
    file://e39maps-install-data \
"

USERADD_PACKAGES = "${PN}"
USERADD_PARAM:${PN} = "--system --home /var/lib/e39maps \
    --shell /sbin/nologin --user-group e39maps"

do_install() {
    install -d ${D}${datadir}/e39rpi-maps
    cp -R --no-preserve=ownership \
        ${S}/manage.py ${S}/e39maps ${S}/core \
        ${D}${datadir}/e39rpi-maps/

    # Never ship local state or build artifacts.
    find ${D}${datadir}/e39rpi-maps -type d -name '__pycache__' -prune -exec rm -rf {} +
    find ${D}${datadir}/e39rpi-maps -name '*.py[co]' -delete
    rm -rf ${D}${datadir}/e39rpi-maps/db.sqlite3* \
           ${D}${datadir}/e39rpi-maps/staticfiles

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/e39maps.service ${D}${systemd_system_unitdir}/
    install -m 0644 ${WORKDIR}/e39maps-init.service ${D}${systemd_system_unitdir}/
    install -m 0644 ${WORKDIR}/e39maps-data.service ${D}${systemd_system_unitdir}/

    install -D -m 0755 ${WORKDIR}/e39maps-install-data ${D}${libexecdir}/e39maps-install-data

    install -d ${D}${sysconfdir}
    install -m 0644 ${WORKDIR}/e39maps.env ${D}${sysconfdir}/e39maps.env

    install -Dm0644 ${S}/.env.example ${D}${docdir}/${PN}/env.example
}

FILES:${PN} += " \
    ${datadir}/e39rpi-maps \
    ${libexecdir}/e39maps-install-data \
    ${systemd_system_unitdir}/e39maps.service \
    ${systemd_system_unitdir}/e39maps-init.service \
    ${systemd_system_unitdir}/e39maps-data.service \
    ${sysconfdir}/e39maps.env \
"
CONFFILES:${PN} += "${sysconfdir}/e39maps.env"

SYSTEMD_SERVICE:${PN} = "e39maps-data.service e39maps-init.service e39maps.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

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

Verify module package names for scarthgap with
`oe-pkgdata-util find-path '*/sqlite3/*.so'`; adjust if a module is folded into
`python3-misc`.

`python3-sqlite3` and `python3-core` are the expected names; `tzdata` is for
`zoneinfo` (the app uses `TIME_ZONE = "UTC"`, but Django's timezone machinery
still expects the database).

---

## 6. Configuration file

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

`ALLOWED_HOSTS` must include the name/IP clients use, or Django returns 400.
Tell the user to edit this file if the hostname differs. It is a `CONFFILES`
entry, so package upgrades preserve edits.

---

## 7. systemd units and ordering

Order: **data → init → web**. All three run as `e39maps`, use the same
`StateDirectory`, and read `/etc/e39maps.env`.

### `e39maps-data.service`

```ini
[Unit]
Description=Install e39-rpi-maps OSM dataset
After=local-fs.target e39rpi-growroot.service
Before=e39maps-init.service
# Skip cleanly when the optional e39maps-data package isn't installed.
ConditionPathExists=/usr/share/e39rpi-maps/data/e39maps-osm.sqlite.zst

[Service]
Type=oneshot
RemainAfterExit=yes
User=e39maps
Group=e39maps
StateDirectory=e39maps
StateDirectoryMode=0750
ExecStart=/usr/libexec/e39maps-install-data

[Install]
WantedBy=multi-user.target
```

`After=e39rpi-growroot.service` matters: the root partition is grown on first
boot, and the data installer needs the free space. If the dataset is mandatory
for your image, add `Requires=e39maps-data.service` to `e39maps-init.service`;
`ConditionPathExists` makes the data unit a clean no-op when the optional
`e39maps-data` package is not installed.

### `e39maps-init.service`

```ini
[Unit]
Description=e39-rpi-maps database and static initialization
After=local-fs.target e39maps-data.service
Before=e39maps.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=e39maps
Group=e39maps
StateDirectory=e39maps
StateDirectoryMode=0750
EnvironmentFile=/etc/e39maps.env
WorkingDirectory=/usr/share/e39rpi-maps

# Generate the secret once, mode 0600, on persistent storage.
ExecStartPre=/bin/sh -c 'test -s /var/lib/e39maps/secret_key || { umask 077; python3 -c "import secrets; print(secrets.token_urlsafe(50))" > /var/lib/e39maps/secret_key; }'

ExecStart=/usr/bin/python3 manage.py migrate --noinput
ExecStart=/usr/bin/python3 manage.py collectstatic --noinput

[Install]
WantedBy=multi-user.target
```

Both tasks are idempotent. `StateDirectory=e39maps` creates
`/var/lib/e39maps` owned by the user before `ExecStartPre` runs.

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
WorkingDirectory=/usr/share/e39rpi-maps

ExecStart=/usr/bin/gunicorn e39maps.wsgi:application \
    --bind 0.0.0.0:8080 \
    --workers 1 --threads 2 \
    --max-requests 1000 --max-requests-jitter 100 \
    --access-logfile - --error-logfile -
Restart=on-failure
RestartSec=5
TimeoutStopSec=20

MemoryMax=192M
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/e39maps

[Install]
WantedBy=multi-user.target
```

- **Port 8080**, not 8000: MPD's HTTP stream uses 8000 on this image.
- `WorkingDirectory` is what makes `e39maps.wsgi` and `core` importable;
  gunicorn inserts the cwd on `sys.path`.
- `ProtectSystem=strict` is safe because all writes go to `/var/lib/e39maps`.
  If you prefer not to harden the unit initially, keep `StateDirectory` anyway.
- Logs go to the journal. Do not add file logging on the SD card.

---

## 8. Getting a pre-populated database onto the device

This is the main integration decision. There are two database roles:

1. **OSM/map data** — large, derived from a Geofabrik extract, read-mostly.
2. **Django state** — auth, sessions, admin, user-created POIs; small, must be
   writable.

The rootfs is writable, so the simplest working design is **one writable
database** installed from a prebuilt artifact. For a read-only base plus user
edits, see "Split databases" below.

### 8.1 Recommended: build the DB on the host, ship it as a package

Do **not** run `osm2pgsql`/import on the Pi. Build the database on the x86
host (or CI) in an environment that matches the target's geospatial libraries:

| Library | Version in the image |
| --- | --- |
| libspatialite | 5.0.1 |
| GEOS | 3.12.1 |
| PROJ | 9.3.1 |
| SQLite | 3.45.3 |
| Python | 3.12 |

SpatiaLite is the compatibility risk: a database created with a newer
`libspatialite` may not open or may try to upgrade metadata on the device.
Build with 5.0.1 (e.g. a Debian/Ubuntu container that has it), and record the
versions in the artifact's `VERSION`/manifest.

Before packaging:

```sh
# In the build container, after the import:
python manage.py migrate                 # if the artifact includes Django tables
sqlite3 osm.sqlite "PRAGMA wal_checkpoint(TRUNCATE); VACUUM; ANALYZE;"
sqlite3 osm.sqlite "PRAGMA integrity_check;"   # must print "ok"
rm -f osm.sqlite-wal osm.sqlite-shm
zstd -19 -T0 -o e39maps-osm-<region>-<date>.sqlite.zst osm.sqlite
sha256sum e39maps-osm-<region>-<date>.sqlite.zst
```

Also ship:

- `VERSION` — e.g. `2026-11-01.<git-sha>.<schema-version>`; the installer uses
  it to decide whether to replace the installed DB.
- `ATTRIBUTION.txt` — "© OpenStreetMap contributors, ODbL 1.0" plus the
  license text/link. The OSM data is **ODbL-1.0**, not MPL-2.0.

Do not put a multi-hundred-MB artifact in git. Publish it as a release asset
or on an artifact server and reference it from the data recipe.

### 8.2 `e39maps-data` recipe

```bitbake
SUMMARY = "Pre-populated OSM/SpatiaLite database for e39-rpi-maps"
HOMEPAGE = "https://github.com/linster/e39-rpi-maps"
LICENSE = "ODbL-1.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/ODbL-1.0;md5=498d26a501c10739d57fca1c9d80bbc2"

# Replace with the real artifact URL and checksum. Keep the dataset version in
# PV so a new artifact is a package upgrade.
PV = "2026-11-01"
SRC_URI = "https://example.invalid/e39maps-osm-<region>-${PV}.sqlite.zst;downloadfilename=e39maps-osm.sqlite.zst;name=data \
           file://ATTRIBUTION.txt"
SRC_URI[data.sha256sum] = "<sha256 of the .zst>"

S = "${WORKDIR}"

inherit allarch

do_install() {
    install -d ${D}${datadir}/e39rpi-maps/data
    install -m 0644 ${WORKDIR}/e39maps-osm.sqlite.zst ${D}${datadir}/e39rpi-maps/data/
    echo "${PV}" > ${D}${datadir}/e39rpi-maps/data/VERSION
    chmod 0644 ${D}${datadir}/e39rpi-maps/data/VERSION
    install -m 0644 ${WORKDIR}/ATTRIBUTION.txt ${D}${datadir}/e39rpi-maps/data/
}

FILES:${PN} = "${datadir}/e39rpi-maps/data"
RDEPENDS:${PN} = "zstd"
```

For local development, override `SRC_URI`/`E39_MAPS_DATA_SRC` to a `file://`
path or use `devtool`; do not commit the artifact to the layer. Put
`ATTRIBUTION.txt` in the recipe's `files/` directory (it is not part of the
large artifact).

### 8.3 First-boot installer

`files/e39maps-install-data` (installed to `/usr/libexec/e39maps-install-data`):

```sh
#!/bin/sh
set -eu

src=/usr/share/e39rpi-maps/data/e39maps-osm.sqlite.zst
dir=/var/lib/e39maps
dst="$dir/db.sqlite3"
marker="$dir/.dataset-version"
want="$(cat /usr/share/e39rpi-maps/data/VERSION)"

if [ -f "$dst" ] && [ -f "$marker" ] && [ "$(cat "$marker")" = "$want" ]; then
    echo "dataset $want already installed"
    exit 0
fi

mkdir -p "$dir"

# Refuse to start if the decompressed DB plus a temp copy cannot fit. zstd -l
# prints the uncompressed size as a number + unit; convert to bytes. Keep a
# 10% margin.
need="$(zstd -l "$src" | awk 'NR == 2 {
    mult = 1
    if ($6 == "KiB") mult = 1024
    else if ($6 == "MiB") mult = 1024 * 1024
    else if ($6 == "GiB") mult = 1024 * 1024 * 1024
    printf "%.0f", $5 * mult
}')"
avail="$(df -Pk "$dir" | awk 'NR==2 {print $4 * 1024}')"
if [ -n "$need" ] && [ "$need" -gt 0 ] && [ "$avail" -lt $((need + need / 10)) ]; then
    echo "not enough space: need ~$need bytes, have $avail" >&2
    exit 1
fi

tmp="$dst.tmp"
zstd -d -q -f -o "$tmp" "$src"
chmod 0640 "$tmp"

# Cheap sanity check. immutable=1 keeps SQLite from creating -wal/-shm files
# for a database that is only being read.
python3 - "$tmp" <<'PY'
import sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro&immutable=1", uri=True)
result = con.execute("PRAGMA integrity_check").fetchone()[0]
if result != "ok":
    sys.exit("integrity check failed: " + result)
PY

mv -f "$tmp" "$dst"
echo "$want" > "$marker"
echo "installed dataset $want"
```

Notes:

- The installer runs as `e39maps`, so the extracted file is already owned by
  the right user; no `chown` is needed. (The `tmpfiles.d` variant runs as root
  and needs the `Z` line.)
- `mv` within the same directory is atomic. On a dataset update this replaces
  the old DB; if you need rollback, keep `db.sqlite3.old` (costs one extra copy
  on the card).
- Do not ship `-wal`/`-shm` files. If the app uses WAL, they are created at
  runtime.
- If the dataset is small (tens of MB), the simpler `tmpfiles.d` pattern used
  by the HMI is enough:

  ```ini
  # Alternative layout: the data package ships an uncompressed
  # default-data/db.sqlite3 instead of the .zst.
  C /var/lib/e39maps/data - - - - /usr/share/e39rpi-maps/default-data
  Z /var/lib/e39maps/data - e39maps e39maps -
  ```

  `C` copies only when the destination does not exist, so it does not handle
  dataset updates. It also means `DJANGO_DB_PATH` must point into that
  subdirectory (e.g. `/var/lib/e39maps/data/db.sqlite3`). Use the
  version-aware installer for anything that changes.

### 8.4 One DB or two?

**v1 (recommended): one writable DB.**

- Install the artifact to `/var/lib/e39maps/db.sqlite3`.
- `migrate` in the init unit applies any app migrations.
- Admin can edit everything.
- Downside: installing a new dataset **overwrites admin edits**. Treat OSM
  data as regenerable and keep user-created data in a separate DB before that
  becomes a problem.

**Later: split read-only OSM base + writable state.**

- Open the base DB in place, read-only:
  `NAME = "file:/usr/share/e39rpi-maps/data/osm.sqlite?mode=ro&immutable=1"`.
  Django passes `uri=True` to SQLite, so URI filenames work.
- Omit `init_command` for that alias. `PRAGMA journal_mode=WAL` fails on a
  read-only database; the current `default` settings include it.
- Add a `DATABASE_ROUTERS` entry routing OSM models to the `osm` alias and
  auth/admin/user models to `default`.
- No cross-database joins. Keep queries within one alias.
- SpatiaLite tables live in the read-only DB; `InitSpatialMetaData` must have
  run when the artifact was built. Django's SpatiaLite backend checks
  `geometry_columns` on connect and only initializes when it is empty.
- Updates become a package upgrade of `e39maps-data` (atomic at the file
  level), and user edits survive.

Do not attempt the split until there is an actual OSM model and router; the
single-DB path is much less code for the hello-world stage.

### 8.5 Alternatives (and why not)

- **Import `.osm.pbf` on first boot:** slow, RAM-hungry, needs `osm2pgsql` in
  the image, and writes heavily to the SD card. Don't.
- **Download the DB on first boot:** viable as an *update* mechanism, but adds
  a network dependency and failure modes to initial provisioning. For an
  in-car device, ship the dataset in the image.
- **USB stick or second partition:** use if the dataset outgrows the rootfs.
  The current WIC layout is boot + root only; a data partition needs a custom
  `.wks`, an fstab entry, and a mount unit. Not needed for a regional extract
  that fits in the rootfs.

### 8.6 Space budget

`IMAGE_ROOTFS_EXTRA_SPACE` is 512 MiB, and the card grows to fill the SD on
first boot. The **image** still has to contain the compressed artifact, and
first boot needs room for the decompressed DB plus a temp copy. A regional
extract that compresses to a few hundred MB is fine on a 16 GB card; a
multi-GB dataset is not. Measure the artifact before committing to this
approach.

---

## 9. Static files

Keep first-boot `collectstatic` into `/var/lib/e39maps/static`. It is a few
seconds once, the rootfs is writable, and it avoids needing Django and
WhiteNoise runnable on the build host.

`python-django.inc` does set `BBCLASSEXTEND = "native nativesdk"`, so a
build-time `collectstatic` is possible with `python3-django-native`. It would
also need a native WhiteNoise (not defined in the pinned meta-python) or a
settings switch to plain `StaticFilesStorage`. Not worth it unless boot time
becomes a measured problem.

Do not set `DJANGO_DEBUG=1` in production to avoid the manifest.

---

## 10. GeoDjango and gRPC

**GeoDjango** (not in the app yet):

- `RDEPENDS:${PN} += "libspatialite geos proj"` when the SpatiaLite backend
  lands. `django.contrib.gis` imports GDAL at startup (its system check), so
  add GDAL too — via a geospatial layer or a custom recipe; GDAL is not in the
  pinned layers. Ask before adding a new layer.
- Set `SPATIALITE_LIBRARY_PATH` only if `find_library("spatialite")` fails on
  the target.
- The image's `libspatialite` 5.0.1 must match the version used to build the
  DB artifact.

**gRPC** (not in the app yet):

- `python3-grpcio` 1.62.2 and `python3-grpcio-tools` 1.62.2 are in
  meta-python; no custom build is needed initially.
- Generate `*_pb2.py`/`*_pb2_grpc.py` with the matching `grpcio-tools` and
  commit them; do not run codegen on the device.
- Run it as a separate `e39maps-grpc.service` (port 50051, same user/env,
  `Requires=e39maps-init.service`), with its own `MemoryMax`. Do not embed it
  in the gunicorn process.
- Do not add the unit until the management command exists.

---

## 11. Build and acceptance checks

```sh
cd ~/e39-rpi-yocto
source setup-env                      # or the usual oe-init-build-env
bitbake e39maps e39maps-data
oe-pkgdata-util list-pkg-files -p e39maps
bitbake e39rpi-hmi-image
```

On the target or `runqemu`:

```sh
systemctl status e39maps-data.service e39maps-init.service e39maps.service
journalctl -u e39maps-data.service -u e39maps-init.service -u e39maps.service --no-pager

curl -fsS http://127.0.0.1:8080/healthz        # {"status": "ok"}
curl -fsS http://127.0.0.1:8080/ | grep "Hello from Django"
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/admin/   # 302
ls -l /var/lib/e39maps/                        # db, secret_key, static
test ! -e /usr/share/e39rpi-maps/db.sqlite3    # no local state packaged
test ! -d /usr/share/e39rpi-maps/.venv         # no venv packaged
systemctl show -p MemoryCurrent e39maps.service
```

Also verify:

- The rendered CSS URL is hashed (`/static/core/site.<hash>.css`) and returns
  200.
- `/var/lib/e39maps/secret_key` is mode `0600`.
- A reboot does not regenerate the key and does not rerun the data installer
  when `VERSION` is unchanged.
- Bumping `e39maps-data`'s `PV` replaces the DB on the next boot and logs it.
- `DJANGO_ALLOWED_HOSTS` matches how clients reach the device.

`manage.py check --deploy` with `DJANGO_HTTPS=1` should be clean. With plain
HTTP expect `W004`, `W008`, `W012`, `W016`; that is intentional for the LAN
device. Do not enable HSTS on plain HTTP.

---

## 12. Pitfalls checklist

- [ ] Add the repo as a **submodule** and use `externalsrc`, matching
      `e39rpi-hmi.bb`; don't introduce `gitsm` unless asked.
- [ ] `E39_MAPS_SRC_DEFAULT` points at `<yocto-root>/e39-rpi-maps`.
- [ ] No `pip`, venv, or `pip bundle` on the target.
- [ ] `cp --no-preserve=ownership` (build runs as root).
- [ ] Remove `__pycache__`, `*.pyc`, `db.sqlite3*`, `staticfiles/` from `${D}`.
- [ ] `WorkingDirectory=/usr/share/e39rpi-maps` on init and web units.
- [ ] Ordering: data → init → web (`After`; web `Requires` init).
- [ ] Secret generated at first boot, never baked in.
- [ ] `DJANGO_ALLOWED_HOSTS` includes the device hostname/IP.
- [ ] Port **8080**; 8000 belongs to MPD.
- [ ] One gunicorn worker, `MemoryMax` set.
- [ ] Dataset is a separate `e39maps-data` package, ODbL-1.0, checksummed.
- [ ] Build the dataset with libspatialite **5.0.1** to match the image.
- [ ] Install dataset after `e39rpi-growroot.service`; check free space.
- [ ] Installed DB is owned by `e39maps` (the installer runs as that user; the
      `tmpfiles.d` variant uses the `Z` line); no `-wal`/`-shm` in the artifact.
- [ ] Don't ship users/sessions/secret key in the DB.
- [ ] If Django stays at 5.0.11, fix the `init_command` incompatibility.
- [ ] Verify Python module package names for scarthgap.
- [ ] Don't add gRPC/GDAL deps before the code exists.

---

## 13. Useful commands

```sh
# In the app repo
git rev-parse HEAD                              # submodule commit to pin
md5sum LICENSE                                  # LIC_FILES_CHKSUM

# In the Yocto repo
git submodule status
bitbake-layers show-recipes 'python3-*' | grep -E 'django|gunicorn|whitenoise|grpcio'
oe-pkgdata-util find-path '*/django/__init__.py'
oe-pkgdata-util list-pkg-files python3-django | head -40
bitbake -e e39maps | grep -E '^(RDEPENDS|FILES|SYSTEMD_SERVICE|EXTERNALSRC)'
devtool modify e39maps                          # local iteration only
```

---

## 14. Current limitations (do not paper over)

- No GeoDjango models, SpatiaLite backend, or OSM import yet.
- No gRPC service, generated stubs, or `run_grpc_server` command yet.
- No custom user model; admin uses Django's default.
- No `pyproject.toml`; source-tree installation is intentional for now.
- The app is pinned to Django 5.2.19, while the pinned meta-python has 5.0.11.
  Resolve that before building (see §2).
