#!/usr/bin/env bash

set -euo pipefail
# set -x    # uncomment during debugging

if [[ $# -lt 1 || -z "$1" ]]; then
    echo "Usage: $0 VERSION [no-pull]"
    echo "  VERSION       : required, e.g. 1.2.3"
    echo "  no-pull       : optional, when present disables --pull and --no-cache"
    echo
    exit 1
fi

VERSION="$1"
NO_CACHE_PULL="${2:-}"   # empty = normal cache/pull, non-empty = disable them

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
REGISTRY="registry.ralph.or.at/pyops/pyops"

cd "$DIR" || exit 1

# Clean up pycache
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Prepare tags
TAG_FULL="${REGISTRY}:${VERSION}"
TAG_MINOR="${REGISTRY}:${VERSION%.*}"
TAG_MAJOR="${REGISTRY}:${VERSION%%.*}"
TAG_LATEST="${REGISTRY}:latest"
TAG_DEFAULT="${REGISTRY}"               # tagless

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
BUILD_ARGS+=( --tag "$TAG_DEFAULT" )
BUILD_ARGS+=( --tag "$TAG_LATEST" )
BUILD_ARGS+=( --tag "$TAG_MAJOR" )
BUILD_ARGS+=( --tag "$TAG_MINOR" )
BUILD_ARGS+=( --tag "$TAG_FULL" )

echo "Building and pushing the following tags:"
printf "  - %s\n" "$TAG_DEFAULT" "$TAG_LATEST" "$TAG_MAJOR" "$TAG_MINOR" "$TAG_FULL"

docker buildx build \
    "${BUILD_ARGS[@]}" \
    --push \
    .

# Optional: clean up builder (you may want to keep it for faster subsequent builds)
# docker buildx rm pyops-builder 2>/dev/null || true

echo
echo "Build & push completed successfully."
echo "Tags pushed:"
echo "  ${TAG_DEFAULT}"
echo "  ${TAG_LATEST}"
echo "  ${TAG_MAJOR}"
echo "  ${TAG_MINOR}"
echo "  ${TAG_FULL}"