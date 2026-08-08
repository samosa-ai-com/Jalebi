"""Snapshot + invariants for the external messaging templates.

Any new string Jalebi posts to GitHub must come from ``jalebi.messaging``; this
file pins the exact output so a template change is a conscious decision.
"""

from __future__ import annotations

import re

import pytest

from jalebi import messaging

# --- constants ---------------------------------------------------------------


def test_brand_constants_locked() -> None:
    assert messaging.BRAND_NAME == "Jalebi"
    assert messaging.OWNER_NAME == "Samosa AI"
    assert messaging.BRAND_EMOJI == "\U0001f9a6"  # otter
    assert messaging.REPO_URL == "https://github.com/samosa-ai-com/jalebi"
    assert messaging.OWNER_URL == "https://github.com/samosa-ai-com"
    assert messaging.CO_AUTHOR_NAME == "Jalebi"
    assert messaging.CO_AUTHOR_EMAIL == "jalebi@samosa-ai.com"
    assert messaging.PR_TITLE_MAX == 80
    assert messaging.PR_FALLBACK_TITLE == "\U0001f9a6 Jalebi task"
    assert messaging.PR_TITLE_PREFIX == "\U0001f9a6 Jalebi:"


# --- pr_title ---------------------------------------------------------------


def test_pr_title_with_first_line() -> None:
    assert messaging.pr_title("fix the broken login flow") == (
        "\U0001f9a6 Jalebi: fix the broken login flow"
    )


def test_pr_title_truncates_to_max() -> None:
    long = "x" * 200
    out = messaging.pr_title(long)
    assert out.startswith("\U0001f9a6 Jalebi: ")
    # The first line is capped at PR_TITLE_MAX; the prefix sits on top of that.
    assert out == "\U0001f9a6 Jalebi: " + ("x" * messaging.PR_TITLE_MAX)
    assert len(out) == len("\U0001f9a6 Jalebi: ") + messaging.PR_TITLE_MAX


def test_pr_title_empty_falls_back() -> None:
    assert messaging.pr_title("") == messaging.PR_FALLBACK_TITLE
    assert messaging.pr_title("   \n  ") == messaging.PR_FALLBACK_TITLE
    assert messaging.pr_title(None or "") == messaging.PR_FALLBACK_TITLE


# --- pr_footer_parts / pr_footer_body ---------------------------------------


def test_pr_footer_no_closes() -> None:
    parts = messaging.pr_footer_parts(closes=None)
    assert parts == [
        "\U0001f9a6 Opened by [Jalebi](https://github.com/samosa-ai-com/jalebi) — "
        "your self-hosted AI coding agent by [Samosa AI](https://github.com/samosa-ai-com).",
        "Co-authored-by: Jalebi <jalebi@samosa-ai.com>",
    ]


def test_pr_footer_empty_closes_list() -> None:
    parts = messaging.pr_footer_parts(closes=[])
    assert len(parts) == 2
    assert "Closes" not in parts[0]
    assert "Co-authored-by: Jalebi <jalebi@samosa-ai.com>" == parts[1]


def test_pr_footer_with_one_closes() -> None:
    parts = messaging.pr_footer_parts(closes=[42])
    assert parts[0] == "Closes #42"
    assert "Opened by" in parts[1]
    assert "Co-authored-by" in parts[2]


def test_pr_footer_with_multiple_closes() -> None:
    parts = messaging.pr_footer_parts(closes=[1, 2, 3])
    assert parts[0] == "Closes #1 #2 #3"


def test_pr_footer_body_joins_with_blank_lines() -> None:
    body = messaging.pr_footer_body(closes=[5])
    expected = (
        "Closes #5\n\n"
        "\U0001f9a6 Opened by [Jalebi](https://github.com/samosa-ai-com/jalebi) — "
        "your self-hosted AI coding agent by [Samosa AI](https://github.com/samosa-ai-com).\n\n"
        "Co-authored-by: Jalebi <jalebi@samosa-ai.com>"
    )
    assert body == expected


# --- issue_comment_for_pr ----------------------------------------------------


def test_issue_comment_format() -> None:
    out = messaging.issue_comment_for_pr(
        42, "https://github.com/samosa-ai-com/jalebi/pull/42"
    )
    assert out == (
        "\U0001f9a6 Jalebi opened [PR #42]"
        "(https://github.com/samosa-ai-com/jalebi/pull/42) to address this issue. "
        "_[What's Jalebi?](https://github.com/samosa-ai-com/jalebi)_"
    )


def test_issue_comment_renders_links() -> None:
    out = messaging.issue_comment_for_pr(7, "https://example.com/pr/7")
    # Both links must be present, on a single line so GitHub renders them inline.
    assert "[PR #7](https://example.com/pr/7)" in out
    assert "[What's Jalebi?](https://github.com/samosa-ai-com/jalebi)" in out
    assert out.count("\n") == 0


# --- wrap_pr_review ----------------------------------------------------------


def test_wrap_pr_review_with_body() -> None:
    body = "LGTM with nits:\n- fix x\n"
    out = messaging.wrap_pr_review(body)
    assert out.startswith(
        "> \U0001f9a6 _Review posted by "
        "[Jalebi](https://github.com/samosa-ai-com/jalebi)_\n\n"
    )
    assert "LGTM with nits:\n- fix x" in out
    assert out.rstrip().endswith(
        "\u2b50 _[Star Jalebi on GitHub]"
        "(https://github.com/samosa-ai-com/jalebi) — "
        "your self-hosted AI coding agent._"
    )


def test_wrap_pr_review_preserves_blockquote_body() -> None:
    body = "> already quoted line\nplain line"
    out = messaging.wrap_pr_review(body)
    # The agent's blockquote must NOT be flattened; the wrapper adds its own
    # `> 🦦 …` header on the first line.
    lines = out.splitlines()
    assert lines[0].startswith("> \U0001f9a6")
    assert "> already quoted line" in lines
    assert "plain line" in out


def test_wrap_pr_review_empty_body_still_has_header_and_footer() -> None:
    out = messaging.wrap_pr_review("")
    assert out.startswith("> \U0001f9a6 _Review posted by ")
    assert "\U0001f9a6 _Review" in out and "\u2b50 _[Star Jalebi" in out
    # Footer still present, separated by a horizontal rule.
    assert "\n\n---\n\n" in out


def test_wrap_pr_review_strips_trailing_whitespace() -> None:
    out = messaging.wrap_pr_review("hello   \n\n")
    # Body is rstripped so a single \n\n separator precedes the footer.
    assert "hello\n\n---\n\n" in out


# --- invariants across every template ---------------------------------------


@pytest.mark.parametrize(
    "factory",
    [
        lambda: messaging.pr_title("hello"),
        lambda: messaging.pr_title(""),
        lambda: messaging.pr_footer_body(),
        lambda: messaging.pr_footer_body(closes=[1, 2]),
        lambda: messaging.issue_comment_for_pr(1, "https://x/y/pull/1"),
        lambda: messaging.wrap_pr_review("hello"),
        lambda: messaging.wrap_pr_review(""),
    ],
)
def test_no_internal_info_leaks(factory) -> None:
    """No produced string may contain task IDs, localhost, or internal IPs."""
    out = factory()
    assert "127.0.0.1" not in out
    assert "localhost" not in out.lower()
    # No bare `task <digits>` (an external comment must never reference Jalebi's
    # internal task id).
    assert not re.search(r"\btask\s+\d+\b", out, flags=re.IGNORECASE)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: messaging.pr_footer_body(),
        lambda: messaging.pr_footer_body(closes=[1, 2]),
        lambda: messaging.issue_comment_for_pr(1, "https://x/y/pull/1"),
        lambda: messaging.wrap_pr_review("hello"),
    ],
)
def test_every_external_post_includes_repo_cta(factory) -> None:
    """Every user-visible external post links to the public Jalebi repo."""
    assert messaging.REPO_URL in factory()


def test_every_external_post_uses_brand_emoji(factory=None) -> None:  # type: ignore[no-untyped-def]
    """Every user-visible external post carries the brand emoji once on the brand line."""
    for text in (
        messaging.pr_title("hello"),
        messaging.pr_footer_body(closes=[1]),
        messaging.issue_comment_for_pr(1, "https://x/y/pull/1"),
        messaging.wrap_pr_review("hello"),
    ):
        assert messaging.BRAND_EMOJI in text


def test_worktree_identity_uses_same_co_author() -> None:
    """The git commit identity uses the same Co-authored-by name/email."""
    from jalebi import worktree_bootstrap

    assert worktree_bootstrap.GIT_USER_NAME == messaging.CO_AUTHOR_NAME
    assert worktree_bootstrap.GIT_USER_EMAIL == messaging.CO_AUTHOR_EMAIL
