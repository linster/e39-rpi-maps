#!/usr/bin/env bash
# Render offline map tiles in OpenStreetMap's own style (openstreetmap-carto,
# Mapnik) for the map toolkit's region, into MBTiles files:
#
#   toolkit/render-tiles.sh            prepare, import (if needed), render
#   toolkit/render-tiles.sh --stop     stop the tile server, if one was left running
#
# Steps (podman; the region, extracts and tile plan come from the toolkit's
# settings):
#   1. prepare: the toolkit container clips the extracts to the circle and
#      merges them (osmium-tool), and finds the towns.
#   2. import: overv/openstreetmap-tile-server imports that into PostGIS, in
#      the podman volume e39maps-osm-db (~1-2 hours, tens of GB; needs the
#      internet once for coastline data). Only when the region or the
#      extracts changed.
#   3. render: the same image runs renderd; for each zoom level the toolkit
#      lists the plan's metatiles (8x8 tiles) still to draw and renderd's
#      render_list draws them (no web request timeouts; its tile cache is
#      the volume e39maps-osm-tiles, so an interrupted run resumes).
#   4. collect: the toolkit fetches the drawn tiles over HTTP (127.0.0.1:$PORT)
#      into <work>/tiles-NN.mbtiles, a new file every ~1.9 GB.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_IMAGE=docker.io/overv/openstreetmap-tile-server:2.3.0
VOLUME=e39maps-osm-db
TILES_VOLUME=e39maps-osm-tiles
CONTAINER=e39maps-osm-render
PORT="${E39MAPS_RENDER_PORT:-8091}"
THREADS="${E39MAPS_RENDER_THREADS:-$(nproc)}"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/e39maps-toolkit"

command -v podman >/dev/null || { echo "Needs podman" >&2; exit 1; }
stop_server() { podman rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
if [ "${1:-}" = --stop ]; then stop_server; exit 0; fi

echo "== 1. prepare"
WORK="$("$HERE/map-toolkit-container.sh" render-prepare | tail -1)"
[ -f "$WORK/region.osm.pbf" ] || { echo "prepare failed" >&2; exit 1; }
KEY="$(basename "$WORK")"

echo "== 2. import ($KEY)"
DB_KEY_FILE="$CACHE/render/imported-key"
if podman volume exists "$VOLUME" && [ "$(cat "$DB_KEY_FILE" 2>/dev/null)" = "$KEY" ]; then
    echo "PostGIS already has this region"
else
    stop_server
    podman volume rm -f "$VOLUME" "$TILES_VOLUME" >/dev/null 2>&1 || true
    podman volume create "$VOLUME" >/dev/null
    rm -f "$DB_KEY_FILE"
    echo "Importing (this takes a while; log: $WORK/import.log)"
    podman run --rm --name "$CONTAINER-import" --shm-size=2g -e THREADS="$THREADS" \
        -e "OSM2PGSQL_EXTRA_ARGS=-C 12000" \
        -v "$WORK/region.osm.pbf:/data/region.osm.pbf:ro" -v "$VOLUME:/data/database/" \
        "$SERVER_IMAGE" import > "$WORK/import.log" 2>&1
    echo "$KEY" > "$DB_KEY_FILE"
fi

echo "== 3. render"
stop_server
podman volume exists "$TILES_VOLUME" || podman volume create "$TILES_VOLUME" >/dev/null
podman run -d --name "$CONTAINER" --shm-size=2g -e THREADS="$THREADS" \
    -p "127.0.0.1:$PORT:80" -v "$VOLUME:/data/database/" -v "$TILES_VOLUME:/data/tiles/" \
    "$SERVER_IMAGE" run >/dev/null
trap stop_server EXIT
for _ in $(seq 1 120); do
    curl -sf -o /dev/null "http://127.0.0.1:$PORT/tile/0/0/0.png" && break
    sleep 5
done
for z in $("$HERE/map-toolkit-container.sh" render-plan --work "$WORK" --zooms); do
    "$HERE/map-toolkit-container.sh" render-plan --work "$WORK" --zoom "$z" > "$WORK/metatiles.txt"
    n=$(grep -c . "$WORK/metatiles.txt" || true)
    echo "zoom $z: $n metatiles to draw ($(date +%H:%M:%S))"
    [ "$n" -gt 0 ] || continue
    podman exec -i "$CONTAINER" render_list -n "$THREADS" -l 1000 -z "$z" -Z "$z" < "$WORK/metatiles.txt" \
        | grep -E "Rendered|Total" || true
done
rm -f "$WORK/metatiles.txt"

echo "== 4. collect"
"$HERE/map-toolkit-container.sh" render-tiles --server "http://127.0.0.1:$PORT/tile/{z}/{x}/{y}.png" --work "$WORK"
echo "Done: $WORK/tiles-*.mbtiles"
