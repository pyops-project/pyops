#!/usr/bin/env bash

set -euo pipefail
# set -x    # uncomment during debugging

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
REGISTRY="registry.ralph.or.at/pyops/pyops"

cd "$DIR" || exit 1

if [[ $# -ge 1 && -n "$1" ]]; then
    VERSION="$1"
else
    if [[ ! -f "$DIR/VERSION" ]]; then
        echo "VERSION file not found at $DIR/VERSION" >&2
        echo "Usage: $0 [VERSION|dev] [no-pull]" >&2
        exit 1
    fi
    VERSION="$(tr -d '[:space:]' < "$DIR/VERSION")"
    if [[ -z "$VERSION" ]]; then
        echo "VERSION file is empty" >&2
        exit 1
    fi
fi

NO_CACHE_PULL="${2:-}"   # empty = normal cache/pull, non-empty = disable them

# Clean up pycache
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Prepare tags: "dev" (CI on non-main branches) pushes only :dev; a release
# version pushes tagless, :latest, :MAJOR, :MAJOR.MINOR and :VERSION
if [[ "$VERSION" == "dev" ]]; then
    TAGS=( "${REGISTRY}:dev" )
else
    TAGS=(
        "${REGISTRY}"                   # tagless
        "${REGISTRY}:latest"
        "${REGISTRY}:${VERSION%%.*}"
        "${REGISTRY}:${VERSION%.*}"
        "${REGISTRY}:${VERSION}"
    )
fi

# Buildx builder setup (idempotent)
docker buildx inspect pyops-builder >/dev/null 2>&1 || \
    docker buildx create --name pyops-builder --use --driver docker-container

docker buildx inspect --bootstrap pyops-builder

# Build arguments
BUILD_ARGS=(
    --platform linux/amd64,linux/arm64
    --builder pyops-builder
    --provenance=false
)

if [[ -z "$NO_CACHE_PULL" ]]; then
    BUILD_ARGS+=( --pull --no-cache )
fi

# Add all tags
for TAG in "${TAGS[@]}"; do
    BUILD_ARGS+=( --tag "$TAG" )
done

echo "Building and pushing the following tags:"
printf "  - %s\n" "${TAGS[@]}"

docker buildx build \
    "${BUILD_ARGS[@]}" \
    --push \
    .

# Optional: clean up builder (you may want to keep it for faster subsequent builds)
# docker buildx rm pyops-builder 2>/dev/null || true

echo
echo "Build & push completed successfully."
echo "Tags pushed:"
printf "  %s\n" "${TAGS[@]}"