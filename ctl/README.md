# pyopsctl

Remote-admin CLI for the pyops execution platform. Speaks the existing
pyops HTTP API; nothing on the server side is changed by the presence
or absence of pyopsctl.

## Prerequisites

- `python3` (>= 3.11 for stdlib `tomllib`)
- `python3-requests` (distro package, not pip)

On Debian/Ubuntu:

```
sudo apt install python3 python3-requests
```

No venv. No pip install of pyopsctl itself.

## Install

```
./install.sh
```

Creates a symlink at `$XDG_BIN_HOME/pyopsctl` (default `~/.local/bin/pyopsctl`)
pointing at this checkout's `bin/pyopsctl`. Idempotent — re-running
replaces the symlink. Warns if the bin dir isn't on `$PATH`.

To remove:

```
./install.sh --uninstall
```

## Configure

Drop a `~/.config/pyops/config.toml` with one or more profiles. Mode 0600.

```toml
default_profile = "aiops"

[profiles.aiops]
url     = "https://aiops.ralph.or.at"
user    = "ralph"
api_key = "..."             # used as Basic-Auth password

[profiles.local]
url  = "http://localhost:8000"
# no api_key → no Basic Auth, X-User sent directly
# user omitted → X-User defaults to "pyopsctl"
```

## Use

See `docs/pyops_guide.md` § pyopsctl in the project root for the full command reference.
Quick smoke check:

```
pyopsctl config check
pyopsctl version
```
