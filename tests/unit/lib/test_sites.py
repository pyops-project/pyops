import pytest
from unittest.mock import mock_open, patch
from app.lib import sites

@pytest.fixture
def mock_sites_path():
    return "/mock/sites"

def test_get_valid_path():
    content = "<html><body>Test site</body></html>"
    with patch('os.path.join', return_value="/mock/sites/test.html"), \
         patch('builtins.open', mock_open(read_data=content)) as m_open:
        result = sites.get("test")
        assert result == content
        m_open.assert_called_once_with("/mock/sites/test.html", "r")

def test_get_file_not_found():
    with patch('os.path.join', return_value="/mock/sites/missing.html"), \
         patch('builtins.open', side_effect=FileNotFoundError):
        with pytest.raises(FileNotFoundError):
            sites.get("missing")

# Edges: Invalid paths not handled beyond open error; no encoding specified (defaults utf-8).
