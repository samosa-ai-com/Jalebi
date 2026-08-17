"""Phase 4 T4.2 — auto-nudge (default OFF)."""


import pytest

from jalebi import nudger, repos, secrets, settings


@pytest.fixture(autouse=True)
def _fake_token(config, monkeypatch):
    secrets.add_github_token(config, "test", "ghp_test")


def _make_task(session, app, status: str = "done"):
    repos.upsert_repo(
        session,
        full_name="owner/r",
        default_branch="main",
        clone_url="https://x/r.git",
        pat_name="test",
    )
    from jalebi import tasks as tasks_svc

    task = tasks_svc.create_task(
        session, type_="freeform", repo_id=1, prompt="x"
    )
    task.status = status
    session.commit()
    return task


def test_nudge_setting_default_off(session, app) -> None:
    """auto_nudge defaults to false — on_poller_fact_change is a no-op."""
    task = _make_task(session, app)
    from jalebi.db import Run

    run = Run(task_id=task.id, seq=1, status="done", session_id="ses_1")
    session.add(run)
    session.commit()

    q = app.config["JALEBI_QUEUE"]
    nudger.on_poller_fact_change(session, q, task.id, pr_number=1, kind="ci_failure", ref="sha")
    assert nudger._already_nudged(session, task.id, f"{task.id}:ci_failure:sha") is False


def test_nudge_skips_running_task(session, app) -> None:
    """A queued/running task is never nudged."""
    task = _make_task(session, app, status="running")
    from jalebi.db import Run

    run = Run(task_id=task.id, seq=1, status="running", session_id="ses_1")
    session.add(run)
    session.commit()
    settings.set_setting(session, "auto_nudge", True)

    q = app.config["JALEBI_QUEUE"]
    nudger.on_poller_fact_change(session, q, task.id, pr_number=1, kind="ci_failure", ref="sha")
    assert nudger._already_nudged(session, task.id, f"{task.id}:ci_failure:sha") is False


def test_nudge_skips_done_task(session, app) -> None:
    """A task already in `done` is never nudged — nudges are for unfinished work."""
    from jalebi.db import Run

    task = _make_task(session, app, status="done")
    run = Run(task_id=task.id, seq=1, status="done", session_id="ses_1")
    session.add(run)
    session.commit()
    settings.set_setting(session, "auto_nudge", True)

    q = app.config["JALEBI_QUEUE"]
    nudger.on_poller_fact_change(session, q, task.id, pr_number=1, kind="ci_failure", ref="sha")
    assert nudger._already_nudged(session, task.id, f"{task.id}:ci_failure:sha") is False


def test_nudge_dedupes_on_signature(session, app) -> None:
    """Two nudges with the same (task, kind, ref) only land once."""
    from jalebi.db import Run

    task = _make_task(session, app, status="failed")
    run = Run(task_id=task.id, seq=1, status="failed", session_id="ses_1")
    session.add(run)
    session.commit()
    settings.set_setting(session, "auto_nudge", True)

    q = app.config["JALEBI_QUEUE"]
    # Stub enqueue_followup to just count calls.
    call_count = {"n": 0}
    orig = q.enqueue_followup

    def counting(*a, **k):
        call_count["n"] += 1

    q.enqueue_followup = counting  # type: ignore[assignment]
    try:
        for _ in range(2):
            nudger.on_poller_fact_change(
                session, q, task.id, pr_number=1, kind="ci_failure", ref="sha"
            )
    finally:
        q.enqueue_followup = orig  # type: ignore[assignment]
    assert call_count["n"] == 1


def test_nudge_caps_at_max(session, app) -> None:
    """Per-task cap (MAX_NUDGES_PER_TASK=3) prevents runaway nudges."""
    from jalebi.db import Run

    task = _make_task(session, app, status="failed")
    run = Run(task_id=task.id, seq=1, status="failed", session_id="ses_1")
    session.add(run)
    session.commit()
    settings.set_setting(session, "auto_nudge", True)

    q = app.config["JALEBI_QUEUE"]
    orig = q.enqueue_followup
    call_count = {"n": 0}

    def counting(*a, **k):
        call_count["n"] += 1

    q.enqueue_followup = counting  # type: ignore[assignment]
    try:
        # 4 different signatures (refs) — only the first 3 should land.
        for ref in ("a", "b", "c", "d"):
            nudger.on_poller_fact_change(
                session, q, task.id, pr_number=1, kind="ci_failure", ref=ref
            )
    finally:
        q.enqueue_followup = orig  # type: ignore[assignment]
    assert call_count["n"] == 3
