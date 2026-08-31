"""Workflow checkpoints for review pause/resume.

Checkpoints store only what is needed to resume: project/job/node IDs, a short
input summary, and related scope/option/version ids. They never persist API keys,
Data URLs, Base64, full prompts, or signed URLs.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from ..database import connect, from_json, to_json, utc_now
from ..services.job_service import redact_text, redact_value

ALLOWED_STATE_KEYS = {
    "project_id",
    "job_id",
    "node",
    "stage",
    "option_id",
    "scope_id",
    "storyline_id",
    "bible_id",
    "version_id",
    "shot_id",
    "input_summary",
    "pause_reason",
    "review_status",
}

FORBIDDEN_KEY_TOKENS = (
    "api_key",
    "authorization",
    "token",
    "secret",
    "password",
    "data_url",
    "base64",
    "prompt",
    "signature",
    "bearer",
)

_DATA_URL = re.compile(r"data:[^,\s]+;base64,[a-z0-9+/=]+", re.I)
_SK = re.compile(r"(?<![a-z])sk-[a-z0-9._\-]{8,}", re.I)
_SIGNED = re.compile(r"x-amz-(?:signature|credential)|[?&]signature=", re.I)

REVIEW_NODES = {
    "storyline_review",
    "scope_review",
    "bible_review",
    "storyboard_review",
    "quality_gate",
}

REVIEW_STATUSES = {
    "awaiting_storyline_review",
    "awaiting_scope_review",
    "adaptation_options_ready",
    "awaiting_bible_review",
    "story_bible_ready",
    "awaiting_storyboard_review",
    "storyboard_draft_ready",
    "review_pending",
}

NODE_FOR_STATUS = {
    "awaiting_storyline_review": "storyline_review",
    "awaiting_scope_review": "scope_review",
    "adaptation_options_ready": "scope_review",
    "awaiting_bible_review": "bible_review",
    "story_bible_ready": "bible_review",
    "awaiting_storyboard_review": "storyboard_review",
    "storyboard_draft_ready": "storyboard_review",
    "review_pending": "quality_gate",
}

PAUSE_REASON = {
    "storyline_review": "已到达故事线审核节点，等待选择并确认范围。",
    "scope_review": "已到达改编范围审核节点，等待选择并确认方案。",
    "bible_review": "已到达 Story Bible 审核节点，等待确认后再生成分镜。",
    "storyboard_review": "已到达分镜审核节点，等待确认后进入镜头制作。",
    "quality_gate": "已到达旧版监制质检节点，等待人工确认后继续。",
}


class CheckpointError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def save_workflow_checkpoint(project_id: str, job_id: str, node: str, state: dict[str, Any]) -> str:
    now = utc_now()
    serializable_state = sanitize_checkpoint_state(state, project_id=project_id, job_id=job_id, node=node)
    with connect() as conn:
        existing = conn.execute(
            """
            SELECT id FROM workflow_checkpoints
            WHERE project_id = ? AND node = ? AND status = ?
            ORDER BY updated_at DESC LIMIT 1
            """,
            (project_id, node, "paused"),
        ).fetchone()
        conn.execute(
            """
            UPDATE workflow_checkpoints
            SET status = ?, updated_at = ?
            WHERE project_id = ? AND status = ? AND node != ?
            """,
            ("superseded", now, project_id, "paused", node),
        )
        payload = to_json(serializable_state)
        if existing:
            conn.execute(
                """
                UPDATE workflow_checkpoints
                SET job_id = ?, state_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (job_id, payload, now, existing["id"]),
            )
            return existing["id"]
        checkpoint_id = f"checkpoint_{uuid.uuid4().hex[:10]}"
        conn.execute(
            """
            INSERT INTO workflow_checkpoints
            (id, project_id, job_id, node, status, state_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (checkpoint_id, project_id, job_id, node, "paused", payload, now, now),
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
    return _row_to_checkpoint(row)


def get_checkpoint(checkpoint_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM workflow_checkpoints WHERE id = ?", (checkpoint_id,)).fetchone()
    return _row_to_checkpoint(row)


def list_checkpoints(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM workflow_checkpoints
            WHERE project_id = ?
            ORDER BY updated_at DESC
            """,
            (project_id,),
        ).fetchall()
    return [public_checkpoint(_row_to_checkpoint(row)) for row in rows]


def complete_checkpoint(checkpoint_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE workflow_checkpoints SET status = ?, updated_at = ? WHERE id = ?",
            ("completed", utc_now(), checkpoint_id),
        )


def public_checkpoint(item: dict | None) -> dict:
    if not item:
        return {}
    state = item.get("state") if isinstance(item.get("state"), dict) else {}
    return {
        "id": item.get("id"),
        "project_id": item.get("project_id"),
        "job_id": item.get("job_id"),
        "node": item.get("node"),
        "status": item.get("status"),
        "stage": state.get("stage") or NODE_FOR_STATUS.get(item.get("node") or "", ""),
        "option_id": state.get("option_id"),
        "scope_id": state.get("scope_id"),
        "storyline_id": state.get("storyline_id"),
        "version_id": state.get("version_id"),
        "input_summary": state.get("input_summary") or "",
        "pause_reason": state.get("pause_reason") or PAUSE_REASON.get(item.get("node") or "", "流程已在审核节点暂停。"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def sanitize_checkpoint_state(state: dict[str, Any], *, project_id: str, job_id: str, node: str) -> dict[str, Any]:
    raw = redact_value(state if isinstance(state, dict) else {})
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        lowered = str(key).lower()
        if any(token in lowered for token in FORBIDDEN_KEY_TOKENS):
            continue
        if key not in ALLOWED_STATE_KEYS:
            continue
        cleaned[key] = _clip_value(value)
    cleaned["project_id"] = project_id
    cleaned["job_id"] = job_id
    cleaned["node"] = node
    cleaned.setdefault("stage", NODE_FOR_STATUS.get(node, ""))
    cleaned.setdefault("pause_reason", PAUSE_REASON.get(node, "流程已在审核节点暂停。"))
    if "input_summary" in cleaned:
        cleaned["input_summary"] = str(cleaned["input_summary"] or "")[:200]
    blob = to_json(cleaned)
    if _looks_like_secret(blob):
        raise CheckpointError("CHECKPOINT_UNSAFE", "检查点包含敏感内容，已拒绝保存。请重试当前审核步骤。")
    return cleaned


def _clip_value(value: Any) -> Any:
    if isinstance(value, str):
        text = redact_text(value)
        if _looks_like_secret(text):
            return "<redacted>"
        return text[:400]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key)[:40]: _clip_value(item) for key, item in list(value.items())[:12] if str(key).lower() in ALLOWED_STATE_KEYS}
    if isinstance(value, list):
        return [_clip_value(item) for item in value[:8]]
    return str(value)[:120]


def _looks_like_secret(blob: str) -> bool:
    lowered = blob.lower()
    if _SIGNED.search(lowered):
        return True
    if _DATA_URL.search(lowered):
        return True
    if "base64," in lowered:
        return True
    if _SK.search(lowered):
        return True
    return False


def _row_to_checkpoint(row) -> dict:
    if not row:
        return {}
    item = dict(row)
    item["state"] = from_json(item.get("state_json"), {}) or {}
    return item


def _serialize_state(state: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible helper used by older LangGraph pause path."""
    result: dict[str, Any] = {}
    for key, value in (state or {}).items():
        if hasattr(value, "model_dump"):
            result[key] = value.model_dump()
        elif hasattr(value, "dict"):
            result[key] = value.dict()
        else:
            result[key] = value
    return result
