# PYTHON_ARGCOMPLETE_OK
"""pyopsctl entry point.

argparse-based dispatch. Each command group is a module under
`pyopsctl.commands` that exposes `register(subparsers, parent_flags)`.
The parent_flags parser carries the global options every command
shares (`--profile`, `--server`, `--user`, `--api-key`, `--api-key-stdin`,
`--json`, `--quiet`, `--timeout`) — keeping them on a parent parser
means each subcommand inherits them without re-declaring.

The leading `PYTHON_ARGCOMPLETE_OK` marker lets argcomplete's global
completion hook recognise this script as completion-enabled without
needing per-command registration. The actual hook (autocomplete call)
runs in `main()` before `parse_args` — gated by ImportError so hosts
without `python3-argcomplete` installed still run normally, just
without tab completion.
"""
import argparse
import sys

from pyopsctl import __version__
from pyopsctl.commands import scriptlet, job, debug, monitoring, config as config_cmd
from pyopsctl.output import emit_error
from pyopsctl.client import HTTPError, ConfigError


def _build_parent_flags() -> argparse.ArgumentParser:
    """The flag set every command inherits. Defined once as a parent
    parser so each subparser picks them up via `parents=[…]`."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--profile", metavar="NAME",
                   help="config profile to use (overrides PYOPSCTL_PROFILE and config.default_profile)")
    p.add_argument("--server", metavar="URL",
                   help="server URL; overrides the profile's url")
    p.add_argument("--user", metavar="U",
                   help="X-User / Basic-Auth username; overrides the profile's user")
    p.add_argument("--api-key", metavar="STRING",
                   help="API key for Basic Auth; overrides the profile's api_key (and triggers Basic-Auth path)")
    p.add_argument("--api-key-stdin", action="store_true",
                   help="read the API key from stdin (one line, trailing newline stripped)")
    p.add_argument("--json", dest="json_mode", action="store_true",
                   help="emit JSON to stdout; structured errors to stderr")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="emit IDs only (one per line), suitable for shell piping")
    p.add_argument("--timeout", type=float, default=30.0, metavar="SECS",
                   help="HTTP request timeout in seconds (default: 30)")
    return p


def _build_parser() -> argparse.ArgumentParser:
    parents = [_build_parent_flags()]
    parser = argparse.ArgumentParser(
        prog="pyopsctl",
        description="Remote-admin CLI for pyops.",
    )
    parser.add_argument("--version", action="version", version=f"pyopsctl {__version__}")
    subparsers = parser.add_subparsers(dest="group", required=True, metavar="GROUP")

    scriptlet.register(subparsers, parents)
    job.register(subparsers, parents)
    debug.register(subparsers, parents)
    monitoring.register(subparsers, parents)
    config_cmd.register(subparsers, parents)

    return parser


def main(argv: list | None = None) -> int:
    parser = _build_parser()
    try:
        import argcomplete
        argcomplete.autocomplete(parser)
    except ImportError:
        pass
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as ex:
        emit_error(args, str(ex), status=None)
        return 3
    except HTTPError as ex:
        emit_error(args, ex.message, status=ex.status)
        return 4 if 400 <= ex.status < 500 else 5
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
