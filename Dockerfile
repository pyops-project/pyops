FROM debian:bookworm-slim

RUN apt update && \
    DEBIAN_FRONTEND=noninteractive apt -y upgrade && \
    apt clean

RUN apt update && \
    DEBIAN_FRONTEND=noninteractive apt -y upgrade && \
    apt clean

# Python Base Requirements
RUN DEBIAN_FRONTEND=noninteractive apt -y install \
        python3-dev python3-full \
        gunicorn python3-gunicorn uvicorn python3-uvicorn python3-fastapi python3-bonsai \
        python3-sqlalchemy python3-sqlalchemy-utils && \
    apt clean

# Python Connectivity Modules
RUN DEBIAN_FRONTEND=noninteractive apt -y install \
        python3-mysqldb python3-psycopg2 python3-oracledb python3-pyodbc python3-pymssql \
        python3-paramiko python3-proxmoxer python3-pyzabbix python3-protobix python3-pyfg \
        python3-requests python3-requestsexceptions python3-requests-futures python3-requests-kerberos python3-requests-ntlm python3-requests-toolbelt \
        python3-requests-cache python3-requests-file python3-requests-oauthlib python3-requests-unixsocket && \
    apt clean

# Python Data Modules
RUN DEBIAN_FRONTEND=noninteractive apt -y install \
        python3-pandas python3-numpy python3-scipy \
        python3-pattern python3-docx python3-xlsxwriter python3-pypdf2 \
        python3-zipp python3-zipstream-ng \
        python3-croniter && \
    apt clean

RUN DEBIAN_FRONTEND=noninteractive apt -y install pwgen && \
    apt clean

RUN mkdir -p /opt/infraops/app && mkdir -p /opt/infraops/data

COPY app /opt/infraops/app
COPY run.sh /opt/infraops/

CMD [ "/opt/infraops/run.sh" ]