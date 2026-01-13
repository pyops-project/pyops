# https://docs.vultr.com/how-to-deploy-fastapi-applications-with-gunicorn-and-nginx-on-ubuntu-20-04
# https://docs.gunicorn.org/en/stable/settings.html#on-starting

import sys, json, os, os.path, shutil
from multiprocessing import Process
import subprocess

project_root = os.path.dirname(os.path.dirname(__file__))

sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

from app.lib.files import Config
Config.init(os.path.join(project_root, "etc", "pyops.json"))

#import debugpy
#debugpy.listen(('localhost', 5678))
#debugpy.wait_for_client()

from app.orm.database import Base, init_db
from app import runner

with open(os.path.join(project_root, "etc", "pyops.json"), "r") as file:
    conf = json.load(file)

# Configurable Worker Options
bind = conf["web"]["bind"]
workers = conf["web"]["worker"]
timeout = conf["web"]["timeout"]
if "access" in conf.get("log", {}) and conf["log"].get("access"):
    access_path = os.path.join(project_root, conf["log"]["access"])
    os.makedirs(os.path.dirname(access_path), exist_ok=True)
    accesslog = access_path
if "info" in conf.get("log", {}) and conf["log"].get("info"):
    error_path = os.path.join(project_root, conf["log"]["info"])
    os.makedirs(os.path.dirname(error_path), exist_ok=True)
    errorlog = error_path
loglevel = conf["log"]["loglevel"]
capture_output = True

# Static Worker Options
wsgi_app = "app.web:app"
worker_class = "uvicorn.workers.UvicornWorker"

if not os.path.exists(os.path.join(project_root, conf["data"]["jobs"])):
    os.makedirs(os.path.join(project_root, conf["data"]["jobs"]))

if not os.path.exists(os.path.join(project_root, conf["data"]["git"])):
    os.makedirs(os.path.join(project_root, conf["data"]["git"]))

venv_path = os.path.abspath(os.path.join(project_root, conf["runner"]["venv"]))
if not os.path.exists(os.path.join(venv_path, "pyvenv.cfg")):
    subprocess.check_call([sys.executable, '-m', 'venv', venv_path])
pkg_path = os.path.abspath(os.path.join(project_root, conf["runner"]["pkg"]))

# Prepare isolated env for runner subprocess (manual venv activation: set VIRTUAL_ENV and prepend bin/ to PATH)
runner_env = os.environ.copy()
runner_env['VIRTUAL_ENV'] = venv_path
bin_path = os.path.join(venv_path, 'bin')
runner_env['PATH'] = f"{bin_path}:{runner_env.get('PATH', '')}"
#runner_env['SSH_AUTH_SOCK'] = os.environ.get('SSH_AUTH_SOCK')

p = Process(target=runner.main, kwargs={
    "env": runner_env,
    "data_base": os.path.join(project_root, conf["data"]["jobs"]),
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
