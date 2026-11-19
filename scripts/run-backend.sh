#!/usr/bin/env bash
# Start the Django backend for development: the HTTP server (page, /healthz,
# admin, /tiles) and the gRPC API for the HMI, together. Ctrl-C stops both.
#
#   scripts/run-backend.sh                 HTTP on 127.0.0.1:8000, gRPC on 0.0.0.0:50051
#   scripts/run-backend.sh 0.0.0.0:8000    HTTP on another address
#   scripts/run-backend.sh --no-grpc       HTTP only
#
# Creates .venv (repo root) if missing and installs src/requirements/base.txt
# whenever that file changes. Reads .env (repo root or src/) if present, since
# settings.py only reads the environment. Runs migrate before starting.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
REQS="$ROOT/src/requirements/base.txt"

HTTP_ADDR=127.0.0.1:8000
GRPC=1
for arg in "$@"; do
    case "$arg" in
        --no-grpc) GRPC=0 ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) echo "Unknown option: $arg" >&2; exit 2 ;;
        *) HTTP_ADDR="$arg" ;;
    esac
done

# Virtualenv, with the pinned requirements (reinstalled when base.txt changes).
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
STAMP="$VENV/.base-requirements.sha256"
WANT="$(sha256sum "$REQS" | cut -d' ' -f1)"
if [ "$(cat "$STAMP" 2>/dev/null)" != "$WANT" ]; then
    echo "Installing $REQS..." >&2
    "$VENV/bin/pip" install -q -r "$REQS"
    echo "$WANT" > "$STAMP"
fi
PY="$VENV/bin/python"

# Environment from .env, if any (variables already set win).
for envfile in "$ROOT/.env" "$ROOT/src/.env"; do
    [ -f "$envfile" ] || continue
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in ''|\#*) continue ;; esac
        name="${line%%=*}"
        [ -z "${!name+x}" ] && export "$line"
    done < "$envfile"
done

cd "$ROOT/src"
"$PY" manage.py migrate --noinput

PIDS=()
cleanup() {
    trap - INT TERM EXIT
    for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
    wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

if [ "$GRPC" = 1 ]; then
    "$PY" manage.py run_grpc_server &
    PIDS+=($!)
fi
"$PY" manage.py runserver "$HTTP_ADDR" &
PIDS+=($!)

echo "HTTP: http://$HTTP_ADDR/  (admin: /admin/, health: /healthz)" >&2
[ "$GRPC" = 1 ] && echo "gRPC: ${DJANGO_GRPC_BIND:-0.0.0.0:50051}" >&2

# Stop everything when either server exits.
wait -n
