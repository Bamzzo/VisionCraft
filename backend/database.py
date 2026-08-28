import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import DB_PATH, init_environment


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    init_environment()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def from_json(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    return json.loads(value)


def init_db() -> None:
    init_environment()
    schema_path = Path(__file__).resolve().parent / "schema.sql"
    with connect() as conn:
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        _ensure_column(conn, "projects", "review_mode", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "projects", "archived", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "shots", "rag_evidence", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "shot_versions", "video_mode", "TEXT NOT NULL DEFAULT 't2v'")
        _ensure_column(conn, "shot_versions", "provider", "TEXT")
        _ensure_column(conn, "shot_versions", "model", "TEXT")
        _ensure_column(conn, "assets", "mime_type", "TEXT")
        _ensure_column(conn, "assets", "byte_size", "INTEGER")
        _ensure_column(conn, "assets", "sha256", "TEXT")
        _ensure_column(conn, "assets", "width", "INTEGER")
        _ensure_column(conn, "assets", "height", "INTEGER")
        _ensure_column(conn, "assets", "source_provider", "TEXT")
        _ensure_column(conn, "assets", "source_model", "TEXT")
        _ensure_video_tasks(conn)
        _ensure_media_transfers(conn)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _ensure_video_tasks(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS video_tasks (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
          version_id TEXT NOT NULL REFERENCES shot_versions(id) ON DELETE CASCADE,
          job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
          provider TEXT NOT NULL,
          model TEXT NOT NULL,
          remote_task_id TEXT NOT NULL,
          status TEXT NOT NULL,
          cloud_status TEXT NOT NULL DEFAULT '',
          prompt TEXT NOT NULL DEFAULT '',
          submit_payload TEXT NOT NULL DEFAULT '{}',
          status_payload TEXT NOT NULL DEFAULT '{}',
          video_url TEXT,
          result_path TEXT,
          error_code TEXT,
          error_message TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(provider, remote_task_id)
        )
        """
    )


def _ensure_media_transfers(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_transfers (
          id TEXT PRIMARY KEY,
          asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
          target_provider TEXT NOT NULL,
          target_model TEXT NOT NULL,
          transfer_mode TEXT NOT NULL,
          role TEXT NOT NULL,
          request_reference TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_transfers_asset_target "
        "ON media_transfers(asset_id, target_provider, target_model)"
    )
