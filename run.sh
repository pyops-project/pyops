#!/bin/bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

[ -f "$DIR/init.sh" ] && . "$DIR/init.sh"

cd $DIR/app
gunicorn -c gunicorn.py --access-logfile - --error-logfile - --capture-output