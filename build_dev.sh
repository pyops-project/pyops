#!/bin/bash

set -x

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
REGISTRY="registry.ralph.or.at/pyops/pyops:dev"

cd $DIR

find . -type d -name __pycache__ -exec rm -fr {} \; > /dev/null 2>&1

# Ensure Buildx builder exists (run once, or add --bootstrap if needed)
docker buildx create --use --name pyops-builder-dev || true
docker buildx inspect --bootstrap

docker buildx build \
  --provenance=false \
  --platform linux/amd64,linux/arm64 \
  --tag "$REGISTRY" \
  --push .