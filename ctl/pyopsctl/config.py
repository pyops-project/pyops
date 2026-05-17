"""TOML config loading for pyopsctl.

Reads `~/.config/pyops/config.toml` (overridable via `PYOPSCTL_CONFIG`
env var, mostly for tests). Enforces mode 0600 on the real config path;
broader perms produce a hard refusal because the file holds API keys.

Profile resolution chain: `--profile NAME` > `PYOPSCTL_PROFILE` env >
`default_profile` from config. `--server URL`, `--user U`,
`--api-key STRING`, `--api-key-stdin` on the command line override the
profile values for that single call.
"""
import os
import stat
import sys
import tomllib
from dataclasses import dataclass


CONFIG_ENV = "PYOPSCTL_CONFIG"
PROFILE_ENV = "PYOPSCTL_PROFILE"
DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/pyops/config.toml")
DEFAULT_X_USER = "pyopsctl"


@dataclass
class Profile:
    """Resolved per-call config.

    `api_key` and `user` may be empty strings; the client treats:
      - `api_key` truthy → Basic Auth (Authorization header).
      - `api_key` falsy  → no Authorization header, X-User sent directly
                           (falls through to DEFAULT_X_USER when `user`
                           is also empty).

    `debug_ports` is the per-profile pool of TCP ports pyopsctl may use
    when starting debug jobs (the bootstrap injected into `param` binds
    to one of these; the client leases via $XDG_RUNTIME_DIR pidfiles so
    concurrent invocations don't double-allocate the same port).
    """
    name: str
    url: str
    user: str = ""
    api_key: str = ""
    debug_ports: tuple[int, ...] = ()


def _config_path() -> str:
    return os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG_PATH


def _ensure_mode_0600(path: str) -> None:
    """Refuse to load a config file with broader-than-0600 perms.

    The file holds API keys; loosely-permissioned secrets are the kind
    of thing this CLI shouldn't silently accept. ssh does the same with
    private keys.
    """
    info = os.stat(path)
    bad_bits = info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    if bad_bits:
        raise ConfigError(
            f"{path} has mode {stat.filemode(info.st_mode)} — must be 0600 "
            f"(group/other bits set). Fix with: chmod 600 {path}"
        )


class ConfigError(Exception):
    """Config loading or resolution failure. Maps to exit code 3."""


def load(args) -> Profile:
    """Resolve the effective Profile for this invocation.

    Order of precedence:
      1. Per-call flags on `args` (--server, --user, --api-key,
         --api-key-stdin) override anything from the file.
      2. The named profile from the config file.
      3. For X-User fallback: empty `user` → DEFAULT_X_USER ("pyopsctl").

    Raises ConfigError on missing config (when needed), bad perms, or
    unknown profile.
    """
    cfg_path = _config_path()
    cfg_data: dict = {}
    profiles: dict = {}
    default_profile_name: str | None = None

    if os.path.isfile(cfg_path):
        _ensure_mode_0600(cfg_path)
        with open(cfg_path, "rb") as f:
            cfg_data = tomllib.load(f)
        profiles = cfg_data.get("profiles") or {}
        default_profile_name = cfg_data.get("default_profile")

    name = (
        getattr(args, "profile", None)
        or os.environ.get(PROFILE_ENV)
        or default_profile_name
    )

    if name is not None:
        if name not in profiles:
            raise ConfigError(
                f"profile {name!r} not found in {cfg_path} "
                f"(known: {sorted(profiles)})"
            )
        base = profiles[name]
    else:
        # No profile resolved. `--server` flag is then mandatory.
        base = {}

    url = getattr(args, "server", None) or base.get("url") or ""
    user = getattr(args, "user", None) or base.get("user") or ""
    api_key = getattr(args, "api_key", None) or base.get("api_key") or ""
    raw_ports = base.get("debug_ports") or []
    debug_ports = tuple(_validate_debug_ports(raw_ports, cfg_path))

    if getattr(args, "api_key_stdin", False):
        api_key = sys.stdin.readline().rstrip("\n")

    if not url:
        raise ConfigError(
            "no server URL — set a profile in "
            f"{cfg_path} or pass --server URL"
        )

    if api_key and url.startswith("http://"):
        print(
            f"pyopsctl: warning: sending Basic-Auth credentials over plain "
            f"HTTP ({url}) — use https:// in production",
            file=sys.stderr,
        )

    return Profile(name=name or "<no-profile>", url=url.rstrip("/"),
                   user=user, api_key=api_key, debug_ports=debug_ports)


def _validate_debug_ports(raw, cfg_path: str) -> list[int]:
    if not isinstance(raw, list):
        raise ConfigError(
            f"{cfg_path}: debug_ports must be a list of integers, "
            f"got {type(raw).__name__}"
        )
    out: list[int] = []
    for entry in raw:
        if not isinstance(entry, int) or entry < 1025 or entry > 65535:
            raise ConfigError(
                f"{cfg_path}: debug_ports entry {entry!r} is not a valid "
                f"TCP port (1025-65535)"
            )
        out.append(entry)
    return out


def redact(profile: Profile) -> dict:
    """Profile dict with secrets masked, for `pyopsctl config show`."""
    return {
        "name": profile.name,
        "url": profile.url,
        "user": profile.user,
        "api_key": "***" if profile.api_key else "",
        "auth_pathway": "basic" if profile.api_key else "x-user",
        "x_user_sent": profile.user or DEFAULT_X_USER if not profile.api_key else None,
        "debug_ports": list(profile.debug_ports),
    }
