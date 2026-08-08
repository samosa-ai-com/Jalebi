"""External messaging templates posted to GitHub by Jalebi.

Every string Jalebi writes to a GitHub PR / issue comment / review must come
from this module — never inline in ``queue.py`` or ``github.py``. That keeps
the brand consistent, makes template tweaks a one-file change, and lets tests
assert exact strings without spinning up the queue.

Design principles (see ``docs/14-messaging-strategy.md``):

1. **No internal info leaks** — never embed task IDs, ``localhost``, internal
   IPs, or run data in external messages.
2. **Functional lines stay functional** — ``Closes #N`` and ``Co-authored-by``
   are GitHub features; they keep working unchanged.
3. **Brand-consistent** — every external post identifies itself as Jalebi by
   Samosa AI, with one public-repo CTA.
4. **One emoji per brand line** — noise hurts more than it helps.
"""

from __future__ import annotations

BRAND_NAME = "Jalebi"
OWNER_NAME = "Samosa AI"
BRAND_EMOJI = "\U0001f9a6"  # otter

REPO_URL = "https://github.com/samosa-ai-com/jalebi"
OWNER_URL = "https://github.com/samosa-ai-com"

CO_AUTHOR_NAME = BRAND_NAME
CO_AUTHOR_EMAIL = "jalebi@samosa-ai.com"

PR_TITLE_PREFIX = f"{BRAND_EMOJI} {BRAND_NAME}:"
PR_FALLBACK_TITLE = f"{BRAND_EMOJI} {BRAND_NAME} task"

PR_TITLE_MAX = 80


def pr_title(first_line: str) -> str:
    """Return the PR title when no ``.jalebi/pr.md`` was written by the agent.

    ``first_line`` is the first line of the task prompt (already stripped).
    """
    head = (first_line or "").strip()[:PR_TITLE_MAX]
    if head:
        return f"{PR_TITLE_PREFIX} {head}"
    return PR_FALLBACK_TITLE


def pr_footer_parts(closes: list[int] | None = None) -> list[str]:
    """Return the ordered body parts appended to every PR body.

    Order: optional ``Closes #N`` line, brand line, ``Co-authored-by`` line.
    The caller joins the parts with ``\\n\\n``.
    """
    parts: list[str] = []
    if closes:
        closes_str = " ".join(f"#{int(n)}" for n in closes)
        parts.append(f"Closes {closes_str}")
    parts.append(
        f"{BRAND_EMOJI} Opened by [{BRAND_NAME}]({REPO_URL}) — "
        f"your self-hosted AI coding agent by [{OWNER_NAME}]({OWNER_URL})."
    )
    parts.append(f"Co-authored-by: {CO_AUTHOR_NAME} <{CO_AUTHOR_EMAIL}>")
    return parts


def pr_footer_body(closes: list[int] | None = None) -> str:
    """Return the joined PR footer body (parts separated by blank lines)."""
    return "\n\n".join(pr_footer_parts(closes=closes))


def issue_comment_for_pr(pr_number: int, pr_url: str) -> str:
    """Return the body of the comment Jalebi posts on a linked issue."""
    return (
        f"{BRAND_EMOJI} {BRAND_NAME} opened [PR #{pr_number}]({pr_url}) "
        f"to address this issue. "
        f"_[What's {BRAND_NAME}?]({REPO_URL})_"
    )


def wrap_pr_review(body: str) -> str:
    """Wrap an agent-written review body with the Jalebi header + CTA footer.

    ``body`` is the agent's ``.jalebi/review.md`` (or the last assistant
    message as fallback). The wrapper is added verbatim — call sites must mask
    the body for secrets BEFORE wrapping.
    """
    header = f"> {BRAND_EMOJI} _Review posted by [{BRAND_NAME}]({REPO_URL})_"
    footer = (
        "---\n\n"
        f"\u2b50 _[Star {BRAND_NAME} on GitHub]({REPO_URL}) — "
        f"your self-hosted AI coding agent._"
    )
    text = (body or "").rstrip()
    return f"{header}\n\n{text}\n\n{footer}" if text else f"{header}\n\n{footer}"
