import os, zmq, msgpack, json
import requests as rq
from lib import runner, files

class ControlRequest:
    def __init__(self, web_bind: str):
        self.web_bind = web_bind

    def handle_response(self, m, r):
        if r.status_code == 200:
            #print("{} status: {}".format(m, r.status_code))
            return json.loads(r.content)
        elif r.status_code == 404:
            raise Exception("{}: {} {}".format(m, r.status_code, r.reason))
        else:
            raise Exception("{}: {} {}\n{}".format(m, r.status_code, r.reason, r.content))

    def get(self, path, params = {}, headers = {}):
        r = rq.get(f"http://{self.web_bind}/{path}", params=params, headers=headers)
        return self.handle_response(f"GET {path}", r)

    def delete(self, path, params = {}, headers = {}):
        r = rq.delete(f"http://{self.web_bind}/{path}", params=params, headers=headers)
        return self.handle_response(f"DELETE {path}", r)
    
    def post(self, path, headers = {}, json = {}):
        r = rq.post(f"http://{self.web_bind}/{path}", headers=headers, json=json)
        return self.handle_response(f"POST {path}", r)
    
    def put(self, path, headers = {}, json = {}):
        r = rq.put(f"http://{self.web_bind}/{path}", headers=headers, json=json)
        return self.handle_response(f"PUT {path}", r)

class JobControl:
    def __init__(self, rq: ControlRequest):
        self.rq = rq

    def list(self):
        return self.rq.get("scriptlet/job/list")
    
    def create(self, group: str, name: str, drop_done: bool, param: str, debug: bool):
        return self.rq.put("scriptlet/job/item", json={
            "group": group,
            "name": name,
            "drop_done": drop_done,
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
    
    def create(self, group: str, name: str, drop_done: bool, schedule: str, param: str):
        return self.rq.put("scriptlet/schedule/item", json={
            "group": group,
            "name": name,
            "drop_done": drop_done,
            "schedule": schedule,
            "param": param
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
    def __init__(self, web_bind: str):
        self.rq = ControlRequest(web_bind)
        self.job = JobControl(self.rq)
        self.schedule = ScheduleControl(self.rq)

    def get(self, group: str, name: str, params = {}):
        return self.rq.get(f"controller/{group}/{name}/get", params=params)
    
    def delete(self, group: str, name: str, params = {}):
        return self.rq.get(f"controller/{group}/{name}/delete", params=params)
    
    def put(self, group: str, name: str, json = {}):
        return self.rq.get(f"controller/{group}/{name}/put", json=json)
    
    def post(self, group: str, name: str, json = {}):
        return self.rq.get(f"controller/{group}/{name}/post", json=json)

class ServiceListener:
    def __init__(self, sockfile):
        self.sockfile = os.path.abspath(sockfile)
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REP)
        self.socket.bind(f"ipc://{self.sockfile}")

    def listen(self):
        return msgpack.unpackb(self.socket.recv())

    def response(self, msg):
        b = msgpack.packb(msg)
        self.socket.send(b)

class ServiceClient:
    def __init__(self, group: str, name: str, debug: bool = False):
        owner = "TODO"
        jobs = runner.find(owner, group, name, "debug" if debug else "service", ["running"])

        if not jobs:
            raise Exception(f"No running service: {group} {name}")
        else:
            self.job = jobs[0]

            data_base = files.conf_data()["jobs"]
            self.sockfile = os.path.abspath(os.path.join(data_base, f"{self.job}.sock"))

            self.context = zmq.Context()
            self.socket = self.context.socket(zmq.REQ)
            self.socket.connect(f"ipc://{self.sockfile}")

    def request(self, msg):
        b = msgpack.packb(msg)
        self.socket.send(b)
        return msgpack.unpackb(self.socket.recv())
