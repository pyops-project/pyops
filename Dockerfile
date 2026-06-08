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

RUN mkdir -p /opt/pyops/app && mkdir -p /opt/pyops/data && mkdir -p /opt/pyops/etc && mkdir -p /opt/pyops/venv

COPY app /opt/pyops/app
COPY VERSION /opt/pyops/
COPY run.sh /opt/pyops/

CMD [ "/opt/pyops/run.sh" ]
