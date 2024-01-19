import os.path

files_path = os.path.join(os.path.dirname(__file__), "../files")

def get(path: str):
    with open(os.path.join(files_path, path), "rb") as file:
        return file.read()