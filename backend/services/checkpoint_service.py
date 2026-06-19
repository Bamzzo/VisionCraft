import uuid
from typing import Any

from ..database import connect, from_json, to_json, utc_now


def save_workflow_checkpoint(project_id: str, job_id: str, node: str, state: dict[str, Any]) -> str:
    checkpoint_id = f"checkpoint_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    serializable_state = _serialize_state(state)
    with connect() as conn:
        conn.execute(
            """
            UPDATE workflow_checkpoints
            SET status = ?, updated_at = ?
            WHERE project_id = ? AND status = ?
            """,
            ("superseded", now, project_id, "paused"),
        )
        conn.execute(
            """
            INSERT INTO workflow_checkpoints
            (id, project_id, job_id, node, status, state_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (checkpoint_id, project_id, job_id, node, "paused", to_json(serializable_state), now, now),
        )
    return checkpoint_id


def get_paused_checkpoint(project_id: str) -> dict:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM workflow_checkpoints
            WHERE project_id = ? AND status = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (project_id, "paused"),
        ).fetchone()
    if not row:
        return {}
    item = dict(row)
    item["state"] = from_json(item.get("state_json"), {})
    return item


def complete_checkpoint(checkpoint_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE workflow_checkpoints SET status = ?, updated_at = ? WHERE id = ?",
            ("completed", utc_now(), checkpoint_id),
        )


def _serialize_state(state: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in state.items():
        if hasattr(value, "model_dump"):
            result[key] = value.model_dump()
        elif hasattr(value, "dict"):
            result[key] = value.dict()
        else:
            result[key] = value
    return result
