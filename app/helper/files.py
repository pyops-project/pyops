import os.path, json

files_path = os.path.join(os.path.dirname(__file__), "../files")

def get(path: str):
    with open(os.path.join(files_path, path), "rb") as file:
        return file.read()

raw_conf = {}

def load_conf():
    global raw_conf
    with open(os.path.join("..", "etc", "infraops.json"), "r") as file:
        raw_conf = json.load(file)

def conf_log():
    return {
        "base": os.path.join(os.path.curdir, raw_conf["log"]["jobs"])
    } if "log" in raw_conf else {
        "base": os.path.join(os.path.curdir, "../log/jobs")
    }