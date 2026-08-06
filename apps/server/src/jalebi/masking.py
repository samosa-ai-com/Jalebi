"""Secret masking at ingest (PRD F17): the PAT and user-supplied regex patterns."""

import re
from collections.abc import Callable

MASK = "***"


def build_masker(secret: str | None, patterns: list[str]) -> Callable[[str], str]:
    """Return a function that redacts the PAT and any matching regex patterns."""
    compiled: list[re.Pattern[str]] = []
    if secret:
        compiled.append(re.compile(re.escape(secret)))
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
