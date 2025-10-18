# https://docs.vultr.com/how-to-deploy-fastapi-applications-with-gunicorn-and-nginx-on-ubuntu-20-04
# https://docs.gunicorn.org/en/stable/settings.html#on-starting

import sys, json, os, os.path, shutil
from multiprocessing import Process
import subprocess

#import debugpy
#debugpy.listen(('localhost', 5678))
#debugpy.wait_for_client()

from orm.database import Base, engine

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import runner

sys.path.insert(0, os.path.join(os.getcwd(), "lib"))

with open(os.path.join("..", "etc", "pyops.json"), "r") as file:
    conf = json.load(file)

# Configurable Worker Options
bind = conf["web"]["bind"]
workers = conf["web"]["worker"]
timeout = conf["web"]["timeout"]
if "access" in conf["log"] and conf["log"]["access"]:
    accesslog = os.path.join(os.path.curdir, conf["log"]["access"])
if "info" in conf["log"] and conf["log"]["info"]:
    errorlog = os.path.join(os.path.curdir, conf["log"]["info"])
loglevel = conf["log"]["loglevel"]
capture_output = True

# Static Worker Options
wsgi_app = "app.web:app"
worker_class = "uvicorn.workers.UvicornWorker"

if not os.path.exists(os.path.join(os.path.curdir, conf["data"]["jobs"])):
    os.makedirs(os.path.join(os.path.curdir, conf["data"]["jobs"]))

venv_path = os.path.abspath(os.path.join(os.path.curdir, conf["runner"]["venv"]))
if not os.path.exists(os.path.join(venv_path, "pyvenv.cfg")):
    subprocess.check_call([sys.executable, '-m', 'venv', venv_path])
pkg_path = os.path.abspath(os.path.join(os.path.curdir, conf["runner"]["pkg"]))

# Prepare isolated env for runner subprocess (manual venv activation: set VIRTUAL_ENV and prepend bin/ to PATH)
runner_env = os.environ.copy()
runner_env['VIRTUAL_ENV'] = venv_path
bin_path = os.path.join(venv_path, 'bin')
runner_env['PATH'] = f"{bin_path}:{runner_env.get('PATH', '')}"
#runner_env['SSH_AUTH_SOCK'] = os.environ.get('SSH_AUTH_SOCK')

p = Process(target=runner.main, kwargs={
    "env": runner_env,
    "data_base": os.path.join(os.path.curdir, conf["data"]["jobs"]),
    "web_bind": bind,
    "venv_path": venv_path,
    "pkg_path": pkg_path
})

def on_starting(server):
    Base.metadata.create_all(engine)
    
    p.start()

def on_exit(server):
    p.terminate()
