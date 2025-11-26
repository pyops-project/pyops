def sanitize_owner(owner: str | None) -> str:
    """Sanitize owner string to extract username prefix, defaulting to 'anonymous'."""
    if not owner or owner == "anonymous":
        return "anonymous"
    first_part = owner.split('/')[0].strip()
    return first_part if first_part else "anonymous"
