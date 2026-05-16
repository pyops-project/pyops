#!/bin/sh
# pyopsctl install / uninstall — symlinks bin/pyopsctl into the user bin
# dir so `pyopsctl` is on PATH without a venv, without pip, without root.
# The script never touches apt; distribution deps (python3, python3-requests)
# are documented as prerequisites in ctl/README.md.
set -e

HERE="$(cd "$(dirname "$0")" && pwd -P)"
SRC="$HERE/bin/pyopsctl"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
TARGET="$BIN_DIR/pyopsctl"

case "${1:-}" in
    --uninstall)
        if [ -L "$TARGET" ]; then
            rm "$TARGET"
            echo "uninstalled: removed $TARGET"
        elif [ -e "$TARGET" ]; then
            echo "refusing to remove $TARGET: not a symlink" >&2
            exit 1
        else
            echo "no symlink at $TARGET — nothing to uninstall"
        fi
        ;;
    "")
        if [ ! -x "$SRC" ]; then
            echo "launcher not found or not executable: $SRC" >&2
            exit 1
        fi
        mkdir -p "$BIN_DIR"
        ln -sfn "$SRC" "$TARGET"
        echo "installed: $TARGET -> $SRC"
        case ":$PATH:" in
            *":$BIN_DIR:"*) ;;
            *) echo "warning: $BIN_DIR is not on PATH — add it (e.g. in ~/.profile) to use pyopsctl from anywhere" >&2 ;;
        esac
        ;;
    -h|--help)
        cat <<EOF
usage: install.sh             # symlink bin/pyopsctl into \$XDG_BIN_HOME (or ~/.local/bin)
       install.sh --uninstall # remove the symlink

Prerequisites: python3 (>= 3.11 for stdlib tomllib), python3-requests.
EOF
        ;;
    *)
        echo "usage: $0 [--uninstall|--help]" >&2
        exit 2
        ;;
esac
