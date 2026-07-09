import threading
import uuid

from ..database import connect, utc_now


_project_locks: dict[str, threading.Lock] = {}
_project_locks_guard = threading.Lock()


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


def create_project_job_guarded(
    project_id: str,
    job_type: str,
    message: str = "Queued",
    block_paused: bool = False,
) -> tuple[str | None, dict | None]:
    """Create a project job unless the project already has an active job.

    This lock is intentionally process-local. It is enough for the current
    single-process FastAPI BackgroundTasks deployment, but a multi-worker or
    distributed deployment should replace it with Redis locks, database row
    locks, or a real task queue.
    """

    lock = _project_lock(project_id)
    with lock:
        active = get_project_blocking_job(project_id, block_paused=block_paused)
        if active:
            return None, active
        return create_job(project_id, job_type, message), None


def get_project_blocking_job(project_id: str, block_paused: bool = False) -> dict:
    statuses = ("queued", "running") if not block_paused else ("queued", "running", "paused")
    placeholders = ",".join("?" for _ in statuses)
    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT id, type, status
            FROM jobs
            WHERE project_id = ?
              AND status IN ({placeholders})
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (project_id, *statuses),
        ).fetchone()
    return dict(row) if row else {}


def mark_orphaned_jobs_on_startup() -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE jobs
            SET status = ?, progress = ?, message = ?, error_message = ?, updated_at = ?
            WHERE status IN ('queued', 'running')
            """,
            ("failed", 100, "Job orphaned on restart", "orphaned on restart", utc_now()),
        )
    return cursor.rowcount


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


def _project_lock(project_id: str) -> threading.Lock:
    with _project_locks_guard:
        lock = _project_locks.get(project_id)
        if not lock:
            lock = threading.Lock()
            _project_locks[project_id] = lock
        return lock
