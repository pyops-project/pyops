# ZMQ IPC Examples Patterns

.pyops demonstrates ZMQ usage in scriptlets, focusing on service types with persistent listeners for command processing. Patterns in task/service files use pyops.ServiceListener for REP socket IPC.

**Core Pattern (Service Listeners)**:
- Services: Class {Name}Service(__init__(self, listener)): self.listener = listener. listen(): while not self.listener.kill_now: msg = self.listener.listen() (polls 1s or TERM), process msg (json.loads if "cmd" in msg, else malformed), match case "cmd": self.listener.response(result) (msgpack via response), else unknown/broken with json.dumps/traceback. Handles exceptions with "broken message" + traceback.
- Commands: Match case on "cmd" for operations (e.g., system control, status queries, actions like stop/move/turn, data responses like images/base64), with specific cmd_ methods, default unknown json.
- Integration: In controller.py: service="{ServiceName}", debug=True. In task/service.py: s = {Service}(listener), s.listen() (infinite loop until TERM).

**Mechanisms**:
- Listener: pyops.ServiceListener(job_id) (ZMQ REP ipc://{temp/pyops/{id}.sock}, poller, signals SIGINT/TERM set kill_now, atexit remove sock).
- Client: From web.py /q: pyops.ServiceClient(set, service, debug, owner), request(msg) (REQ send msgpack, recv unpack).
- Patterns: REQ/REP for request-response (client sends cmd/data, service processes/responses), no pub/sub/async. Services persistent (kind="service", auto-restart <3 fails), debug mode unstaged code. Examples show simple cmd dispatch (match case), json/msgpack serialization, error responses with dumps/traceback. No multi-client (single REP), no auth beyond owner in Job.

Emphasizes lightweight IPC for operations, but fixed polling (1s), no timeouts/retries, potential blocking on listen.
