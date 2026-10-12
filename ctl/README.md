# pyopsctl

Remote-admin CLI for the pyops execution platform. Speaks the existing
pyops HTTP API; nothing on the server side is changed by the presence
or absence of pyopsctl.

## Prerequisites

- `python3` (>= 3.11 for stdlib `tomllib`)
- `python3-requests` (distro package, not pip)
- `python3-argcomplete` *(optional — enables zsh tab completion)*

On Debian/Ubuntu:

```
sudo apt install python3 python3-requests python3-argcomplete
```

No venv. No pip install of pyopsctl itself.

## Install — .deb (Ubuntu 24.04+ / Debian 13+)

Each release publishes `pyopsctl_<version>_all.deb` as a Generic
Package on the GitLab project. Install it with:

```
sudo apt install ./pyopsctl_<version>_all.deb
```

apt pulls `python3-requests` (required) and `python3-argcomplete`
(recommended) automatically. The deb drops files at apt-managed
system paths — completion works out of the box, no rc edits.

Build the deb locally (rarely needed; CI handles this for releases):

```
./build.sh
```

Produces `ctl/dist/pyopsctl_<version>_all.deb`. Requires `dpkg-dev`
and `python3-argcomplete` on the build host.

## Install — checkout (development / no apt)

```
./install.sh
```

Creates a symlink at `$XDG_BIN_HOME/pyopsctl` (default `~/.local/bin/pyopsctl`)
pointing at this checkout's `bin/pyopsctl`. Idempotent — re-running
replaces the symlink. Warns if the bin dir isn't on `$PATH`.

If `python3-argcomplete` is installed, the script also drops a zsh
completion file at `$XDG_DATA_HOME/zsh/site-functions/_pyopsctl`
(default `~/.local/share/zsh/site-functions/_pyopsctl`) and adds a
marker-tagged `fpath=(…)` line to `~/.zshrc` — `compinit` picks it up
on next shell start.

To remove (symlink + completion + the zshrc line):

```
./install.sh --uninstall
```

## Configure

Drop a `~/.config/pyops/config.toml` with one or more profiles. Mode 0600.
The VSIX extension reads the same file (its `pyops.profile` setting picks
the profile), so one profile serves both clients.

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
