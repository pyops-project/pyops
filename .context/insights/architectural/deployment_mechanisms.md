# Deployment Mechanisms

PyOps supports containerized and direct deployment for development/production.

**Current Mechanisms**:
- **Dockerfile**: Builds Python 3.12 image, copies app/, installs deps (pip install -r requirements.txt implied), exposes 8000, runs gunicorn via app/gunicorn.py (workers=4, bind=127.0.0.1:8000, timeout=300).
- **Gunicorn Configuration** (app/gunicorn.py): Uvicorn workers for ASGI, loglevels from conf, graceful timeout.
- **Scripts**: run.sh (docker build -t pyops . && docker run -p 8000:8000 pyops), run_dev.sh (python app/web.py --reload), build.sh (docker build).
- **Scaling**: Worker count in conf.web.worker (default 4), no horizontal scaling; runner daemon runs inside container (python app/runner.py).
- **Data Persistence**: Volumes for data/, log/, etc. not specified; SQLite in data/ (bind mount needed).
