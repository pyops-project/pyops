#!/bin/bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

[ -f "$DIR/init.sh" ] && . "$DIR/init.sh"

cd $DIR
# `exec` replaces this bash with the gunicorn process. Without exec, bash
# becomes PID 1 inside the docker container, holds gunicorn as a child,
# and silently swallows the SIGTERM that `docker stop` delivers to PID 1
# — bash's default signal handling does not forward to foreground
# children. The result is that gunicorn (and therefore the runner and
# every service it owns) never sees the shutdown signal, docker waits
# out stop_grace_period, then SIGKILLs the whole container. Internal
# services then show up next start as "incomplete execution detected on
# startup". `exec` makes gunicorn itself PID 1 so SIGTERM lands where it
# can drive the graceful shutdown chain.
exec gunicorn -c app/gunicorn.py --access-logfile - --error-logfile - --capture-output