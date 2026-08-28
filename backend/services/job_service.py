import json
import re
import uuid
from typing import Any

from ..database import connect, from_json, to_json, utc_now

ACTIVE_JOB_STATUSES = {"queued", "running", "waiting_remote", "paused"}
EVENT_JOB_UPDATE = "job.update"
EVENT_ASSET_READY = "asset.ready"
EVENT_JOB_FAILED = "job.failed"
EVENT_REFRESH_REQUIRED = "project.refresh_required"

_DATA_URL_RE = re.compile(r"data:[^,\s]+;base64,[A-Za-z0-9+/=\s]+", re.I)
_SIGNED_URL_RE = re.compile(r"(https?://[^\s\"']+(?:X-Amz-Signature|Signature|X-Amz-Credential|Expires)=[^\s\"']+)", re.I)
_KEY_RE = re.compile(r"(?i)(api[_-]?key|authorization|bearer|token|secret)\s*[:=]\s*([^\s\"']+)")
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")
_SK_RE = re.compile(r"(?i)sk-[A-Za-z0-9._\-]+")


def create_job(
    project_id: str,
    job_type: str,
    message: str = "任务已排队",
    shot_id: str | None = None,
    stage: str = "queued",
) -> str:
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs
            (id, project_id, type, status, progress, message, retry_count, created_at, updated_at, stage, shot_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, project_id, job_type, "queued", 0, message, 0, now, now, stage, shot_id),
        )
    append_job_event(
        job_id,
        event_type=EVENT_JOB_UPDATE,
        stage=stage,
        status="queued",
        progress=0,
        message=message,
        shot_id=shot_id,
    )
    return job_id


def update_job(
    job_id: str,
    status: str,
    progress: int,
    message: str,
    error_message: str | None = None,
    *,
    shot_id: str | None = None,
    stage: str | None = None,
    event_type: str | None = None,
    detail: dict | None = None,
    emit_refresh: bool = False,
) -> None:
    progress = max(0, min(int(progress), 100))
    safe_error = redact_text(error_message) if error_message else None
    inferred_stage = stage or _stage_from_status(status)
    inferred_type = event_type or _event_type_from_status(status, inferred_stage)
    now = utc_now()
    with connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            return
        resolved_shot = shot_id if shot_id is not None else job["shot_id"]
        conn.execute(
            """
            UPDATE jobs
            SET status = ?, progress = ?, message = ?, error_message = ?, updated_at = ?, stage = ?, shot_id = COALESCE(?, shot_id)
            WHERE id = ?
            """,
            (status, progress, message, safe_error, now, inferred_stage, resolved_shot, job_id),
        )
        project_id = job["project_id"]
    event = append_job_event(
        job_id,
        event_type=inferred_type,
        stage=inferred_stage,
        status=status,
        progress=progress,
        message=message,
        shot_id=resolved_shot,
        detail=_event_detail(detail, safe_error),
        project_id=project_id,
    )
    if emit_refresh or inferred_type in {EVENT_ASSET_READY, EVENT_JOB_FAILED} or status == "completed":
        if inferred_type != EVENT_REFRESH_REQUIRED:
            append_job_event(
                job_id,
                event_type=EVENT_REFRESH_REQUIRED,
                stage=inferred_stage,
                status=status,
                progress=progress,
                message="项目素材已更新，正在刷新预览",
                shot_id=resolved_shot,
                detail={"reason": inferred_type},
                project_id=project_id,
                skip_duplicate=False,
            )
    return event


def append_job_event(
    job_id: str,
    *,
    event_type: str,
    stage: str,
    status: str,
    progress: int,
    message: str,
    shot_id: str | None = None,
    detail: dict | None = None,
    project_id: str | None = None,
    skip_duplicate: bool = True,
) -> dict | None:
    safe_detail = redact_value(detail or {})
    with connect() as conn:
        job = conn.execute("SELECT project_id, shot_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            return None
        resolved_project = project_id or job["project_id"]
        resolved_shot = shot_id if shot_id is not None else job["shot_id"]
        if skip_duplicate:
            last = conn.execute(
                """
                SELECT event_type, stage, status, progress, message
                FROM job_events
                WHERE job_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            if last and (
                last["event_type"] == event_type
                and last["stage"] == stage
                and last["status"] == status
                and int(last["progress"]) == int(progress)
                and last["message"] == message
            ):
                return None
        cursor = conn.execute(
            """
            INSERT INTO job_events
            (job_id, project_id, shot_id, event_type, stage, status, progress, message, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                resolved_project,
                resolved_shot,
                event_type,
                stage,
                status,
                progress,
                message,
                to_json(safe_detail),
                utc_now(),
            ),
        )
        event_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM job_events WHERE id = ?", (event_id,)).fetchone()
    return public_job_event(row)


def get_job(job_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return {}
        events = conn.execute(
            "SELECT * FROM job_events WHERE job_id = ? ORDER BY id ASC",
            (job_id,),
        ).fetchall()
    result = dict(row)
    result["events"] = [public_job_event(item) for item in events]
    return result


def get_job_events(
    project_id: str,
    *,
    after_id: int = 0,
    job_id: str | None = None,
    limit: int = 200,
) -> list[dict]:
    query = "SELECT * FROM job_events WHERE project_id = ? AND id > ?"
    params: list[Any] = [project_id, int(after_id or 0)]
    if job_id:
        query += " AND job_id = ?"
        params.append(job_id)
    query += " ORDER BY id ASC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [public_job_event(row) for row in rows]


def get_recent_job_events(project_id: str, limit: int = 40) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM job_events
            WHERE project_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (project_id, max(1, min(int(limit), 200))),
        ).fetchall()
    return [public_job_event(row) for row in reversed(rows)]


def list_active_jobs(project_id: str) -> list[dict]:
    placeholders = ",".join("?" for _ in ACTIVE_JOB_STATUSES)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM jobs
            WHERE project_id = ? AND status IN ({placeholders})
            ORDER BY updated_at DESC
            """,
            (project_id, *ACTIVE_JOB_STATUSES),
        ).fetchall()
    return [dict(row) for row in rows]


def job_center_snapshot(project_id: str, after_id: int = 0) -> dict:
    jobs = list_active_jobs(project_id)
    events = get_job_events(project_id, after_id=after_id)
    latest_id = after_id
    if events:
        latest_id = events[-1]["id"]
    elif jobs:
        with connect() as conn:
            row = conn.execute("SELECT MAX(id) AS max_id FROM job_events WHERE project_id = ?", (project_id,)).fetchone()
            latest_id = int(row["max_id"] or after_id)
    return {
        "project_id": project_id,
        "jobs": jobs,
        "events": events,
        "latest_event_id": latest_id,
        "has_waiting_remote": any(job["status"] == "waiting_remote" for job in jobs),
    }


def public_job_event(row: Any) -> dict:
    item = dict(row)
    return {
        "id": item["id"],
        "event_type": item["event_type"],
        "project_id": item["project_id"],
        "job_id": item["job_id"],
        "shot_id": item.get("shot_id"),
        "stage": item["stage"],
        "status": item["status"],
        "progress": item["progress"],
        "message": item["message"],
        "detail": from_json(item.get("detail_json"), {}),
        "created_at": item["created_at"],
    }


def format_sse(event: dict, event_name: str | None = None) -> str:
    name = event_name or event.get("event_type") or EVENT_JOB_UPDATE
    payload = json.dumps(event, ensure_ascii=False)
    return f"id: {event['id']}\nevent: {name}\ndata: {payload}\n\n"


def redact_text(value: str | None) -> str:
    if not value:
        return ""
    text = _DATA_URL_RE.sub("<data-url-omitted>", value)
    text = _SIGNED_URL_RE.sub("<signed-url-omitted>", text)
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    text = _KEY_RE.sub(r"\1=<redacted>", text)
    text = _SK_RE.sub("<redacted>", text)
    if "base64," in text.lower() and len(text) > 80:
        text = "<base64-omitted>"
    return text[:800]


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("api_key", "authorization", "token", "secret", "password", "data_url", "base64")):
                result[key] = "<redacted>"
            else:
                result[key] = redact_value(item)
        return result
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def increment_job_retry(job_id: str) -> int:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET retry_count = retry_count + 1, updated_at = ? WHERE id = ?",
            (utc_now(), job_id),
        )
        row = conn.execute("SELECT retry_count FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return int(row["retry_count"]) if row else 0


def _event_type_from_status(status: str, stage: str) -> str:
    if status == "failed" or stage == "failed":
        return EVENT_JOB_FAILED
    if stage == "persist_asset":
        return EVENT_ASSET_READY
    return EVENT_JOB_UPDATE


def _stage_from_status(status: str) -> str:
    mapping = {
        "queued": "queued",
        "waiting_remote": "waiting_remote",
        "completed": "completed",
        "failed": "failed",
        "paused": "queued",
    }
    return mapping.get(status, "prepare")


def _event_detail(detail: dict | None, safe_error: str | None) -> dict:
    payload = dict(detail or {})
    if safe_error:
        payload.setdefault("error", safe_error)
    return payload
