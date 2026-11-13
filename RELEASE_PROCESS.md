# Map release process

How a map gets from the toolkit to a GitHub release, and from there into
car images. The user side (putting a published map on a USB stick, live
swaps) is in [`map_updates.md`](map_updates.md).

## What a release is

A published map is a **GitHub release** of this repository. Its assets:

| Asset | What |
| --- | --- |
| `e39maps-osm.sqlite` | the map database (POIs and facts about the map) |
| `e39maps-osm.routing.tar` | its Valhalla routing tiles |
| `e39maps-osm.tiles-NN.mbtiles` | its offline map tiles, if rendered (each under GitHub's 2 GiB asset limit) |
| `ATTRIBUTION.txt` | the OpenStreetMap licence notice |
| `map-release.json` | the release's manifest: every file's name, size and SHA-256 |

Releases are named after the region's city and the build time:

- tag `map-<city>-<built_at>`, e.g. `map-ottawa-20261113T044215Z`;
- title `Map: <city> (<radius> km), <date>`, e.g. "Map: Ottawa (530 km), 2026-11-13".

Maps built before regions had names are just `map-<built_at>`. Releases are
never replaced: a new build is a new release, and older ones stay
available.

### The name

A map is named after the largest metropolitan area at the centre of its
circle:

1. Of the OSM cities (`place=city`) within a quarter of the radius of the
   centre (at least 30 km, at most 150 km), the one with the largest
   population (OSM's `population` tag).
2. With no city there: the nearest city in the circle, then the nearest
   town, then the region's name in the toolkit.

Ottawa + 530 km is "Ottawa": Gatineau is nearby but smaller, and Montréal,
165 km out, isn't at the centre. Edmonton + 1000 km is "Edmonton". The
build collects cities and towns in the same pass that reads the POIs, logs
the name ("Named after the largest city at the centre: Ottawa") and
stores it in the database's facts (`region_label`, `region_slug`; see
`toolkit/map-toolkit.sh info`).

## Pinning

There can be any number of releases, of several regions. **Pinning**
chooses the one that new car images ship:

- The committed `map-release.json` at the top of this repository names
  exactly one release, with its files' sizes and SHA-256: the **pinned**
  map.
- e39-rpi-yocto includes this repository as a submodule. Its
  `scripts/fetch-map.sh` reads `map-release.json`, downloads that release
  into the build's map folder and checks every file; the `e39maps-data`
  recipe then installs it in the image ("Map: the published release ..."
  in the build log; a locally built map instead gets a warning).
- So the pin travels with a commit: a new pin is a commit of
  `map-release.json` here, then a submodule update in e39-rpi-yocto.

The pin doesn't limit what can go in the car: any release can be
downloaded, live-swapped onto a running device or copied to a USB stick
(toolkit: `fetch`, `push --release TAG`, `usb DIR --release TAG`). It only
sets what a freshly flashed SD card starts with.

### When publishing pins

| Pinned now | Publishing a map of | Pins it? |
| --- | --- | --- |
| nothing | any region | yes |
| a map from before names | any region | yes (there was only one region then) |
| Ottawa | Ottawa (a newer build) | yes |
| Ottawa | Edmonton | **no**, unless asked |

A map of another region doesn't silently take over the car's image. The
toolkit window asks whether to pin it instead; on the command line,
`publish --pin` pins it anyway and `publish --no-pin` never pins.

## Steps

1. **Pick the region** in the toolkit (centre and radius on the map, then
   the extracts it needs; e.g. Edmonton + 1000 km needs Alberta, British
   Columbia and Saskatchewan).
2. **Render offline map tiles** (optional, `toolkit/render-tiles.sh` or the
   toolkit's button). Each region's tile files are kept, so later builds
   of that region reuse them. Rendering another region re-imports the
   renderer's database, which holds one region at a time (35 minutes and
   27 GB for Ottawa + 530 km, more for a bigger circle).
3. **Build the map** (**Build map database** or `toolkit/map-toolkit.sh
   build`). Check the name it logs.
4. **Test it**: **Live swap** to QEMU (or a Pi) and look at POIs, routes
   and tiles in the HMI.
5. **Commit and push e39-rpi-maps.** The release's tag points at the
   current commit, so it must be on GitHub first.
6. **Publish** (**Publish local map** or `toolkit/map-toolkit.sh publish
   [--pin | --no-pin]`; needs `gh auth login` once). This uploads the
   assets; a 2.9 GB map takes about 10 minutes. If the upload fails, the
   incomplete release is deleted.
7. **Check it** (optional): `toolkit/map-toolkit.sh releases` lists it, and
   `toolkit/map-toolkit.sh fetch TAG` downloads it back and checks every
   file against the manifest.
8. **If it was pinned**, `map-release.json` changed:
   1. Commit and push it here.
   2. In e39-rpi-yocto, update the `e39-rpi-maps` submodule to that commit,
      then commit and push.
   3. On build machines, run `scripts/fetch-map.sh --force` before the
      next image build.

   If it wasn't pinned, nothing else changes: the release is there for USB
   sticks and live swaps.

## Re-pinning an existing release

To make images ship a release that's already published (e.g. switching the
car back from Edmonton to Ottawa), there's nothing to publish:

```sh
toolkit/map-toolkit.sh releases      # find the tag
toolkit/map-toolkit.sh pin map-ottawa-20261113T044215Z
```

`pin` writes that release's own manifest as `map-release.json` (only a
small download; the map files aren't fetched). Then continue with step 8.
