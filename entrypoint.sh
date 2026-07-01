#!/bin/bash
# PyOps container entrypoint.
#
# Runs as root so the deployment's init.sh can install packages and the
# data volume can be made writable, then drops to the unprivileged pyops
# user (uid/gid 1000) for the application itself. pyops and every scriptlet
# job it spawns therefore run non-root, while privileged start-up setup
# (package installs, volume ownership) still works.

# Abort the boot if any privileged setup step fails instead of starting
# gunicorn on a half-provisioned container. Without this a failing
# `apt install` in init.sh (sourced below) is swallowed and pyops comes
# up with the package silently missing — the failure only surfaces later
# as an import error in a job. With set -e the entrypoint exits non-zero,
# the failure is loud in the container log, and `restart:` retries it.
set -e
trap 'rc=$?; echo "entrypoint: privileged start-up failed (exit $rc) — refusing to start pyops" >&2; exit $rc' ERR

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

# Deployment hook (optional): package installs / privileged setup, sourced
# as root — the whole reason the container starts privileged. Runs under
# `set -e`, so any failing command here aborts the boot.
if [ -f "$DIR/init.sh" ]; then
    . "$DIR/init.sh"
fi

# The runtime tree must be writable by pyops; a bind-mounted data volume is
# typically root-owned on first boot. Harmless once already owned.
chown -R pyops:pyops "$DIR/data" "$DIR/etc" "$DIR/venv" 2>/dev/null || true

# setpriv changes the uid/gid but does NOT reset the environment, so root's
# HOME=/root would otherwise leak into the pyops process — and pyops
# (uid 1000) cannot write to /root, which breaks pip's cache, the git config
# and the credential helper. Point HOME (and USER/LOGNAME) at pyops's own
# home, created writable by `useradd -m` and owned by uid 1000.
export HOME=/home/pyops
export USER=pyops
export LOGNAME=pyops

# Drop privileges and hand off to run.sh, which `exec`s gunicorn so it stays
# PID 1 and receives docker's SIGTERM for the graceful shutdown chain.
exec setpriv --reuid=pyops --regid=pyops --init-groups "$DIR/run.sh"
