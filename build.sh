#!/bin/bash

set -x

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
REGISTRY="registry.ralph.or.at/infraops/infraops"

cd $DIR

find . -type d -name __pycache__ -exec rm -fr {} \;
sudo docker build --pull --no-cache -t $REGISTRY .
sudo docker push $REGISTRY