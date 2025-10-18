#!/bin/bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

cd $DIR/app
gunicorn -c gunicorn.py --reload --access-logfile - --error-logfile - --capture-output