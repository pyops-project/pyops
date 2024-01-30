# https://docs.vultr.com/how-to-deploy-fastapi-applications-with-gunicorn-and-nginx-on-ubuntu-20-04
# https://docs.gunicorn.org/en/stable/settings.html#on-starting

import json, os, os.path
from multiprocessing import Process

from orm.database import Base, engine

import runner

with open(os.path.join("..", "etc", "infraops.json"), "r") as file:
    conf = json.load(file)

# Configurable Worker Options
bind = conf["web"]["bind"]
workers = conf["web"]["worker"]
timeout = conf["web"]["timeout"]
accesslog = os.path.join(os.path.curdir, conf["log"]["access"])
errorlog = os.path.join(os.path.curdir, conf["log"]["info"])
loglevel = conf["log"]["loglevel"]
capture_output = True

# Static Worker Options
wsgi_app = "web:app"
worker_class = "uvicorn.workers.UvicornWorker"

if not os.path.exists(os.path.join(os.path.curdir, conf["log"]["jobs"])):
    os.makedirs(os.path.join(os.path.curdir, conf["log"]["jobs"]))
p = Process(target=runner.main, kwargs={"log_base": os.path.join(os.path.curdir, conf["log"]["jobs"])})

def on_starting(server):
    Base.metadata.create_all(engine)
    
    p.start()

def on_exit(server):
    p.terminate()
    p.join()
    