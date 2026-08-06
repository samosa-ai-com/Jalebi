"""Task routes: create, list, detail, cancel, rerun, publish."""

from flask import Blueprint, current_app, jsonify, request
from flask.typing import ResponseReturnValue

from jalebi import db, masking, secrets, tasks
from jalebi.config import Config
from jalebi.db import utcnow
from jalebi.queue import TaskQueue

bp = Blueprint("tasks", __name__, url_prefix="/api/tasks")


def _queue() -> TaskQueue:
    return current_app.config["JALEBI_QUEUE"]


@bp.post("")
def create_task() -> ResponseReturnValue:
    config: Config = current_app.config["JALEBI_CONFIG"]
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400

    repo_id = payload.get("repo_id")
    if not isinstance(repo_id, int):
        return jsonify({"error": "repo_id is required"}), 400

    session = db.get_session()
    token = secrets.load_github_token(config)
    masker = masking.build_masker(token, [])
    raw_timeout = payload.get("timeout_minutes")
    timeout_minutes = raw_timeout if isinstance(raw_timeout, int) and raw_timeout > 0 else 30
    try:
        task = tasks.create_task(
            session,
            type_=payload.get("type", "freeform"),
            repo_id=repo_id,
            prompt=payload.get("prompt", ""),
            source_branch=payload.get("source_branch", "main"),
            target_branch=payload.get("target_branch", "main"),
            model=payload.get("model"),
            cli=payload.get("cli"),
            timeout_minutes=timeout_minutes,
            masker=masker,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    _queue().enqueue(task.id)
    return jsonify(tasks.task_to_dict(task)), 201


@bp.get("")
def list_tasks() -> ResponseReturnValue:
    session = db.get_session()
    items = [
        tasks.task_to_dict(task, run=tasks.latest_run(session, task.id))
        for task in tasks.list_tasks(session)
    ]
    return jsonify(items)


@bp.get("/<int:task_id>")
def get_task(task_id: int) -> ResponseReturnValue:
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    return jsonify(tasks.task_to_dict(task, run=tasks.latest_run(session, task_id)))


@bp.post("/<int:task_id>/cancel")
def cancel_task(task_id: int) -> ResponseReturnValue:
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    if task.status == "queued":
        task.status = "cancelled"
        task.updated_at = utcnow()
        session.commit()
        return jsonify({"status": "cancelled"})
    if task.status == "running":
        killed = _queue().cancel(task_id)
        return jsonify({"status": "cancelling", "killed": killed})
    return jsonify({"error": f"cannot cancel task in state {task.status}"}), 409


@bp.post("/<int:task_id>/rerun")
def rerun_task(task_id: int) -> ResponseReturnValue:
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    if task.status in ("queued", "running"):
        return jsonify({"error": f"cannot rerun task in state {task.status}"}), 409
    task.status = "queued"
    task.retry_count = (task.retry_count or 0) + 1
    task.updated_at = utcnow()
    session.commit()
    _queue().enqueue(task.id)
    return jsonify(tasks.task_to_dict(task))


@bp.post("/<int:task_id>/publish")
def publish_task(task_id: int) -> ResponseReturnValue:
    try:
        pr_number = _queue().publish_task(task_id)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"pr_number": pr_number, "status": "done"})
