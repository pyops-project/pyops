#!/bin/bash

set -x

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
REGISTRY="registry.ralph.or.at/pyops/pyops:dev"

cd $DIR

find . -type d -name __pycache__ -exec rm -fr {} \; > /dev/null 2>&1
docker build --pull --no-cache -t $REGISTRY .
docker push $REGISTRY
