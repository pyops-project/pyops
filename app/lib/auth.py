import re

from fastapi import Header


# The characters an owner keeps; every other character becomes '_'.
_OWNER_OTHER_CHARS = re.compile(r"[^A-Za-z0-9_.@-]")


async def get_current_user(x_user: str = Header(default=None, alias="X-User")) -> str:
    return sanitize_owner(x_user)


def sanitize_owner(owner: str | None) -> str:
    """The owner for an `X-User` value: its part before the first '/', with
    every character other than letters, digits, '_', '-', '.' and '@'
    replaced by '_'; 'anonymous' when nothing is left. Two values that
    differ only in replaced characters (`user+1`, `user 1`, `user_1`) are
    the same owner."""
    if not owner or owner == "anonymous":
        return "anonymous"
    first_part = owner.split('/')[0].strip()
    return _OWNER_OTHER_CHARS.sub("_", first_part) if first_part else "anonymous"
