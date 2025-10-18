# Configuration and Logging Schema

etc/pyops.json defines runtime config as JSON with sections: web (bind: "127.0.0.1:8000", worker:4, timeout:300), data (jobs: "../data/jobs", git: "../data/git"), runner (venv: "../data/venv", pkg: "../data/pkg", git_pull_interval:60), log (access: "../log/access.log", info: "../log/info.log", loglevel:"debug").

app/lib/files.py handles loading/serving via Config class:
- get(path): Reads binary from ../files/{path} (assets like CSS/JS).
- Config.load(): Lazy loads _raw_conf from ../etc/pyops.json (json.load), skips if already loaded.
- conf_data(): Lazy-loads if needed, returns data dict with defaults (jobs/git paths relative to curdir), overrides if "data" section present.
- conf_runner(): Lazy-loads, returns runner dict with default git_pull_interval=60, overrides if "runner" section.

Logging via logging.getLogger(__name__) in Config methods (debug/error); levels from conf.log.loglevel ("debug"), handlers to conf.log.access/info files (no rotation mechanisms observed; manual handling implied). Used across modules like runner.py (job states/exceptions), web.py (requests/exceptions), routers (HTTPExceptions with detail/traceback).

Mechanisms: Config class with lazy dicts for sections, defaults for missing keys.
