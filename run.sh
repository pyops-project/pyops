#!/bin/bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

cd $DIR/app
gunicorn -k uvicorn.workers.UvicornWorker -w 4 -t 300 --bind 0.0.0.0:80 main:app