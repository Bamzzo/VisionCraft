import uuid

from ..database import connect, utc_now


def create_job(project_id: str, job_type: str, message: str = "Queued") -> str:
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs
            (id, project_id, type, status, progress, message, retry_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, project_id, job_type, "queued", 0, message, 0, now, now),
        )
    return job_id


def update_job(
    job_id: str,
    status: str,
    progress: int,
    message: str,
    error_message: str | None = None,
) -> None:
    progress = max(0, min(progress, 100))
    with connect() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = ?, progress = ?, message = ?, error_message = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, progress, message, error_message, utc_now(), job_id),
        )


def increment_job_retry(job_id: str) -> int:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET retry_count = retry_count + 1, updated_at = ? WHERE id = ?",
            (utc_now(), job_id),
        )
        row = conn.execute("SELECT retry_count FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return int(row["retry_count"]) if row else 0


def get_job(job_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else {}
