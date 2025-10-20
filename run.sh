#!/bin/bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

[ -f "$DIR/init.sh" ] && . "$DIR/init.sh"

cd $DIR
gunicorn -c app/gunicorn.py --access-logfile - --error-logfile - --capture-output