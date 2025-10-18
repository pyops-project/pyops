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
    from app.lib.files import Config
    mock_content = '{"data": {"jobs": "/test/jobs"}}'
    conf_path = tmp_path / "test.json"
    with open(conf_path, "w") as f:
        f.write(mock_content)
    Config.init(str(conf_path))
    result = files.conf_data()
    expected = {
        "db": os.path.join(os.path.curdir, "../app.db"),
        "jobs": "/test/jobs",
        "git": os.path.join(os.path.curdir, "../data/git"),
    }
    assert result == expected

def test_conf_runner_no_init_when_loaded(mock_conf):
    with patch('app.lib.files.Config.init') as mock_init:
        result = files.conf_runner()
        mock_init.assert_not_called()
        expected = {"git_pull_interval": 60}
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
    expected = {"git_pull_interval": 30}
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
