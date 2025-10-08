"""Domain-level constants for authentication features."""

ALLOWED_AVATAR_EMOJIS: tuple[str, ...] = (
    "\U0001F600",  # grinning face
    "\U0001F60E",  # smiling face with sunglasses
    "\U0001F929",  # star-struck
    "\U0001F916",  # robot
    "\U0001F984",  # unicorn
    "\U0001F419",  # octopus
    "\U0001F680",  # rocket
    "\U0001F525",  # fire
    "\U0001F31F",  # glowing star
    "\U0001F3AF",  # bullseye
    "\U0001F389",  # party popper
    "\U0001F4A1",  # light bulb
    "\U0001F340",  # four leaf clover
    "\U0001F355",  # slice of pizza
)


ALLOWED_AVATAR_COLORS: tuple[str, ...] = (
    "#F3F4F6",
    "#1F2937",
    "#0EA5E9",
    "#10B981",
    "#6366F1",
    "#F59E0B",
    "#E5E7EB",
)


def normalise_avatar_emoji(value: str | None) -> str | None:
    """Return a canonical avatar emoji or ``None`` if the value is not allowed."""
    if value is None:
        return None

    stripped = value.strip()
    if not stripped:
        return None

    # Some browsers may append variation selectors; normalise by
    # dropping them when the base emoji is in the allowed list.
    for candidate in (stripped, stripped.rstrip("\uFE0F")):
        if candidate in ALLOWED_AVATAR_EMOJIS:
            return candidate

    return None


def normalise_avatar_color(value: str | None) -> str | None:
    if value is None:
        return None

    stripped = value.strip()
    if not stripped:
        return None

    candidate = stripped.upper()
    if not candidate.startswith("#"):
        candidate = f"#{candidate}"

    if candidate in ALLOWED_AVATAR_COLORS:
        return candidate

    return None
