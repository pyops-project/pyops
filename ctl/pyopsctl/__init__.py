"""pyopsctl — remote-admin CLI for the pyops execution platform.

Speaks the existing pyops HTTP API and is shaped to be driven both from
an operator terminal and non-interactively by agents (LLM-driven or
otherwise) and shell scripts. The version is locked to the project-root
VERSION file so a `pyopsctl version` reports the same string the
server's `/version` endpoint does.

Two install layouts resolve `__version__`:
  - Dev checkout (install.sh symlink): VERSION sits two dirs up from
    this file at `<repo>/VERSION` — read it at import.
  - Debian package install: the package is at
    `/usr/lib/python3/dist-packages/pyopsctl/` with no `<repo>/VERSION`
    in reach, so `ctl/build.sh` bakes a sibling `_version.py` into the
    deb. We import from it first; the file read is the fallback.
"""

try:
    from ._version import __version__  # type: ignore[import-not-found]
except ImportError:
    import os
    _HERE = os.path.dirname(os.path.realpath(__file__))
    _VERSION_FILE = os.path.normpath(os.path.join(_HERE, "..", "..", "VERSION"))
    try:
        with open(_VERSION_FILE, encoding="utf-8") as _f:
            __version__ = _f.read().strip()
    except OSError:
        __version__ = "0.0.0+unknown"
