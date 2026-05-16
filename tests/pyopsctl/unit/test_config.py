"""Config loading + profile resolution.

Verifies:
- TOML parsing and `Profile` shape
- Resolution chain: --profile > PYOPSCTL_PROFILE env > default_profile
- 0600 enforcement (broader perms → ConfigError)
- Missing-profile error message lists known profiles
- --server / --user / --api-key per-call overrides
- HTTPS warning suppressed for http+api_key (stderr only, not in the
  exception path so the call still proceeds)
- Empty api_key → X-User fallback (and "pyopsctl" when user is also empty)
"""
import os
import stat
from types import SimpleNamespace

import pytest


def _args(**kw):
    """Build the minimal argparse-like Namespace `config.load` expects."""
    defaults = dict(
        profile=None, server=None, user=None,
        api_key=None, api_key_stdin=False,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Write a config.toml with two profiles and point PYOPSCTL_CONFIG at it.
    Caller can write additional content by appending to the returned path."""
    path = tmp_path / "config.toml"
    path.write_text(
        'default_profile = "aiops"\n'
        '\n'
        '[profiles.aiops]\n'
        'url     = "https://aiops.example"\n'
        'user    = "ralph"\n'
        'api_key = "secret"\n'
        '\n'
        '[profiles.local]\n'
        'url  = "http://localhost:8000"\n'
        # no user, no api_key
        '\n'
        '[profiles.bob]\n'
        'url  = "http://localhost:9000"\n'
        'user = "bob"\n'
    )
    path.chmod(0o600)
    monkeypatch.setenv("PYOPSCTL_CONFIG", str(path))
    monkeypatch.delenv("PYOPSCTL_PROFILE", raising=False)
    return path


def test_default_profile_resolves(config_file):
    from pyopsctl import config

    profile = config.load(_args())
    assert profile.name == "aiops"
    assert profile.url == "https://aiops.example"
    assert profile.user == "ralph"
    assert profile.api_key == "secret"


def test_flag_overrides_default_profile(config_file):
    from pyopsctl import config

    profile = config.load(_args(profile="local"))
    assert profile.name == "local"
    assert profile.url == "http://localhost:8000"
    assert profile.api_key == ""
    # Empty api_key + empty user means X-User fallback kicks in at the client layer.


def test_env_overrides_default_but_loses_to_flag(config_file, monkeypatch):
    from pyopsctl import config

    monkeypatch.setenv("PYOPSCTL_PROFILE", "bob")
    profile = config.load(_args())
    assert profile.name == "bob"

    profile = config.load(_args(profile="local"))
    assert profile.name == "local"  # flag wins


def test_unknown_profile_raises_with_helpful_list(config_file):
    from pyopsctl import config

    with pytest.raises(config.ConfigError) as exc:
        config.load(_args(profile="nope"))
    assert "nope" in str(exc.value)
    assert "aiops" in str(exc.value)  # known-profile list visible


def test_bad_perms_refuse(config_file):
    from pyopsctl import config

    config_file.chmod(0o644)
    with pytest.raises(config.ConfigError) as exc:
        config.load(_args())
    assert "0600" in str(exc.value) or "mode" in str(exc.value)


def test_per_call_overrides_take_precedence(config_file):
    from pyopsctl import config

    profile = config.load(_args(
        server="https://other.example",
        user="alice",
        api_key="aaa",
    ))
    assert profile.url == "https://other.example"
    assert profile.user == "alice"
    assert profile.api_key == "aaa"


def test_server_flag_without_profile_match_works_with_explicit_creds(monkeypatch):
    from pyopsctl import config

    monkeypatch.delenv("PYOPSCTL_CONFIG", raising=False)
    monkeypatch.delenv("PYOPSCTL_PROFILE", raising=False)
    profile = config.load(_args(server="http://localhost:9999"))
    # No config file, no profile, just --server URL — falls through with empty creds.
    assert profile.url == "http://localhost:9999"
    assert profile.user == ""
    assert profile.api_key == ""


def test_http_with_api_key_warns_on_stderr(config_file, capsys):
    """Sending Basic-Auth credentials over plain HTTP gets a stderr
    warning but doesn't fail the call (operator may be exercising local
    dev with a reverse proxy on the loopback)."""
    from pyopsctl import config

    profile = config.load(_args(profile="local", api_key="leaky"))
    captured = capsys.readouterr()
    assert "Basic-Auth" in captured.err and "http" in captured.err
    assert profile.api_key == "leaky"


def test_redact_masks_api_key(config_file):
    from pyopsctl import config

    profile = config.load(_args())
    red = config.redact(profile)
    assert red["api_key"] == "***"
    assert "secret" not in str(red)
    assert red["auth_pathway"] == "basic"


def test_redact_marks_x_user_pathway_when_no_api_key(config_file):
    from pyopsctl import config

    profile = config.load(_args(profile="local"))
    red = config.redact(profile)
    assert red["auth_pathway"] == "x-user"
    # No api_key + no user → x_user_sent defaults to "pyopsctl"
    assert red["x_user_sent"] == "pyopsctl"
