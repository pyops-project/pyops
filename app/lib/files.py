import os.path, json
import logging
logger = logging.getLogger(__name__)

project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

files_path = os.path.join(project_root, "app/files")

def get(path: str):
    with open(os.path.join(files_path, path), "rb") as file:
        return file.read()

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

    def log(self):
        try:
            logger.debug("Accessing conf_log, _raw_conf type: %s", type(self._raw_conf))
            if self._raw_conf is None:
                raise ValueError("Config not initialized. Call Config.init(conf_path) first.")
            # Log section with conditional paths
            log = {"loglevel": "info"}
            if "log" in self._raw_conf:
                if "access" in self._raw_conf["log"]:
                    log["access"] = self._raw_conf["log"]["access"]
                if "info" in self._raw_conf["log"]:
                    log["info"] = self._raw_conf["log"]["info"]
                log["loglevel"] = self._raw_conf["log"].get("loglevel", "info")
            logger.debug("Conf log accessed successfully")
            return log
        except Exception as e:
            logger.error("Conf log access failed: %s", e)
            raise

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

def conf_log():
    global config
    if config is None:
        raise ValueError("Config not initialized. Call Config.init(conf_path) first.")
    return config.log()
