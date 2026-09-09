import json
import threading
import time

import pytest

from jalebi import repos, secrets, settings, tasks
from jalebi.adapters.types import AgentEvent
from jalebi.events import TaskEvents
from jalebi.queue import TaskQueue

FULL_NAME = "owner/repo"


def test_task_events_publish_subscribe_close() -> None:
    bus = TaskEvents()
    q = bus.subscribe(7)
    bus.publish(7, {"type": "message", "text": "hi"})
    item = q.get(timeout=1)
    assert item["type"] == "message"
    assert item["text"] == "hi"
    assert item["seq"] == 1
    bus.publish(7, {"type": "message", "text": "again"})
    assert q.get(timeout=1)["seq"] == 2
    bus.close(7)
    assert q.get(timeout=1) is None


def test_task_events_seq_monotonic_and_buffered() -> None:
    bus = TaskEvents()
    for i in range(3):
        bus.publish(7, {"type": "message", "text": str(i)})
    # A subscriber connecting later with no after_seq sees nothing already sent…
    q = bus.subscribe(7)
    assert q.empty()
    # …and after_seq backfills only newer events.
    q2 = bus.subscribe(7, after_seq=1)
    assert [item["seq"] for item in [q2.get(timeout=1), q2.get(timeout=1)]] == [2, 3]
    assert q2.empty()


def test_task_events_unsubscribe() -> None:
    bus = TaskEvents()
    q = bus.subscribe(7)
    bus.unsubscribe(7, q)
    bus.close(7)
    assert q.empty()


def test_task_events_no_cross_task_leak() -> None:
    bus = TaskEvents()
    q = bus.subscribe(1)
    bus.subscribe(2)
    bus.publish(2, {"type": "message", "text": "other"})
    assert q.empty()


class _FakeProc:
    def __init__(self) -> None:
        self.killed = False

    def poll(self):
        return None

    def terminate(self) -> None:
        self.killed = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout=None) -> int:
        return 0


class _FakeHandle:
    def __init__(self, events):
        self._events = list(events)
        self.session_id = "ses_fake"
        self.proc = _FakeProc()

    def events(self):
        yield from self._events


class _FakeAdapter:
    def __init__(self, handle):
        self._handle = handle

    def start(self, cwd, prompt, model=None, env=None):
        return self._handle

    def resume(self, *args, **kwargs):
        raise NotImplementedError

    def list_models(self):
        return []


@pytest.fixture(autouse=True)
def _fake_token(config, monkeypatch):
    secrets.add_github_token(config, "test", "ghp_test")


@pytest.fixture
def git_remote(tmp_path) -> str:
    import subprocess

    def _git(args):
        proc = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    remote = tmp_path / "remote.git"
    src = tmp_path / "src"
    _git(["init", "--bare", str(remote)])
    _git(["init", str(src)])
    _git(["-C", str(src), "config", "user.email", "t@example.com"])
    _git(["-C", str(src), "config", "user.name", "Test"])
    (src / "f.txt").write_text("hello\n")
    _git(["-C", str(src), "add", "f.txt"])
    _git(["-C", str(src), "commit", "-m", "initial"])
    _git(["-C", str(src), "branch", "-M", "main"])
    _git(["-C", str(src), "remote", "add", "origin", str(remote)])
    _git(["-C", str(src), "push", "-u", "origin", "main"])
    _git(["-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"])
    return str(remote)


@pytest.fixture
def q(app):
    return app.config["JALEBI_QUEUE"]


def test_sse_streams_live_events_and_closes(
    q: TaskQueue, app, session, git_remote, monkeypatch
) -> None:
    settings.set_setting(session, "auto_publish", False)
    row, _ = repos.upsert_repo(
        session,
        full_name=FULL_NAME,
        default_branch="main",
        clone_url=git_remote,
        pat_name="test",
    )
    task = tasks.create_task(session, type_="freeform", repo_id=row.id, prompt="do it")
    session.commit()
    task_id = task.id

    handle = _FakeHandle(
        [
            AgentEvent(type="message", text="working"),
            AgentEvent(type="done"),
        ]
    )
    monkeypatch.setattr(
        "jalebi.queue.get_adapter", lambda cli: _FakeAdapter(handle)
    )

    client = app.test_client()
    stream_resp = client.get(f"/api/tasks/{task_id}/events", buffered=False)
    assert stream_resp.mimetype == "text/event-stream"

    lines: list[str] = []

    def read_stream():
        for chunk in stream_resp.response:
            lines.append(chunk.decode())

    reader = threading.Thread(target=read_stream)
    reader.start()

    runner = threading.Thread(target=q._run_task, args=(task_id,))
    runner.start()
    runner.join(timeout=10)
    assert not runner.is_alive()
    reader.join(timeout=10)
    assert not reader.is_alive()

    payloads = []
    for line in "".join(lines).splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))
    types = [p["type"] for p in payloads]
    assert types[0] == "connected"
    assert "message" in types
    assert "done" in types
    assert types[-1] == "stream_end"


def test_sse_immediate_close_for_terminal_task(
    q: TaskQueue, app, session, git_remote, monkeypatch
) -> None:
    settings.set_setting(session, "auto_publish", False)
    row, _ = repos.upsert_repo(
        session,
        full_name=FULL_NAME,
        default_branch="main",
        clone_url=git_remote,
        pat_name="test",
    )
    task = tasks.create_task(session, type_="freeform", repo_id=row.id, prompt="do it")
    handle = _FakeHandle([AgentEvent(type="done")])
    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: _FakeAdapter(handle))
    q._run_task(task.id)

    session.expire_all()
    fresh = tasks.get_task(session, task.id)
    assert fresh is not None
    assert fresh.status == "done"

    client = app.test_client()
    resp = client.get(f"/api/tasks/{task.id}/events")
    body = b"".join(resp.response).decode()
    payloads = [
        json.loads(line[6:])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    assert payloads == [{"type": "connected"}, {"type": "stream_end"}]


def test_sse_after_seq_backfills_events_published_before_subscribe(
    q: TaskQueue, app, session, git_remote, monkeypatch
) -> None:
    """Events emitted before a subscriber attached are replayed, not lost (F4)."""
    settings.set_setting(session, "auto_publish", False)
    row, _ = repos.upsert_repo(
        session,
        full_name=FULL_NAME,
        default_branch="main",
        clone_url=git_remote,
        pat_name="test",
    )
    task = tasks.create_task(session, type_="freeform", repo_id=row.id, prompt="do it")
    session.commit()
    task_id = task.id

    class _GatedHandle:
        def __init__(self, events):
            self._events = list(events)
            self.release = threading.Event()
            self.session_id = "ses_g"
            self.proc = _FakeProc()

        def events(self):
            last = len(self._events) - 1
            for i, ev in enumerate(self._events):
                yield ev
                if i < last and not self.release.wait(timeout=10):
                    break
                if i < last:
                    self.release.clear()

    handle = _GatedHandle(
        [
            AgentEvent(type="message", text="early"),
            AgentEvent(type="message", text="working"),
            AgentEvent(type="done"),
        ]
    )
    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: _FakeAdapter(handle))

    runner = threading.Thread(target=q._run_task, args=(task_id,))
    runner.start()

    # Wait until the run has published its first event (seq 1), then attach a
    # late subscriber that must backfill it from the replay buffer.
    deadline = time.monotonic() + 10
    while _published_seq(q.events, task_id) < 1 and time.monotonic() < deadline:
        time.sleep(0.01)

    client = app.test_client()
    stream_resp = client.get(f"/api/tasks/{task_id}/events?after_seq=0", buffered=False)
    lines: list[str] = []

    def read_stream():
        for chunk in stream_resp.response:
            lines.append(chunk.decode())

    reader = threading.Thread(target=read_stream)
    reader.start()

    handle.release.set()  # let "working" flow; wait for it, then release the tail
    deadline = time.monotonic() + 10
    while _published_seq(q.events, task_id) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    handle.release.set()
    runner.join(timeout=10)
    assert not runner.is_alive()
    reader.join(timeout=10)
    assert not reader.is_alive()

    payloads = []
    for line in "".join(lines).splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))
    texts = [(p.get("type"), p.get("text"), p.get("seq")) for p in payloads]
    assert texts[0][0] == "connected"
    # The pre-subscribe events were replayed from the buffer (seq 1), then
    # live. Consecutive messages merge into one step, so "early"+"working"
    # arrive as a single payload.
    assert ("message", "earlyworking", 1) in texts
    assert texts[-1] == ("stream_end", None, None)


def _published_seq(events: TaskEvents, task_id: int) -> int:
    """Highest seq published for ``task_id`` across runs.

    ``TaskEvents._seq`` is keyed by ``(task_id, run_id)`` since the durable-SSE
    change — a bare ``_seq.get(task_id)`` never matches and spins until its
    deadline (this test used to burn ~20s per run this way).
    """
    with events._lock:
        return max(
            (seq for (tid, _run_id), seq in events._seq.items() if tid == task_id),
            default=0,
        )


def test_sse_not_found(app) -> None:
    client = app.test_client()
    assert client.get("/api/tasks/999/events").status_code == 404


def test_sse_live_no_duplicate_memory_and_db_replay(
    q: TaskQueue, app, session
) -> None:
    """F6: a live reconnect with after_seq gets each event exactly once —
    the memory-bus backfill must not repeat the durable DB replay."""
    from jalebi.db import Run

    settings.set_setting(session, "auto_publish", False)
    row, _ = repos.upsert_repo(
        session,
        full_name=FULL_NAME,
        default_branch="main",
        clone_url="https://example.invalid/owner/repo.git",
        pat_name="test",
    )
    task = tasks.create_task(session, type_="freeform", repo_id=row.id, prompt="do it")
    run = Run(task_id=task.id, seq=1, status="running", session_id="ses_live")
    session.add(run)
    session.commit()
    task_id = task.id

    for text in ("a", "b"):
        q.events.publish(
            task_id, {"type": "message", "text": text}, run_id=run.id, session=session
        )
    session.commit()

    client = app.test_client()
    stream_resp = client.get(f"/api/tasks/{task_id}/events?after_seq=0", buffered=False)
    lines: list[str] = []

    def read_stream():
        for chunk in stream_resp.response:
            lines.append(chunk.decode())

    reader = threading.Thread(target=read_stream)
    reader.start()

    # Wait until both events arrive, then end the stream from the test.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        seqs = [
            json.loads(line[6:]).get("seq")
            for line in "".join(lines).splitlines()
            if line.startswith("data: ")
        ]
        if sum(1 for s in seqs if s in (1, 2)) >= 2:
            break
        time.sleep(0.01)
    q.events.close(task_id)
    reader.join(timeout=10)
    assert not reader.is_alive()

    seqs = [
        json.loads(line[6:]).get("seq")
        for line in "".join(lines).splitlines()
        if line.startswith("data: ")
    ]
    seqs = [s for s in seqs if isinstance(s, int)]
    assert seqs == [1, 2]


def test_sse_emits_ids_and_honors_last_event_id(q: TaskQueue, app, session) -> None:
    """Frames carry `id: <seq>` and a reconnect via `Last-Event-ID` (native
    EventSource auto-reconnect, no `after_seq` param) backfills from the
    durable table without duplicates."""
    from jalebi.db import Run

    settings.set_setting(session, "auto_publish", False)
    row, _ = repos.upsert_repo(
        session,
        full_name=FULL_NAME,
        default_branch="main",
        clone_url="https://example.invalid/owner/repo.git",
        pat_name="test",
    )
    task = tasks.create_task(session, type_="freeform", repo_id=row.id, prompt="do it")
    run = Run(task_id=task.id, seq=1, status="running", session_id="ses_ids")
    session.add(run)
    session.commit()
    task_id = task.id

    for text in ("a", "b"):
        q.events.publish(
            task_id, {"type": "message", "text": text}, run_id=run.id, session=session
        )
    session.commit()

    client = app.test_client()
    stream_resp = client.get(
        f"/api/tasks/{task_id}/events", headers={"Last-Event-ID": "1"}, buffered=False
    )
    lines: list[str] = []

    def read_stream():
        for chunk in stream_resp.response:
            lines.append(chunk.decode())

    reader = threading.Thread(target=read_stream)
    reader.start()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if any('"seq": 2' in line for line in lines):
            break
        time.sleep(0.01)
    q.events.close(task_id)
    reader.join(timeout=10)
    assert not reader.is_alive()

    body = "".join(lines)
    # Only seq 2 was replayed (seq 1 was already seen per Last-Event-ID).
    seqs = [
        json.loads(line[6:]).get("seq")
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    seqs = [s for s in seqs if isinstance(s, int)]
    assert seqs == [2]
    # The data frame for seq 2 was preceded by a standard SSE id line.
    assert "id: 2" in body.splitlines()
