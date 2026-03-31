# app/lib/pyops.py
import os, signal, zmq, msgpack, json
import requests as rq
import tempfile
import atexit

import logging
logger = logging.getLogger(__name__)

class ControlRequest:
    def __init__(self, owner, web_bind: str):
        self.web_bind = web_bind
        self.owner = owner

    def handle_response(self, m, r):
        if r.status_code == 200:
            #print("{} status: {}".format(m, r.status_code))
            return json.loads(r.content)
        elif r.status_code == 404:
            raise Exception("{}: {} {}".format(m, r.status_code, r.reason))
        else:
            raise Exception("{}: {} {}\n{}".format(m, r.status_code, r.reason, r.content))

    def get(self, path, params = {}, headers = {}):
        headers = {**headers, "X-User": self.owner} if self.owner else headers
        r = rq.get(f"http://{self.web_bind}/{path}", params=params, headers=headers)
        return self.handle_response(f"GET {path}", r)

    def delete(self, path, params = {}, headers = {}):
        headers = {**headers, "X-User": self.owner} if self.owner else headers
        r = rq.delete(f"http://{self.web_bind}/{path}", params=params, headers=headers)
        return self.handle_response(f"DELETE {path}", r)

    def post(self, path, headers = {}, json = {}):
        headers = {**headers, "X-User": self.owner} if self.owner else headers
        r = rq.post(f"http://{self.web_bind}/{path}", headers=headers, json=json)
        return self.handle_response(f"POST {path}", r)

    def put(self, path, headers = {}, json = {}):
        headers = {**headers, "X-User": self.owner} if self.owner else headers
        r = rq.put(f"http://{self.web_bind}/{path}", headers=headers, json=json)
        return self.handle_response(f"PUT {path}", r)

class JobControl:
    def __init__(self, rq: ControlRequest):
        self.rq = rq

    def list(self):
        return self.rq.get("scriptlet/job/list")
    
    def create(self, set: str, name: str, drop_done: bool = False, max_retries: int = 0, param: str = None, debug: bool = False, user: str = None):
        headers = {"X-User": user} if user else {}
        return self.rq.put("scriptlet/job/item", headers=headers, json={
            "set": set,
            "name": name,
            "drop_done": drop_done,
            "max_retries": max_retries,
            "param": param,
            "debug": debug
        })["job"]

    def get(self, id: str):
        return self.rq.get("scriptlet/job/item", params={"id":id})
    
    def delete(self, id: str):
        return self.rq.delete("scriptlet/job/item", params={"id":id})
    
    def cancel(self, id: str):
        return self.rq.get("scriptlet/job/item", params={"id":id})
    
    def download(self, id: str):
        return self.rq.get("scriptlet/job/item", params={"id":id})

class ScheduleControl:
    def __init__(self, rq: ControlRequest):
        self.rq = rq

    def list(self):
        return self.rq.get("scriptlet/schedule/list")
    
    def create(self, set: str, name: str, drop_done: bool, schedule: str, param: str, max_retries: int = 0):
        return self.rq.put("scriptlet/schedule/item", json={
            "set": set,
            "name": name,
            "drop_done": drop_done,
            "schedule": schedule,
            "param": param,
            "max_retries": max_retries
        })["schedule"]

    def get(self, id: int):
        return self.rq.get("scriptlet/schedule/item", params={id:id})
    
    def delete(self, id: int):
        return self.rq.delete("scriptlet/schedule/item", params={id:id})
    
    def activate(self, id: int):
        return self.rq.get("scriptlet/schedule/activate", params={id:id})
    
    def deactivate(self, id: int):
        return self.rq.get("scriptlet/schedule/deactivate", params={id:id})

class Control:
    def __init__(self, owner: str, web_bind: str):
        self.rq = ControlRequest(owner, web_bind)
        self.job = JobControl(self.rq)
        self.schedule = ScheduleControl(self.rq)

    def get(self, set: str, name: str, params = {}):
        return self.rq.get(f"controller/{set}/{name}", params=params)

    def delete(self, set: str, name: str, params = {}):
        return self.rq.delete(f"controller/{set}/{name}", params=params)

    def put(self, set: str, name: str, json = {}):
        return self.rq.put(f"controller/{set}/{name}", json=json)

    def post(self, set: str, name: str, json = {}):
        return self.rq.post(f"controller/{set}/{name}", json=json)

class ServiceListener:
    def __init__(self, owner: str, job_id: str):
        self.kill_now = False
        self.owner = owner
        self.last_identity = None
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

        temp_dir = tempfile.gettempdir()
        pyops_dir = os.path.join(temp_dir, "pyops")
        os.makedirs(pyops_dir, exist_ok=True)
        self.sockfile = os.path.join(pyops_dir, f"{job_id}.sock")
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.ROUTER)
        self.socket.bind(f"ipc://{self.sockfile}")
        self.poller = zmq.Poller()
        self.poller.register(self.socket, flags=zmq.POLLIN)

    def close(self):
        if hasattr(self, 'poller'):
            try:
                self.poller.unregister(self.socket)
            except zmq.ZMQError:
                pass
            self.poller = None
        if hasattr(self, 'socket'):
            try:
                self.socket.setsockopt(zmq.LINGER, 0)
                self.socket.close()
            except Exception:
                pass
        if hasattr(self, 'context'):
            self.context.term()
        if os.path.exists(self.sockfile):
            os.remove(self.sockfile)

    def exit_gracefully(self, signum, frame):
        self.kill_now = True

    def poll(self):
        if self.kill_now:
            return "TERM"
        events = self.poller.poll(timeout=0)
        if events:
            self.last_identity = self.socket.recv()
            self.socket.recv()  # empty frame
            return msgpack.unpackb(self.socket.recv())
        return None

    def listen(self):
        while not self.poller.poll(timeout=1000) and not self.kill_now:
            pass
        if not self.kill_now:
            self.last_identity = self.socket.recv()
            self.socket.recv()  # empty frame
            return msgpack.unpackb(self.socket.recv())
        else:
            return "TERM"

    def response(self, msg, status: int = 200):
        if self.last_identity is not None:
            b = msgpack.packb([status, msg])
            self.socket.send(self.last_identity, zmq.SNDMORE)
            self.socket.send(b'', zmq.SNDMORE)
            self.socket.send(b)
            self.last_identity = None

    def async_poll(self):
        """Poll for pending messages and return list of (identity, message) tuples."""
        messages = []
        while True:
            events = self.poller.poll(timeout=0)
            if not events:
                break
            identity = self.socket.recv()
            self.socket.recv()  # empty frame
            message = msgpack.unpackb(self.socket.recv())
            messages.append((identity, message))
        return messages

    def async_response(self, identity, msg, status: int = 200):
        """Send response to a specific client identity."""
        b = msgpack.packb([status, msg])
        self.socket.send(identity, zmq.SNDMORE)
        self.socket.send(b'', zmq.SNDMORE)
        self.socket.send(b)

class ServiceClient:
    def __init__(self, job_id: str):
        self.job_id = job_id

        temp_dir = tempfile.gettempdir()
        pyops_dir = os.path.join(temp_dir, "pyops")
        self.sockfile = os.path.join(pyops_dir, f"{self.job_id}.sock")

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)
        self.socket.connect(f"ipc://{self.sockfile}")
        atexit.register(self.close)

    def close(self):
        if hasattr(self, 'socket'):
            self.socket.close()
        if hasattr(self, 'context'):
            self.context.term()

    def request(self, msg):
        b = msgpack.packb(msg)
        self.socket.send(b)
        status, data = msgpack.unpackb(self.socket.recv())
        return status, data
