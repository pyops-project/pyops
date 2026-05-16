"""pyopsctl — remote-admin CLI for the pyops execution platform.

Speaks the existing pyops HTTP API and is shaped to be driven both from
an operator terminal and non-interactively by agents (LLM-driven or
otherwise) and shell scripts. The version is locked to the project-root
VERSION file so a `pyopsctl version` reports the same string the
server's `/version` endpoint does.
"""
import os

_HERE = os.path.dirname(os.path.realpath(__file__))
_VERSION_FILE = os.path.normpath(os.path.join(_HERE, "..", "..", "VERSION"))

try:
    with open(_VERSION_FILE, encoding="utf-8") as _f:
        __version__ = _f.read().strip()
except OSError:
    __version__ = "0.0.0+unknown"
