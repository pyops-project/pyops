# https://docs.vultr.com/how-to-deploy-fastapi-applications-with-gunicorn-and-nginx-on-ubuntu-20-04
# https://docs.gunicorn.org/en/stable/settings.html#on-starting

import sys, os, os.path, shutil
from multiprocessing import Process
import subprocess

project_root = os.path.dirname(os.path.dirname(__file__))

sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

from app.lib.files import Config, conf_web, conf_log, conf_data, conf_runner
Config.init(os.path.join(project_root, "etc", "pyops.json"))

#import debugpy
#debugpy.listen(('localhost', 5678))
#debugpy.wait_for_client()

from app.orm.database import Base, init_db
from app import runner

web_conf = conf_web()
log_conf = conf_log()
data_conf = conf_data()
runner_conf = conf_runner()

# Configurable Worker Options
bind = web_conf["bind"]
workers = web_conf["worker"]
timeout = web_conf["timeout"]
loglevel = log_conf["loglevel"]

# Static Worker Options
wsgi_app = "app.web:app"
worker_class = "uvicorn.workers.UvicornWorker"

if not os.path.exists(os.path.join(project_root, data_conf["jobs"])):
    os.makedirs(os.path.join(project_root, data_conf["jobs"]))

if not os.path.exists(os.path.join(project_root, data_conf["git"])):
    os.makedirs(os.path.join(project_root, data_conf["git"]))

venv_path = os.path.abspath(os.path.join(project_root, runner_conf["venv"]))
os.makedirs(venv_path, exist_ok=True)
pkg_path = os.path.abspath(os.path.join(project_root, runner_conf["pkg"]))
os.makedirs(pkg_path, exist_ok=True)

# Prepare isolated env for runner subprocess (manual venv activation: set VIRTUAL_ENV and prepend bin/ to PATH)
runner_env = os.environ.copy()
runner_env['VIRTUAL_ENV'] = venv_path
bin_path = os.path.join(venv_path, 'bin')
runner_env['PATH'] = f"{bin_path}:{runner_env.get('PATH', '')}"
#runner_env['SSH_AUTH_SOCK'] = os.environ.get('SSH_AUTH_SOCK')

p = Process(target=runner.main, kwargs={
    "env": runner_env,
    "data_base": os.path.join(project_root, data_conf["jobs"]),
    "web_bind": bind,
    "venv_path": venv_path,
    "pkg_path": pkg_path
})

def on_starting(server):
    from app.orm.schedule import Schedule
    from app.orm.job import Job
    init_db()
    
    p.start()

def on_exit(server):
    p.terminate()
