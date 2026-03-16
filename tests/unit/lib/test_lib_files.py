import pytest
import os
import json
from unittest.mock import patch, mock_open
from app.lib import files


def test_init_valid_json(mock_conf):
    # mock_conf calls Config.init with valid temp json
    data = files.conf_data()
    runner = files.conf_runner()
    assert "jobs" in data
    assert "git_pull_interval" in runner

def test_init_invalid_json(tmp_path):
    from app.lib.files import Config
    invalid_content = '{invalid json'
    conf_path = tmp_path / "invalid.json"
    with open(conf_path, "w") as f:
        f.write(invalid_content)
    with pytest.raises(json.JSONDecodeError):
        Config.init(str(conf_path))

def test_init_file_not_found():
    from app.lib.files import Config
    with patch('builtins.open', side_effect=FileNotFoundError):
        with pytest.raises(FileNotFoundError):
            Config.init("/nonexistent.json")

def test_conf_data_no_init_when_loaded(mock_conf, tmp_path):
    # mock_conf inits, so conf_data uses it without re-init
    with patch('app.lib.files.Config.init') as mock_init:
        result = files.conf_data()
        mock_init.assert_not_called()
        
        expected = {
            "pool_size": 2,
            "db": str(tmp_path / "test.db"),
            "jobs": str(tmp_path / "data" / "jobs"),
            "git": str(tmp_path / "data" / "git"),
        }
        assert result == expected

def test_conf_data_with_override(mock_conf):
    # mock_conf sets base, test override via update
    result = files.conf_data()
    assert "jobs" in result

def test_conf_data_init_on_empty(tmp_path):
    from app.lib import files
    from app.lib.files import Config
    mock_content = '{"data": {"jobs": "/test/jobs"}}'
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    Config.init(str(conf_path))
    result = files.conf_data()
    expected = {
        "db": os.path.join(files.project_root, "data/app.db"),
        "jobs": "/test/jobs",
        "git": os.path.join(files.project_root, "data/git"),
    }
    assert result == expected

def test_conf_runner_no_init_when_loaded(mock_conf, tmp_path):
    with patch('app.lib.files.Config.init') as mock_init:
        result = files.conf_runner()
        mock_init.assert_not_called()
        expected = {
            "git_pull_interval": 60,
            "drop_done_delay_seconds": 60,
            "venv": str(tmp_path / "data" / "venv"),
            "pkg": str(tmp_path / "data" / "pkg")
        }
        assert result == expected

def test_conf_runner_with_override(mock_conf):
    result = files.conf_runner()
    assert "git_pull_interval" in result

def test_conf_runner_init_on_empty(tmp_path):
    from app.lib.files import Config
    mock_content = '{"runner": {"git_pull_interval": 30}}'
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    Config.init(str(conf_path))
    result = files.conf_runner()
    expected = {"git_pull_interval": 30, "drop_done_delay_seconds": 60}
    assert result == expected

def test_get_file_not_found():
    with patch('builtins.open', side_effect=FileNotFoundError):
        with pytest.raises(FileNotFoundError):
            files.get("nonexistent/path")

def test_get_valid_path():
    content = b"test content"
    with patch('builtins.open', mock_open(read_data=content)):
        result = files.get("test/path")
        assert result == content


def test_conf_data_idempotent(mock_conf):
    with patch('app.lib.files.Config.init') as mock_init:
        files.conf_data()  # First call, but already inited by fixture
        files.conf_data()  # Second call, no re-init
        mock_init.assert_not_called()
        result = files.conf_data()
        assert "jobs" in result

def test_conf_users():
    from app.lib import files
    with patch('app.lib.files.config') as mock_config:
        mock_config.users.return_value = {"test": "users"}
        result = files.conf_users()
        assert result == {"test": "users"}
        mock_config.users.assert_called_once()

def test_conf_users_error():
    from app.lib import files
    with patch('app.lib.files.config') as mock_config:
        mock_config.users.side_effect = Exception("config error")
        with pytest.raises(Exception, match="config error"):
            files.conf_users()

def test_conf_data_error():
    from app.lib import files
    with patch('app.lib.files.config') as mock_config:
        mock_config.data.side_effect = Exception("data error")
        with pytest.raises(Exception, match="data error"):
            files.conf_data()

def test_conf_runner_error():
    from app.lib import files
    with patch('app.lib.files.config') as mock_config:
        mock_config.runner.side_effect = Exception("runner error")
        with pytest.raises(Exception, match="runner error"):
            files.conf_runner()

def test_config_data_with_data_section(tmp_path):
    from app.lib.files import Config
    mock_content = '{"data": {"jobs": "/custom/jobs", "git": "/custom/git"}}'
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    config = Config.init(str(conf_path))
    result = config.data()
    assert result["jobs"] == "/custom/jobs"
    assert result["git"] == "/custom/git"
    assert "db" in result

def test_config_runner_with_runner_section(tmp_path):
    from app.lib.files import Config
    mock_content = '{"runner": {"git_pull_interval": 120, "venv": "/custom/venv"}}'
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    config = Config.init(str(conf_path))
    result = config.runner()
    assert result["git_pull_interval"] == 120
    assert result["venv"] == "/custom/venv"

def test_config_users_with_users_section(tmp_path):
    from app.lib.files import Config
    mock_content = '{"users": {"user1": {"debug_port": 5678}}}'
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    config = Config.init(str(conf_path))
    result = config.users()
    assert result == {"user1": {"debug_port": 5678}}

def test_config_data_exception_in_update(tmp_path):
    from app.lib.files import Config
    mock_content = '{"data": "not_dict"}'
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    config = Config.init(str(conf_path))
    with pytest.raises(ValueError):  # update on dict with str
        config.data()

def test_config_runner_exception_in_update(tmp_path):
    from app.lib.files import Config
    mock_content = '{"runner": "not_dict"}'
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    config = Config.init(str(conf_path))
    with pytest.raises(ValueError):
        config.runner()

def test_config_data_os_path_join_exception(tmp_path):
    from app.lib.files import Config
    mock_content = '{"data": {"jobs": "/custom/jobs"}}'
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    config = Config.init(str(conf_path))
    with patch('os.path.join', side_effect=Exception("os error")):
        with pytest.raises(Exception, match="os error"):
            config.data()


def test_config_users_get_exception(tmp_path):
    from app.lib.files import Config
    mock_content = '{"users": "not_dict"}'
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    config = Config.init(str(conf_path))
    with patch('app.lib.files.Config.users', side_effect=Exception("get error")):
        with pytest.raises(Exception, match="get error"):
            config.users()

def test_conf_log():
    from app.lib import files
    with patch('app.lib.files.config') as mock_config:
        mock_config.log.return_value = {"info": "/test/info.log"}
        result = files.conf_log()
        assert result == {"info": "/test/info.log"}
        mock_config.log.assert_called_once()

def test_conf_log_error():
    from app.lib import files
    with patch('app.lib.files.config') as mock_config:
        mock_config.log.side_effect = Exception("log error")
        with pytest.raises(Exception, match="log error"):
            files.conf_log()

def test_config_log_with_log_section(tmp_path):
    from app.lib.files import Config
    mock_content = '{"log": {"info": "/custom/info.log", "loglevel": "debug"}}'
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    config = Config.init(str(conf_path))
    result = config.log()
    assert result["loglevel"] == "debug"
    assert "access" not in result  # No access if not in config
    assert "info" not in result  # info path removed from log config

def test_config_log_exception_in_update(tmp_path):
    from app.lib.files import Config
    mock_content = '{"log": "not_dict"}'
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    config = Config.init(str(conf_path))
    with pytest.raises(Exception):
        config.log()

def test_config_log_no_log_section(tmp_path):
    from app.lib.files import Config
    mock_content = '{"data": {"jobs": "/test"}}'  # No "log" section
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    config = Config.init(str(conf_path))
    result = config.log()
    assert result == {"loglevel": "info"}
    assert "info" not in result
    assert "access" not in result

def test_config_log_loglevel_only(tmp_path):
    from app.lib.files import Config
    mock_content = '{"log": {"loglevel": "debug"}}'
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    config = Config.init(str(conf_path))
    result = config.log()
    assert result == {"loglevel": "debug"}
    assert "info" not in result
    assert "access" not in result

def test_config_log_access_only(tmp_path):
    from app.lib.files import Config
    mock_content = '{"log": {"access": "log/access.log"}}'
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    config = Config.init(str(conf_path))
    result = config.log()
    assert result["loglevel"] == "info"
    assert "access" not in result  # access path removed from log config
    assert "info" not in result

def test_config_log_info_only(tmp_path):
    from app.lib.files import Config
    mock_content = '{"log": {"info": "log/info.log"}}'
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    config = Config.init(str(conf_path))
    result = config.log()
    assert result["loglevel"] == "info"
    assert "info" not in result  # info path removed from log config
    assert "access" not in result

def test_config_log_full(tmp_path):
    from app.lib.files import Config
    mock_content = '{"log": {"info": "log/info.log", "access": "log/access.log", "loglevel": "debug"}}'
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    config = Config.init(str(conf_path))
    result = config.log()
    assert result["loglevel"] == "debug"
    assert "info" not in result  # info path removed from log config
    assert "access" not in result  # access path removed from log config


def test_conf_users_no_init():
    from app.lib import files
    orig_config = files.config
    files.config = None
    try:
        with pytest.raises(ValueError, match="Config not initialized"):
            files.conf_users()
    finally:
        files.config = orig_config


def test_config_users_uninit():
    from app.lib.files import Config
    config = Config()
    with pytest.raises(ValueError, match="Config not initialized"):
        config.users()

def test_conf_web_no_init_when_loaded(mock_conf, tmp_path):
    with patch('app.lib.files.Config.init') as mock_init:
        result = files.conf_web()
        mock_init.assert_not_called()
        expected = {
            "bind": "127.0.0.1:8000"
        }
        assert result == expected

def test_conf_web_with_override(mock_conf):
    result = files.conf_web()
    assert "bind" in result

def test_conf_web_init_on_empty(tmp_path):
    from app.lib.files import Config
    mock_content = '{"web": {"bind": "0.0.0.0:80"}}'
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    Config.init(str(conf_path))
    result = files.conf_web()
    expected = {"bind": "0.0.0.0:80"}
    assert result == expected
