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
        _ensure_column(conn, "projects", "assembly_stale", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "projects", "output_resolution", "TEXT NOT NULL DEFAULT '1280x720'")
        _ensure_column(conn, "shot_versions", "video_mode", "TEXT NOT NULL DEFAULT 't2v'")
        _ensure_column(conn, "shot_versions", "provider", "TEXT")
        _ensure_column(conn, "shot_versions", "model", "TEXT")
        _ensure_column(conn, "shot_versions", "camera_motion", "TEXT")
        _ensure_column(conn, "shot_versions", "duration_seconds", "INTEGER")
        _ensure_column(conn, "shot_versions", "reference_frame_path", "TEXT")
        _ensure_column(conn, "shot_versions", "change_summary", "TEXT")
        _ensure_column(conn, "assets", "mime_type", "TEXT")
        _ensure_column(conn, "assets", "byte_size", "INTEGER")
        _ensure_column(conn, "assets", "sha256", "TEXT")
        _ensure_column(conn, "assets", "width", "INTEGER")
        _ensure_column(conn, "assets", "height", "INTEGER")
        _ensure_column(conn, "assets", "source_provider", "TEXT")
        _ensure_column(conn, "assets", "source_model", "TEXT")
        _ensure_column(conn, "assets", "source_task_id", "TEXT")
        _ensure_column(conn, "assets", "source_remote_task_id", "TEXT")
        _ensure_column(conn, "assets", "asset_role", "TEXT")
        _ensure_column(conn, "assets", "duration_seconds", "REAL")
        _ensure_column(conn, "assets", "source", "TEXT")
        _ensure_column(conn, "jobs", "stage", "TEXT")
        _ensure_column(conn, "jobs", "shot_id", "TEXT")
        _ensure_column(conn, "projects", "selected_option_id", "TEXT")
        for column, ddl in (
            ("logline", "TEXT"),
            ("adaptation_summary", "TEXT"),
            ("emotion_curve", "TEXT"),
            ("protagonist", "TEXT"),
            ("protagonist_goal", "TEXT"),
            ("obstacle", "TEXT"),
            ("character_cards_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("scene_cards_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("visual_style", "TEXT"),
            ("consistency_constraints", "TEXT"),
            ("option_id", "TEXT"),
            ("source", "TEXT NOT NULL DEFAULT 'mock_planner'"),
            ("review_status", "TEXT NOT NULL DEFAULT 'draft'"),
            ("created_at", "TEXT"),
        ):
            _ensure_column(conn, "story_bibles", column, ddl)
        for column, ddl in (
            ("narrative_purpose", "TEXT"),
            ("action_text", "TEXT"),
            ("duration_seconds", "INTEGER"),
            ("bible_character", "TEXT"),
            ("bible_scene", "TEXT"),
            ("source_excerpt", "TEXT"),
            ("source_start", "INTEGER"),
            ("source_end", "INTEGER"),
            ("source_type", "TEXT"),
            ("review_status", "TEXT"),
        ):
            _ensure_column(conn, "shots", column, ddl)
        _ensure_video_tasks(conn)
        _ensure_remote_video_asset_mapping(conn)
        _ensure_media_transfers(conn)
        _ensure_job_events(conn)
        _ensure_shot_drafts(conn)
        _ensure_adaptation_tables(conn)
        _ensure_medium_text_tables(conn)
        _ensure_column(conn, "adaptation_options", "scope_id", "TEXT")
        _ensure_column(conn, "story_bibles", "scope_id", "TEXT")
        _ensure_column(conn, "storyboard_drafts", "scope_id", "TEXT")
        _ensure_assembly_settings(conn)
        _ensure_column(conn, "projects", "generation_mode", "TEXT NOT NULL DEFAULT 'mock'")
        _ensure_column(conn, "projects", "stale_stages", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "projects", "live_text_call_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "projects", "live_vision_call_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "projects", "live_video_call_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_workflow_model_configs(conn)
        _ensure_vision_reviews(conn)
        for table in ("adaptation_options", "story_bibles", "storyboard_drafts"):
            _ensure_column(conn, table, "provider", "TEXT")
            _ensure_column(conn, table, "model", "TEXT")
            _ensure_column(conn, table, "generation_mode", "TEXT")
            _ensure_column(conn, table, "used_local_fallback", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, table, "config_source", "TEXT")
        _ensure_column(conn, "adaptation_options", "stale", "INTEGER NOT NULL DEFAULT 0")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _ensure_remote_video_asset_mapping(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "assets", "source_task_id", "TEXT")
    _ensure_column(conn, "assets", "source_remote_task_id", "TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assets_source_remote "
        "ON assets(source_provider, source_remote_task_id)"
    )


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


def _ensure_job_events(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS job_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          shot_id TEXT,
          event_type TEXT NOT NULL,
          stage TEXT NOT NULL,
          status TEXT NOT NULL,
          progress INTEGER NOT NULL DEFAULT 0,
          message TEXT NOT NULL DEFAULT '',
          detail_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_job_events_job_id ON job_events(job_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_job_events_project_id ON job_events(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_job_events_created_at ON job_events(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_job_events_project_id_id ON job_events(project_id, id)")


def _ensure_shot_drafts(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shot_drafts (
          shot_id TEXT PRIMARY KEY REFERENCES shots(id) ON DELETE CASCADE,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          description TEXT,
          camera_motion TEXT,
          visual_prompt TEXT,
          negative_prompt TEXT,
          audio_prompt TEXT,
          video_mode TEXT,
          provider TEXT,
          model TEXT,
          duration_seconds INTEGER,
          first_frame_path TEXT,
          last_frame_path TEXT,
          reference_frame_path TEXT,
          updated_at TEXT NOT NULL
        )
        """
    )


def _ensure_adaptation_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS adaptation_options (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          option_index INTEGER NOT NULL,
          title TEXT NOT NULL,
          rationale TEXT NOT NULL,
          protagonist_goal TEXT NOT NULL,
          conflict TEXT NOT NULL,
          ending_orientation TEXT NOT NULL,
          suggested_duration_seconds INTEGER NOT NULL,
          suggested_shot_count INTEGER NOT NULL,
          source_excerpt TEXT NOT NULL,
          source_start INTEGER,
          source_end INTEGER,
          selected INTEGER NOT NULL DEFAULT 0,
          source TEXT NOT NULL DEFAULT 'mock_planner',
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS storyboard_drafts (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          shot_index INTEGER NOT NULL,
          title TEXT NOT NULL,
          narrative_purpose TEXT NOT NULL DEFAULT '',
          characters TEXT NOT NULL DEFAULT '[]',
          scene TEXT NOT NULL DEFAULT '',
          action_text TEXT NOT NULL DEFAULT '',
          camera_motion TEXT NOT NULL DEFAULT '',
          duration_seconds INTEGER NOT NULL DEFAULT 5,
          visual_prompt TEXT NOT NULL DEFAULT '',
          bible_character TEXT,
          bible_scene TEXT,
          source_excerpt TEXT NOT NULL DEFAULT '',
          source_start INTEGER,
          source_end INTEGER,
          source_type TEXT NOT NULL DEFAULT 'auto_draft',
          review_status TEXT NOT NULL DEFAULT 'draft',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_records (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          stage TEXT NOT NULL,
          action TEXT NOT NULL,
          summary TEXT NOT NULL DEFAULT '',
          target_id TEXT,
          target_version TEXT,
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_adaptation_options_project ON adaptation_options(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_storyboard_drafts_project ON storyboard_drafts(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_review_records_project ON review_records(project_id)")


def _ensure_medium_text_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS source_chunks (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          chunk_index INTEGER NOT NULL,
          text TEXT NOT NULL,
          start_offset INTEGER NOT NULL,
          end_offset INTEGER NOT NULL,
          char_count INTEGER NOT NULL,
          summary TEXT NOT NULL DEFAULT '',
          characters_json TEXT NOT NULL DEFAULT '[]',
          places_json TEXT NOT NULL DEFAULT '[]',
          conflict_terms_json TEXT NOT NULL DEFAULT '[]',
          source TEXT NOT NULL DEFAULT 'mock_segmenter',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_source_chunks_project ON source_chunks(project_id);
        CREATE INDEX IF NOT EXISTS idx_source_chunks_project_index ON source_chunks(project_id, chunk_index);
        CREATE TABLE IF NOT EXISTS story_events (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          event_index INTEGER NOT NULL,
          title TEXT NOT NULL,
          summary TEXT NOT NULL DEFAULT '',
          characters_json TEXT NOT NULL DEFAULT '[]',
          places_json TEXT NOT NULL DEFAULT '[]',
          goal TEXT NOT NULL DEFAULT '',
          conflict TEXT NOT NULL DEFAULT '',
          outcome TEXT NOT NULL DEFAULT '',
          chunk_ids_json TEXT NOT NULL DEFAULT '[]',
          source_excerpt TEXT NOT NULL DEFAULT '',
          source_start INTEGER,
          source_end INTEGER,
          importance REAL NOT NULL DEFAULT 0,
          source TEXT NOT NULL DEFAULT 'mock_event_extractor',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_story_events_project ON story_events(project_id);
        CREATE INDEX IF NOT EXISTS idx_story_events_project_index ON story_events(project_id, event_index);
        CREATE TABLE IF NOT EXISTS storylines (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          storyline_index INTEGER NOT NULL,
          title TEXT NOT NULL,
          rationale TEXT NOT NULL DEFAULT '',
          protagonist TEXT NOT NULL DEFAULT '',
          protagonist_goal TEXT NOT NULL DEFAULT '',
          conflict TEXT NOT NULL DEFAULT '',
          turning_point TEXT NOT NULL DEFAULT '',
          ending_orientation TEXT NOT NULL DEFAULT '',
          event_ids_json TEXT NOT NULL DEFAULT '[]',
          chunk_ids_json TEXT NOT NULL DEFAULT '[]',
          source_excerpt TEXT NOT NULL DEFAULT '',
          suggested_duration_seconds INTEGER NOT NULL DEFAULT 45,
          suggested_shot_count INTEGER NOT NULL DEFAULT 5,
          selected INTEGER NOT NULL DEFAULT 0,
          source TEXT NOT NULL DEFAULT 'mock_storyline_planner',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_storylines_project ON storylines(project_id);
        CREATE TABLE IF NOT EXISTS adaptation_scopes (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          storyline_id TEXT,
          event_ids_json TEXT NOT NULL DEFAULT '[]',
          chunk_ids_json TEXT NOT NULL DEFAULT '[]',
          scoped_text TEXT NOT NULL DEFAULT '',
          start_offset INTEGER,
          end_offset INTEGER,
          review_status TEXT NOT NULL DEFAULT 'draft',
          user_note TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL DEFAULT 'user_scope',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_adaptation_scopes_project ON adaptation_scopes(project_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_adaptation_scopes_project_unique ON adaptation_scopes(project_id);
        """
    )


def _ensure_assembly_settings(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assembly_settings (
          project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
          subtitle_enabled INTEGER NOT NULL DEFAULT 0,
          subtitle_text TEXT NOT NULL DEFAULT '',
          subtitle_srt_path TEXT NOT NULL DEFAULT '',
          audio_enabled INTEGER NOT NULL DEFAULT 0,
          audio_asset_path TEXT NOT NULL DEFAULT '',
          audio_volume REAL NOT NULL DEFAULT 0.4,
          keep_source_audio INTEGER NOT NULL DEFAULT 0,
          subtitle_font_size INTEGER NOT NULL DEFAULT 28,
          subtitle_position TEXT NOT NULL DEFAULT 'bottom',
          updated_at TEXT NOT NULL
        )
        """
    )


def _ensure_workflow_model_configs(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_model_configs (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          workflow_run_id TEXT,
          stage TEXT NOT NULL,
          provider TEXT NOT NULL,
          model TEXT NOT NULL,
          parameters TEXT NOT NULL DEFAULT '{}',
          selected_by_user INTEGER NOT NULL DEFAULT 0,
          is_default INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(project_id, stage)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_model_configs_project ON workflow_model_configs(project_id)")


def _ensure_vision_reviews(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vision_reviews (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          asset_id TEXT NOT NULL,
          asset_role TEXT,
          provider TEXT NOT NULL,
          model TEXT NOT NULL,
          transport_mode TEXT,
          mime_type TEXT,
          width INTEGER,
          height INTEGER,
          byte_size INTEGER,
          request_id TEXT,
          result_json TEXT NOT NULL DEFAULT '{}',
          used_local_fallback INTEGER NOT NULL DEFAULT 0,
          generation_mode TEXT,
          source TEXT,
          config_source TEXT,
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vision_reviews_project ON vision_reviews(project_id)")
