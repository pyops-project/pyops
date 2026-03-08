#!/usr/bin/env bash

set -euo pipefail

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
REGISTRY="registry.ralph.or.at/pyops/pyops:dev"

cd "$DIR"

find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Check if builder exists; create/bootstrap only if needed
if ! docker buildx inspect pyops-builder-dev >/dev/null 2>&1; then
    docker buildx create --name pyops-builder-dev --use
    docker buildx inspect --bootstrap pyops-builder-dev
fi

# Parse flag for full rebuild
REBUILD="${1:-}"

# Build arguments
BUILD_ARGS=(
    --provenance=false
    --platform linux/amd64,linux/arm64
    --tag "$REGISTRY"
    --push
    .
)

if [[ "$REBUILD" == "--rebuild" ]]; then
    BUILD_ARGS=( --no-cache --pull "${BUILD_ARGS[@]}" )
fi

docker buildx build "${BUILD_ARGS[@]}"