#!/usr/bin/env bash
# Generate the Python gRPC code for proto/e39maps_api/v1/*.proto into
# src/e39maps_api/v1/ (committed: neither the device nor the Yocto build runs
# protoc). Run after changing a .proto, with the dev requirements installed:
#
#   pip install -r src/requirements/dev.txt
#   scripts/gen-proto.sh
#
# grpcio-tools is pinned to the image's grpcio (1.62.2, protobuf 4.25.3):
# code from a newer generator can refuse to load with the older runtime.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
OUT="$ROOT/src"

"$PYTHON" -c 'import grpc_tools' 2>/dev/null || {
    echo "grpcio-tools missing: pip install -r src/requirements/dev.txt" >&2; exit 1; }

rm -rf "$OUT/e39maps_api"
mkdir -p "$OUT/e39maps_api/v1"
"$PYTHON" -m grpc_tools.protoc -I "$ROOT/proto" \
    --python_out="$OUT" --pyi_out="$OUT" --grpc_python_out="$OUT" \
    "$ROOT"/proto/e39maps_api/v1/*.proto

printf '"""Generated from proto/e39maps_api (scripts/gen-proto.sh). Do not edit."""\n' \
    | tee "$OUT/e39maps_api/__init__.py" > "$OUT/e39maps_api/v1/__init__.py"
echo "Generated:"; (cd "$OUT" && find e39maps_api -type f | sort)
