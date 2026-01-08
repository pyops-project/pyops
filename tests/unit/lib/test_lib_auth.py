import pytest
from app.lib.auth import sanitize_owner

@pytest.mark.parametrize(
    "input_owner, expected",
    [
        (None, "anonymous"),
        ("anonymous", "anonymous"),
        ("user", "user"),
        ("user/repo", "user"),
        ("", "anonymous"),
        (" /repo ", "anonymous"),
    ],
)
def test_sanitize_owner(input_owner, expected):
    assert sanitize_owner(input_owner) == expected
