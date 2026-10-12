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
        ("ralph.bariz", "ralph.bariz"),
        ("ops.user@example.com", "ops.user@example.com"),
        ("a-b_c", "a-b_c"),
        ("user+1", "user_1"),
        ("user 1", "user_1"),
        ("o'brien", "o_brien"),
        ("<b>x</b>/y", "_b_x_"),
        ("ü", "_"),
    ],
)
def test_sanitize_owner(input_owner, expected):
    assert sanitize_owner(input_owner) == expected
