#!/usr/bin/env bash
#
# Local dev build of the pyops image.
#
# Builds for the host's native architecture only and keeps the resulting
# `:dev` tag local — does NOT push to the registry. The remote `:dev` tag
# is intentionally absent from registry.ralph.or.at; dev iterations stay
# on the developer's machine. CI's build.sh handles the published multi-arch
# build for `:main` / release tags.
#
# Why not multi-arch here: cross-arch builds on aarch64 hosts require either
# qemu-user emulation (broken with 64K kernel pages typical on DGX Spark, and
# more generally fragile) or a full qemu-system VM (slow under TCG, big
# infra). Neither is worth the cost when the only consumer of the local
# `:dev` image is the host's own docker daemon.

set -euo pipefail

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
TAG="registry.ralph.or.at/pyops/pyops:dev"

cd "$DIR"

find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

REBUILD="${1:-}"
BUILD_ARGS=( --tag "$TAG" . )
if [[ "$REBUILD" == "--rebuild" ]]; then
    BUILD_ARGS=( --no-cache --pull "${BUILD_ARGS[@]}" )
fi

docker build "${BUILD_ARGS[@]}"
