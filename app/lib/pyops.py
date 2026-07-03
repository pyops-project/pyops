# app/lib/pyops.py
import os, signal, zmq, msgpack, json
import requests as rq
import tempfile

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

# Parent kinds whose programmatically-spawned children should run
# against the working tree. These are exactly the kinds for which
# loadJob/loadLib read unstaged code (see app/runner.py:loadJob),
# so a child spawned from one of them defaults to `draft` — it picks
# up the same working-tree sources the parent is iterating on rather
# than the committed HEAD a plain `task` would read.
_DRAFT_PARENT_KINDS = ("draft", "debug", "test", "testdebug")


class JobControl:
    def __init__(self, rq: ControlRequest, kind: str = None):
        self.rq = rq
        # The kind of the job this Control belongs to (None outside a
        # job context). Drives the default child kind in `create`.
        self.parent_kind = kind

    def list(self):
        return self.rq.get("scriptlet/job/list")

    def create(self, set: str, name: str, drop_done: bool = False, max_retries: int = 0, param: str = None, debug: bool = False, user: str = None, kind: str = None):
        # `kind` resolution: an explicit argument always wins; otherwise
        # inherit from the spawning job — a working-tree parent
        # (draft/debug/test/testdebug) yields a `draft` child so it runs
        # the same uncommitted code, every other parent yields a `task`.
        # `debug` is a deprecated no-op kept for back-compat (the server
        # has no `debug` field; pass `kind="debug"` to debug a child).
        if kind is None:
            kind = "draft" if self.parent_kind in _DRAFT_PARENT_KINDS else "task"
        headers = {"X-User": user} if user else {}
        return self.rq.put("scriptlet/job/item", headers=headers, json={
            "set": set,
            "name": name,
            "drop_done": drop_done,
            "max_retries": max_retries,
            "param": param,
            "kind": kind,
            "debug": debug
        })["job"]

    def get(self, id: str):
        return self.rq.get("scriptlet/job/item", params={"id":id})
    
    def delete(self, id: str):
        return self.rq.delete("scriptlet/job/item", params={"id":id})
    
    def cancel(self, id: str):
        return self.rq.get("scriptlet/job/cancel", params={"id": id})

    def download(self, id: str):
        return self.rq.get("scriptlet/job/download", params={"id": id})

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
        return self.rq.get("scriptlet/schedule/item", params={"id": id})

    def delete(self, id: int):
        return self.rq.delete("scriptlet/schedule/item", params={"id": id})

    def activate(self, id: int):
        return self.rq.get("scriptlet/schedule/activate", params={"id": id})

    def deactivate(self, id: int):
        return self.rq.get("scriptlet/schedule/deactivate", params={"id": id})

class Control:
    def __init__(self, owner: str, web_bind: str, kind: str = None):
        self.rq = ControlRequest(owner, web_bind)
        self.job = JobControl(self.rq, kind)
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
        """Poll for pending messages and return list of (identity, message) tuples.

        If SIGTERM/SIGINT has flipped kill_now, returns a single
        ``(b"", "TERM")`` sentinel so non-blocking callers (e.g. a causal
        tick loop calling this every N ms) can react to shutdown. Without
        this, services using ``async_poll`` poll forever past SIGTERM and
        the runner has to escalate to SIGKILL after the 15s grace window.
        """
        if self.kill_now:
            return [(b"", "TERM")]
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
    """Short-lived REQ client to a service job's ServiceListener.

    Lifecycle is **caller-owned**: construct one, `request()`, then
    `close()` (or use it as a context manager). Each instance owns its
    own `zmq.Context` — a context carries a pair of background threads
    (`ZMQbg/IO`, `ZMQbg/Reaper`) backed by non-reclaimable kernel stack —
    so a client that is never closed pins those threads for the life of
    the process. The web worker creates one per controller-proxy request;
    at that rate a missing close() is a steady KernelStack/thread leak
    (~2 threads/request) that only a worker restart reclaims. `close()`
    is therefore deterministic and idempotent, and there is deliberately
    no `atexit` fallback (that was the leak-enabler: it deferred every
    per-request context's teardown to process exit).
    """

    def __init__(self, job_id: str, recv_timeout_ms: int = None):
        self.job_id = job_id
        self._closed = False

        temp_dir = tempfile.gettempdir()
        pyops_dir = os.path.join(temp_dir, "pyops")
        self.sockfile = os.path.join(pyops_dir, f"{self.job_id}.sock")

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)
        # LINGER 0 → close() returns immediately (a completed REQ/REP has
        # nothing to flush) instead of blocking teardown on delivery.
        self.socket.setsockopt(zmq.LINGER, 0)
        # Bound the blocking recv so a slow/dead service can't wedge the
        # caller — and orphan this context — forever. Left unset (infinite,
        # legacy behaviour) when no timeout is supplied.
        if recv_timeout_ms is not None and recv_timeout_ms > 0:
            self.socket.setsockopt(zmq.RCVTIMEO, recv_timeout_ms)
        self.socket.connect(f"ipc://{self.sockfile}")

    def close(self):
        """Deterministic teardown of the socket + context; reaps this
        client's ZMQbg thread pair. Closing twice is a caller bug and
        raises (zmq itself would accept it silently)."""
        if self._closed:
            raise RuntimeError(f"ServiceClient for job {self.job_id} is already closed")
        self._closed = True
        self.socket.close()
        self.context.term()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def request(self, msg):
        b = msgpack.packb(msg)
        self.socket.send(b)
        status, data = msgpack.unpackb(self.socket.recv())
        return status, data
