import os.path, json
import logging

from .errors import NotFoundError
logger = logging.getLogger(__name__)

project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

files_path = os.path.join(project_root, "app/files")

version_path = os.path.join(project_root, "VERSION")

# The "bundled set" is the one set that ships with the application
# source — its scriptlets live under `app/<BUNDLED_SET_NAME>/` rather
# than `data/git/<set>/` and are versioned with the pyops repo, not
# committed via the inventory API. Its public-facing name is "pyops"
# (so user code imports its libraries as `#!{pyops::name}`), but the
# *role* the code checks for is "is this the bundled set?", not "is
# this named pyops?" — every user set in the project is "a pyops set"
# in the colloquial sense, so we keep the role-describing
# identifier (`is_bundled_set`, `bundled_set_path`, `BUNDLED_SET_NAME`)
# distinct from the value of the name string.
BUNDLED_SET_NAME = "pyops"
bundled_set_path = os.path.join(project_root, "app", BUNDLED_SET_NAME)


def is_bundled_set(name: str) -> bool:
    """True when `name` is the bundled (application-shipped) set.

    Callers gate write operations (inventory PUT/POST/DELETE, sets
    remove, editor save) on this — the bundled set's scriptlets are
    versioned with the source tree, not via the UI.
    """
    return name == BUNDLED_SET_NAME

def get(path: str):
    """Content of the static asset `path` (relative to `app/files/`).

    The path is resolved (symlinks, `..`, an absolute path) and must name a
    regular file inside `app/files/`; anything else is not an asset.

    Raises:
        NotFoundError: `path` does not resolve to a file inside `app/files/`.
    """
    root = os.path.realpath(files_path)
    target = None
    if "\0" not in path:
        target = os.path.realpath(os.path.join(root, path))
    if target is None or os.path.commonpath([root, target]) != root or not os.path.isfile(target):
        raise NotFoundError(f"File not found: {path}")
    with open(target, "rb") as file:
        return file.read()

def version() -> str:
    with open(version_path, "r") as file:
        return file.read().strip()

class Config:
    def __init__(self):
        self._raw_conf = None

    @classmethod
    def init(cls, conf_path: str):
        global config
        config = cls()
        with open(conf_path, "r") as file:
            config._raw_conf = json.load(file)
        logger.debug("Conf loaded successfully")
        return config

    @classmethod
    def init_from_dict(cls, raw_conf: dict):
        """Initialize the module-level singleton from an in-memory dict.

        Used by job subprocesses (`job_setup.py`, `job_runner.py`) that
        receive the already-resolved per-section dicts via JSON kwargs from
        `runner.py`. Re-applying section defaults inside `data()` /
        `runner()` / `users()` / `web()` is idempotent so the dict can be
        either the original conf file content or pre-merged sections.
        """
        global config
        config = cls()
        config._raw_conf = raw_conf
        logger.debug("Conf initialized from in-memory dict")
        return config

    def data(self):
        try:
            logger.debug("Accessing conf_data, _raw_conf type: %s", type(self._raw_conf))
            if self._raw_conf is None:
                raise ValueError("Config not initialized. Call Config.init(conf_path) first.")
            # Data section with defaults
            data = {
                "jobs": os.path.join(project_root, "data/jobs"),
                "git": os.path.join(project_root, "data/git"),
                "db": os.path.join(project_root, "data/app.db"),
            }
            if "data" in self._raw_conf:
                data.update(self._raw_conf["data"])
            logger.debug("Conf data accessed successfully")
            return data
        except Exception as e:
            logger.error("Conf data access failed: %s", e)
            raise

    def runner(self):
        try:
            logger.debug("Accessing conf_runner, _raw_conf type: %s", type(self._raw_conf))
            if self._raw_conf is None:
                raise ValueError("Config not initialized. Call Config.init(conf_path) first.")
            # Add runner section with defaults
            runner = {"git_pull_interval": 60, "drop_done_delay_seconds": 60}
            if "runner" in self._raw_conf:
                runner.update(self._raw_conf["runner"])
            logger.debug("Conf runner accessed successfully")
            return runner
        except Exception as e:
            logger.error("Conf runner access failed: %s", e)
            raise

    def users(self):
        try:
            logger.debug("Accessing conf_users, _raw_conf type: %s", type(self._raw_conf))
            if self._raw_conf is None:
                raise ValueError("Config not initialized. Call Config.init(conf_path) first.")
            users = self._raw_conf.get("users", {})
            logger.debug("Conf users accessed successfully")
            return users
        except Exception as e:
            logger.error("Conf users access failed: %s", e)
            raise

    def web(self):
        try:
            logger.debug("Accessing conf_web, _raw_conf type: %s", type(self._raw_conf))
            if self._raw_conf is None:
                raise ValueError("Config not initialized. Call Config.init(conf_path) first.")
            web = {"bind": "127.0.0.1:8000"}
            if "web" in self._raw_conf:
                web.update(self._raw_conf["web"])
            logger.debug("Conf web accessed successfully")
            return web
        except Exception as e:
            logger.error("Conf web access failed: %s", e)
            raise

    def log(self):
        try:
            logger.debug("Accessing conf_log, _raw_conf type: %s", type(self._raw_conf))
            if self._raw_conf is None:
                raise ValueError("Config not initialized. Call Config.init(conf_path) first.")
            # Log section - no file paths, only loglevel
            log = {"loglevel": "info"}
            if "log" in self._raw_conf:
                log["loglevel"] = self._raw_conf["log"].get("loglevel", "info")
            logger.debug("Conf log accessed successfully")
            return log
        except Exception as e:
            logger.error("Conf log access failed: %s", e)
            raise

    def brand(self):
        """Per-instance branding for the operator-facing chrome.

        Keys (all optional, defaults reproduce the bundled pyops look):
          • `title`   — string shown in the browser tab and in the
                        sidebar / mobile-topbar header. Default: ``"PyOps"``.
          • `icon`    — material-icons-round glyph name shown next to the
                        title. Default: ``"miscellaneous_services"``.
          • `favicon` — path to an icon file the `/favicon.ico` route
                        serves instead of the bundled default. Absolute,
                        or relative to the project root. Default: ``None``
                        (bundled `app/files/favicon.ico`).
        """
        if self._raw_conf is None:
            raise ValueError("Config not initialized. Call Config.init(conf_path) first.")
        defaults = {"title": "PyOps", "icon": "miscellaneous_services", "favicon": None}
        defaults.update(self._raw_conf.get("brand") or {})
        return defaults


config = None

def conf_data():
    global config
    if config is None:
        raise ValueError("Config not initialized. Call Config.init(conf_path) first.")
    return config.data()

def conf_runner():
    global config
    if config is None:
        raise ValueError("Config not initialized. Call Config.init(conf_path) first.")
    return config.runner()

def conf_users():
    global config
    if config is None:
        raise ValueError("Config not initialized. Call Config.init(conf_path) first.")
    return config.users()

def conf_web():
    global config
    if config is None:
        raise ValueError("Config not initialized. Call Config.init(conf_path) first.")
    return config.web()

def conf_log():
    global config
    if config is None:
        raise ValueError("Config not initialized. Call Config.init(conf_path) first.")
    return config.log()

def conf_brand():
    global config
    if config is None:
        raise ValueError("Config not initialized. Call Config.init(conf_path) first.")
    return config.brand()
