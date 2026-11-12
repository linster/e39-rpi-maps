# Updating the car's map from a USB stick

A **map** is two files that belong together:

| File | What | Size |
| --- | --- | --- |
| `e39maps-osm.sqlite` | the map database: POIs (fuel, restaurants, parks, ...), and the facts that identify this map | ~13 MB |
| `e39maps-osm.routing.tar` | its routing tiles (Valhalla, for turn-by-turn routes) | ~625 MB |

Maps are published as **releases** of this repository on GitHub
(`map-<date>`, e.g. `map-20261112T203245Z`), with both files as release
assets. The car installs a map from a USB stick by itself: put the two
files in a folder named **`RPIMAPS`** on the stick and plug it in.

## What you need

- A USB stick with at least **1 GB free**. FAT32 (most sticks as sold),
  exFAT or ext4 all work: the files are under FAT32's 4 GB limit.
- Access to the repository on GitHub (it's private): a GitHub account that
  can see `linster/e39-rpi-maps`.

## 1. Get the map onto the stick

Pick one way.

### a) With the map toolkit (window)

1. `toolkit/map-toolkit-container.sh` (or `toolkit/map-toolkit.sh`).
2. The first time only: log in to GitHub in a terminal with `gh auth login`
   (GitHub.com, HTTPS, log in with a web browser), then restart the toolkit.
3. **Published maps**: click **Refresh**, choose a release (the one marked
   *pinned* is the map the current image ships).
4. Plug in the stick, click **Download + copy to USB stick...**, and choose
   the stick (its folder, e.g. `/media/<you>/STICK`).
5. Wait for "Done: ... has the map built ...". The toolkit checks both
   files (SHA-256) after downloading and makes `RPIMAPS` on the stick.
6. Eject the stick.

A map you built yourself (not published) goes on a stick the same way with
**Copy to USB stick...** in the *Map database* section.

### b) With the map toolkit (command line)

```sh
gh auth login                                   # once
toolkit/map-toolkit-container.sh releases       # list published maps
toolkit/map-toolkit-container.sh usb /media/$USER/STICK --release map-20261112T203245Z
# without --release: the map you built locally
```

### c) By hand, with a web browser

1. Open <https://github.com/linster/e39-rpi-maps/releases> (logged in).
2. Pick a `Map ...` release. Under **Assets**, download
   **`e39maps-osm.sqlite`** and **`e39maps-osm.routing.tar`** (the other
   assets aren't needed in the car).
3. On the stick, make a folder **`RPIMAPS`** at the top level (upper or
   lower case, either works).
4. Copy both files into it. **Don't rename them**: the database names its
   routing tiles, and the car only accepts that pair.
5. Optional, to check the download: the release's `map-release.json` lists
   both files' sizes and SHA-256 sums (`sha256sum *` in the folder).
6. Eject the stick properly (so the copy is complete).

The stick should look like this:

```
STICK/
└── RPIMAPS/
    ├── e39maps-osm.sqlite
    └── e39maps-osm.routing.tar
```

## 2. Plug it into the car

Plug the stick into the Pi (car on, system running). It's mounted
automatically; when it has an `RPIMAPS` folder, the map import starts in
the background:

1. Both files are checked: a map database from the toolkit, not damaged,
   its routing tiles the ones it was built with (size, SHA-256), built for
   this system's Valhalla version.
2. They're copied off the stick first (so pulling it out mid-way can't
   leave a half-installed map; the old map stays until the copy is complete).
3. The map services stop for a few seconds, the new map replaces the old
   one, they start again. Your own POIs and settings aren't touched: they're
   in a separate database.
4. On the stick, the files are renamed to
   `e39maps-osm.sqlite.copied-<date-time>` and
   `e39maps-osm.routing.tar.copied-<date-time>`.

The import takes about **a minute** (most of it copying 640 MB from the
stick). When the files on the stick end in `.copied-...`, it's done and the
stick can be pulled.

### What the names on the stick mean afterwards

| Name ends in | Meaning |
| --- | --- |
| `.copied-<date-time>` | installed |
| `.rejected-<date-time>` | not a usable map: damaged or incomplete download, the routing tiles don't belong to the database, or built for another Valhalla version. Download again. |
| `.skipped-<date-time>` | there was a newer map in the folder; that one was installed |

Since the files are renamed, plugging the same stick in again does nothing.
To install the same map again, rename the files back.

## Checking

On the Pi (or QEMU), as root:

```sh
/usr/libexec/e39maps-mapdb info          # the installed map: built_at, POIs, routing tiles, region
journalctl -t e39maps-mapdb              # what the last import did
```

From another machine: `http://<pi>:8080/healthz` shows the installed map's
facts (`built_at`, `region`, ...).

## Other ways

- **Live swap** over the network (laptop and car on the same network, or
  QEMU): the toolkit's **Live swap** / **Download + live swap**, or
  `toolkit/map-toolkit-container.sh push [--release TAG] [--target HOST]`.
- **A new image**: the image build ships the map that `map-release.json`
  pins. On a build machine: `scripts/fetch-map.sh` in e39-rpi-yocto
  downloads it into the build's map data folder, then build as usual.

## Publishing a map (for whoever builds maps)

1. Build and test a map with the toolkit (**Build map database**, then
   **Live swap** to QEMU).
2. Commit and push e39-rpi-maps (the release's tag points at that commit).
3. **Publish local map** (or `toolkit/map-toolkit-container.sh publish`):
   uploads both files, `ATTRIBUTION.txt` and `map-release.json` as a
   release `map-<built_at>`, and writes `map-release.json` in the
   repository, which pins this map for image builds.
4. Commit and push `map-release.json` (and bump the e39-rpi-yocto submodule)
   so new images ship it.

Release assets have no storage or download quota on GitHub (unlike Git LFS),
but each file must be under 2 GiB.
