#!/bin/bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

cd $DIR
gunicorn -c app/gunicorn.py --reload --access-logfile - --error-logfile - --capture-output
