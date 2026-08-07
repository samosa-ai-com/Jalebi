"""Secret masking at ingest (PRD F17): the PAT and user-supplied regex patterns."""

import re
from collections.abc import Callable

MASK = "***"


def build_masker(
    secret: str | list[str] | None, patterns: list[str]
) -> Callable[[str], str]:
    """Return a function that redacts the PAT(s) and any matching regex patterns."""
    compiled: list[re.Pattern[str]] = []
    secrets = [secret] if isinstance(secret, str) else list(secret or [])
    for item in secrets:
        if item:
            compiled.append(re.compile(re.escape(item)))
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error:
            continue

    def mask(text: str) -> str:
        for pattern in compiled:
            text = pattern.sub(MASK, text)
        return text

    return mask
