import pytest

from jalebi import attention

# Task 42's real stored final message: truncated at 2000 chars mid-table
# (the plan's opening paragraph carries the "waiting for explicit approval").
TASK_42_FINAL_MESSAGE = (
    "I've read the full PR review (fetched the review via the GitHub API — "
    "the AGENTS.md copy was truncated). "
    "The review has10 findings. Per AGENTS.md §2.1, here is my plan; "
    "**waiting for explicit approval** before touching anything.\n"
    "\n"
    "---\n"
    "\n"
    "## Plan: Address PR #3 review findings\n"
    "\n"
    "**Scope decision.** I'll act on the 2 [MEDIUM] findings, the 5 [LOW] findings, "
    "and 1 [INFO] that is a one-line doc sync. "
    "I'm skipping the 2 [INFO] items that aren't actionable as part of a security review response "
    "(`#9 nanoid` is a build-time transitive dep; `#10 bwrap probe` is a possibly-too-strict "
    "probe that, if relaxed, "
    "could yield false positives — neither is part of the security review's intent and the "
    "owner hasn't asked for them).\n"
    "\n"
    "###1. Code changes| # | Finding | File(s) | Change |\n"
    "|---|---|---|---|\n"
    "| **M1** | codex zero-disk-confinement when sandbox unavailable (and worst for screening) | "
    "`apps/server/src/jalebi/screening.py` | In `_run_screen_locked`, after resolving "
    "`effective_cli`, if "
    "`effective_cli == \"codex\"` and not `_sandbox_usable()`, refuse the screening with "
    "a clear `ScreeningError` explaining why. "
    "(`_build_agent_env(None)` already keeps the screening path credential-free; this "
    "adds the missing layer-1 floor that "
    "opencode/claude have.) Plus mirror `_sandbox_usable` on the route validation so a "
    "`codex` screen is rejected at "
    "create/update if the host is sandbox-less (consistent with the runtime refusal). |\n"
    "| **M2** | codex/claude gh-guards bypassable via full path / wrappers | "
    "`apps/server/src/jalebi/worktree_bootstrap.py` (`CODEX_RULES_GUARD`, `CLAUDE_DENY_RULES`) | "
    "Mirror `OPENCODE_GUARD`'s "
    "coverage. For codex, extend `.codex/rules/default.rules` with additional "
    "`prefix_rule(pattern=[\"/usr/bin/gh\"], …)`, "
    "`/usr/local/bin/gh`, `/opt/*/gh`, and wrappers `command`, `which`, `type`, `hash` "
    "(each as a `prefix_rule`, plus updated "
    "`match`/`not_match` self-tests). For claude, add `Bash(/usr/bin/gh*)`, "
    "`Bash(/usr/local/bin/gh*)`, `Bash(/opt/*/gh*)`, "
    "`Bash(command gh*)`, `Bash(which gh*)"
)

PHRASES = (
    "waiting for",
    "awaiting",
    "please confirm",
    "let me know",
    "need your",
    "your approval",
    "should i",
    "do you want",
    "want me to",
    "approval",
)


def test_task_42_final_message_is_waiting() -> None:
    assert attention.is_waiting_message(TASK_42_FINAL_MESSAGE) is True


def test_trailing_question_marks_waiting() -> None:
    assert attention.is_waiting_message("Shall I proceed?") is True
    assert attention.is_waiting_message("Proceed?") is True
    assert attention.is_waiting_message("Proceeding now.") is False


@pytest.mark.parametrize("phrase", PHRASES)
def test_waiting_phrases(phrase: str) -> None:
    assert attention.is_waiting_message(f"I'll be {phrase} your reply.") is True


def test_case_insensitive() -> None:
    assert attention.is_waiting_message("WAITING FOR your reply") is True
    assert attention.is_waiting_message("PLEASE CONFIRM.") is True


def test_plain_done_message_false() -> None:
    assert attention.is_waiting_message("All done! I pushed the fix and CI is green.") is False


def test_looks_great_false() -> None:
    assert attention.is_waiting_message("Looks great!") is False


def test_approve_is_not_approval() -> None:
    assert attention.is_waiting_message("I'll approve the merge when the checks pass.") is False


def test_empty_and_whitespace_false() -> None:
    assert attention.is_waiting_message("") is False
    assert attention.is_waiting_message("   ") is False


def test_last_message_text_picks_last_nonempty() -> None:
    steps = [
        {"type": "tool_call", "text": "x"},
        {"type": "message", "text": "old"},
        {"type": "message", "text": "new"},
    ]
    assert attention.last_message_text(steps) == "new"
    steps.append({"type": "message", "text": ""})
    assert attention.last_message_text(steps) == "new"


def test_last_message_text_empty() -> None:
    assert attention.last_message_text([]) == ""
    assert attention.last_message_text([{"type": "message", "text": ""}]) == ""
    assert attention.last_message_text([{"type": "tool_call", "text": "x"}]) == ""
