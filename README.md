# PyOps
Execution Platform for Python Operations Scriptlets

## Architecture
![Architecture](doc/architecture.png)

## Configuration
### Settings
Example: [etc/pyops.json](etc/pyops.json)
- Not providing log paths logs to stdout/stderr
- Not providing a jobs data path defaults to "app/../data/jobs"
- PyOps does not Provide SSL by itself, you should use a reverse proxy.

### Authentication
At the moment PyOps provides no authentication method.
Implementing an authentication method at reverse proxy level is strongly advised for public systems.

## Scriptlets
### Types
#### Task
One time executed, usually for debugging.

#### Schedule
Cron like scheduling of tasks.

#### Service
Not timed schedule which is kept running unless there are 3 fails or a user cancel.

A service should consist of a main loop.
This main loop can be a [websocket client](https://github.com/websocket-client/websocket-client) receiving messages from FastAPI controller.

#### Controller


## Setup
### Direct
```
git clone https://git.ralph.or.at/pyops/pyops.git
cd pyops
./run.sh
```

### SystemD
- TODO

### Docker compose
- `docker-compose.yml`
```
networks:
  default:
    driver: bridge

services:
  app:
    container_name: pyops_app.SOMEDOMAIN
    image: 'registry.ralph.or.at/pyops/pyops:latest'
    restart: unless-stopped
    networks:
      - default
    volumes:
      - './volumes/app/data:/opt/pyops/data'
      - './volumes/app/etc:/opt/pyops/etc'
      - './volumes/app/venv:/opt/pyops/venv'
  nginx:
    container_name: pyops.SOMEDOMAIN
    image: 'nginx:1.25'
    restart: unless-stopped
    ports:
      - '443:443'
    networks:
      - default
    depends_on:
      - app
    volumes:
      - './volumes/nginx/conf.d:/etc/nginx/conf.d:ro'
      - './volumes/nginx/auth:/etc/nginx/auth:ro'
      - '/etc/ssl/private:/etc/nginx/ssl:ro'
      - './volumes/nginx/log:/var/log/nginx:rw'
```
:exclamation: instead of using original nginx container as reverse proxy you could use [jonasal/nginx-certbot](https://hub.docker.com/r/jonasal/nginx-certbot) for using LetsEncrypt certificates.

- `./volumes/app/etc/pyops.json`
```
{
    "web": {
        "bind": "0.0.0.0:80",
        "worker": 4,
        "timeout": 300
    },
    "data": {
        "jobs": "../data/jobs"
    },
    "log": {
        "access": "../log/access.log",
        "info": "../log/info.log",
        "loglevel": "debug"
    }
}
```
:exclamation: note, that there are no logfiles set, logging shall be done via `docker logs`

- `./volumes/nginx/conf.d/app.conf`
```
server {
	listen 443 ssl;
	server_name pyops.SOMEDOMAIN;

        ssl_certificate /etc/nginx/ssl/SOMECERT.crt;
        ssl_certificate_key /etc/nginx/ssl/SOMEKEY.key;
	    ssl_protocols       TLSv1.2 TLSv1.3;
        ssl_ciphers         HIGH:!aNULL:!MD5;
        add_header Strict-Transport-Security max-age=63072000;

        access_log /var/log/nginx/app_access.log;
	    error_log /var/log/nginx/app_error.log;

        location / {
                auth_basic           "PyOps";
                auth_basic_user_file /etc/nginx/auth/htpasswd; 

                proxy_pass http://pyops_app.SOMEDOMAIN;
                proxy_set_header X-Real-IP $remote_addr;
                proxy_set_header Host pyops.SOMEDOMAIN;
                proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                proxy_set_header X-Protocol https;
                proxy_set_header X-Original-Host $host;
                proxy_read_timeout 300;
                proxy_connect_timeout 120;
                proxy_send_timeout 300;
        }
}
```

- create `./volumes/nginx/auth/htpasswd`
```bash
htpasswd -c ./volumes/nginx/auth/htpasswd SOMEUSER
```
