import os.path

sites_path = os.path.join(os.path.dirname(__file__), "../sites")

def get(path: str):
    with open(os.path.join(sites_path, path+".html"), "r") as file:
        return file.read()