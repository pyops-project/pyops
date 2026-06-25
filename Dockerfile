FROM debian:trixie-slim

RUN apt update && \
    DEBIAN_FRONTEND=noninteractive apt -y upgrade && \
    apt clean

# Python Base Requirements for PyOps
RUN DEBIAN_FRONTEND=noninteractive apt -y install \
        git python3 python3-pip python3-psutil python3-venv python3-virtualenv \
        python3-jedi python3-rich python3-requests python3-yaml \
        python3-zmq python3-msgpack python3-debugpy \
        gunicorn python3-gunicorn uvicorn python3-uvicorn python3-fastapi python3-bonsai \
        python3-sqlalchemy python3-sqlalchemy-utils python3-croniter python3-zipstream-ng && \
    apt clean

# pyops — and every scriptlet job it spawns — runs as a non-root user
# (uid/gid 1000). `-m` gives pyops a writable $HOME for the pip cache, git
# config and credential helper. The container itself still STARTS as root
# (no USER line) so entrypoint.sh can run the deployment's init.sh (package
# installs) and fix the data-volume ownership before dropping to pyops.
RUN groupadd -g 1000 pyops && \
    useradd -u 1000 -g 1000 -m -s /bin/bash pyops

RUN mkdir -p /opt/pyops/app /opt/pyops/data /opt/pyops/etc /opt/pyops/venv

COPY app /opt/pyops/app
COPY VERSION /opt/pyops/
COPY run.sh /opt/pyops/
COPY entrypoint.sh /opt/pyops/

# App code stays root-owned and read-only (a job can't rewrite pyops
# itself); only the runtime-writable tree is pyops-owned. entrypoint.sh
# re-chowns the data tree at boot, so a root-owned bind mount works with
# no manual migration.
RUN chown -R pyops:pyops /opt/pyops/data /opt/pyops/etc /opt/pyops/venv && \
    chmod +x /opt/pyops/run.sh /opt/pyops/entrypoint.sh

CMD [ "/opt/pyops/entrypoint.sh" ]
