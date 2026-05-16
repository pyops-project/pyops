"""Smoke tests for pyopsctl talking to a real pyops server.

Exercises the cheap read-only commands so a profile + server fixture +
launcher integration is confirmed end-to-end before the load-bearing
debug flow runs.
"""
import json


def test_version_command_reports_server_and_cli(pyopsctl_bin, run_ctl, pyops_server_for_ctl):
    result = run_ctl(pyopsctl_bin, "version", "--json")
    payload = json.loads(result.stdout.strip())
    # Both versions should be the same VERSION file at the moment.
    assert payload["server"]
    assert payload["cli"] == payload["server"]


def test_config_check_reports_auth_pathway_x_user_for_no_api_key(pyopsctl_bin, run_ctl, pyops_server_for_ctl):
    result = run_ctl(pyopsctl_bin, "config", "check", "--json")
    payload = json.loads(result.stdout.strip())
    assert payload["reachable"] is True
    assert payload["auth_pathway"] == "x-user"
    assert payload["server_version"]


def test_config_show_redacts_api_key(pyopsctl_bin, run_ctl, pyops_server_for_ctl, tmp_path, monkeypatch):
    """When a profile has an api_key, `config show` must mask it. The
    integration server doesn't need to be hit for this — it's a local
    config inspection."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'default_profile = "x"\n[profiles.x]\nurl = "https://example"\n'
        'user = "alice"\napi_key = "topsecret-key-leak-test"\n'
    )
    cfg.chmod(0o600)
    monkeypatch.setenv("PYOPSCTL_CONFIG", str(cfg))
    result = run_ctl(pyopsctl_bin, "config", "show", "--json")
    payload = json.loads(result.stdout.strip())
    assert payload["auth_pathway"] == "basic"
    assert payload["api_key"] == "***"
    assert "topsecret-key-leak-test" not in result.stdout


def test_health_returns_status(pyopsctl_bin, run_ctl, pyops_server_for_ctl):
    result = run_ctl(pyopsctl_bin, "health", "--json")
    payload = json.loads(result.stdout.strip())
    assert "status" in payload


def test_metrics_returns_prometheus_text(pyopsctl_bin, run_ctl, pyops_server_for_ctl):
    result = run_ctl(pyopsctl_bin, "metrics")
    # Prometheus exposition has `# HELP` or `# TYPE` lines or `pyops_` metrics
    assert "pyops_" in result.stdout or "# HELP" in result.stdout
