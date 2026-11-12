#!/usr/bin/env bash
# Run the e39-rpi map toolkit in a container (podman, else docker), so it
# always has the pinned Python, pyosmium and pyvalhalla (the Valhalla version
# the backend and the image are frozen to), whatever this machine has.
#
#   toolkit/map-toolkit-container.sh            the window
#   toolkit/map-toolkit-container.sh build      (or info, push, update-image; see --help)
#
# The image is built on first use and whenever toolkit/requirements.txt or
# toolkit/Dockerfile change (it's tagged with a hash of both). The toolkit's
# code comes from this checkout (mounted), as do its cache, settings and the
# e39-rpi-yocto checkout, at the same paths as here, so saved settings and
# file paths mean the same inside and out. Files it writes belong to you.
#
# "Update image" needs bitbake, which runs on this machine, not in the
# container: this script keeps a small helper running that starts
# e39-rpi-yocto's scripts/with-progress.sh scripts/build.sh <machine> when
# the toolkit asks (a request file in a shared directory).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

if command -v podman >/dev/null; then
    ENGINE=podman
    # Rootless podman: map our uid to the same uid inside, so files are ours.
    USER_ARGS=(--userns=keep-id)
elif command -v docker >/dev/null; then
    ENGINE=docker
    USER_ARGS=(--user "$(id -u):$(id -g)")
else
    echo "Needs podman or docker" >&2
    exit 1
fi

VALHALLA="$(sed -n 's/^pyvalhalla==//p' "$HERE/requirements.txt")"
HASH="$(cat "$HERE/requirements.txt" "$HERE/Dockerfile" | sha256sum | cut -c1-12)"
IMAGE="localhost/e39maps-toolkit:valhalla-$VALHALLA-$HASH"
if ! $ENGINE image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "Building the toolkit image $IMAGE (once)..." >&2
    $ENGINE build -t "$IMAGE" -f "$HERE/Dockerfile" "$HERE" >&2
fi

CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/e39maps-toolkit"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/e39maps-toolkit"
mkdir -p "$CACHE" "$CONFIG"
YOCTO="$(python3 - "$CONFIG/settings.json" <<'EOF' 2>/dev/null || true
import json, sys
print(json.load(open(sys.argv[1])).get("yocto_dir", ""))
EOF
)"
[ -n "$YOCTO" ] || for d in "$REPO/../e39-rpi-yocto" "$HOME/e39-rpi-yocto"; do
    [ -x "$d/scripts/build.sh" ] && YOCTO="$(cd "$d" && pwd)" && break
done

# Fontconfig (Tk's fonts) caches under ~/.cache/fontconfig: give it a
# directory of its own inside the toolkit's cache.
mkdir -p "$CACHE/container-fontconfig"
MOUNTS=(-v "$REPO:$REPO" -v "$CACHE:$CACHE" -v "$CONFIG:$CONFIG"
        -v "$CACHE/container-fontconfig:${CACHE%/*}/fontconfig")
[ -n "$YOCTO" ] && [ -d "$YOCTO" ] && MOUNTS+=(-v "$YOCTO:$YOCTO")
[ -d "$HOME/.ssh" ] && MOUNTS+=(-v "$HOME/.ssh:$HOME/.ssh:ro")

# The window: this machine's X server (Wayland desktops run X apps through
# XWayland, which sets DISPLAY too).
X_ARGS=()
if [ -n "${DISPLAY:-}" ]; then
    X_ARGS=(-e DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix:ro)
    if [ -n "${XAUTHORITY:-}" ] && [ -f "$XAUTHORITY" ]; then
        X_ARGS+=(-e XAUTHORITY="$XAUTHORITY" -v "$XAUTHORITY:$XAUTHORITY:ro")
    fi
fi

# Host builds for "Update image": the toolkit writes <id>.request (JSON:
# yocto_dir, machine) here; we run the build and answer with <id>.reply
# (with-progress.sh's output: the log path).
REQUESTS="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/e39maps-toolkit.XXXXXX")"
serve_builds() {
    while sleep 1; do
        for req in "$REQUESTS"/*.request; do
            [ -e "$req" ] || continue
            reply="${req%.request}.reply"
            python3 - "$req" > "$reply.tmp" 2>&1 <<'EOF' || true
import json, subprocess, sys
r = json.load(open(sys.argv[1]))
out = subprocess.run([r["yocto_dir"] + "/scripts/with-progress.sh", "scripts/build.sh", r["machine"]],
                     cwd=r["yocto_dir"], capture_output=True, text=True, timeout=120)
print(out.stdout + out.stderr, end="")
print(f"exit={out.returncode}")
EOF
            rm -f "$req"
            mv "$reply.tmp" "$reply"
        done
    done
}
serve_builds &
HELPER=$!
trap 'kill $HELPER 2>/dev/null; rm -rf "$REQUESTS"' EXIT

TTY_ARGS=()
[ -t 0 ] && [ -t 1 ] && TTY_ARGS=(-it)

$ENGINE run --rm "${TTY_ARGS[@]}" "${USER_ARGS[@]}" \
    --network host \
    -e HOME="$HOME" -e XDG_CACHE_HOME="${CACHE%/*}" -e XDG_CONFIG_HOME="${CONFIG%/*}" \
    -e PYTHONPATH="$HERE:$REPO/src" \
    -e E39MAPS_HOST_BUILDS="$REQUESTS" -v "$REQUESTS:$REQUESTS" \
    -e E39MAPS_TOOLKIT_CONTAINER="$IMAGE" \
    "${MOUNTS[@]}" "${X_ARGS[@]}" \
    -w "$REPO" \
    "$IMAGE" "$@"
