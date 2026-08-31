import uuid
import shutil
from typing import Any

from ..database import connect, from_json, to_json, utc_now
from ..config import PROJECTS_DIR
from ..schemas import ProjectCreate, ProjectSettingsUpdate


def _row_to_dict(row: Any) -> dict:
    return dict(row) if row else {}


def compute_routing_mode(text: str) -> str:
    length = len(text)
    if length < 5000:
        return "direct"
    if length < 30000:
        return "chunk"
    return "rag"


def compute_shot_count(payload: ProjectCreate) -> int:
    if payload.shot_count_mode == "manual" and payload.requested_shot_count:
        return payload.requested_shot_count
    text_len = len(payload.source_text)
    if text_len < 800:
        return 3
    if text_len < 1800:
        return 4
    if text_len < 3500:
        return 5
    return 6


def create_project(payload: ProjectCreate) -> dict:
    project_id = f"project_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    routing_mode = compute_routing_mode(payload.source_text)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO projects (
              id, title, source_text, style, aspect_ratio, duration_seconds, output_resolution,
              shot_count_mode, requested_shot_count, review_mode, status, routing_mode, generation_mode,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                payload.title,
                payload.source_text,
                payload.style,
                payload.aspect_ratio,
                payload.duration_seconds,
                _normalize_output_resolution(getattr(payload, "output_resolution", None)),
                payload.shot_count_mode,
                payload.requested_shot_count,
                1 if payload.review_mode else 0,
                "created",
                routing_mode,
                getattr(payload, "generation_mode", None) or "mock",
                now,
                now,
            ),
        )
    return get_project(project_id)


ALLOWED_ASPECT_RATIOS = {"16:9", "9:16", "1:1"}
ALLOWED_DURATIONS = {5, 6, 7, 8, 9, 10}
ALLOWED_RESOLUTIONS = {"1280x720", "1920x1080", "720x1280", "1080x1080", "720x720"}
ASSEMBLY_SPEC_FIELDS = {"aspect_ratio", "duration_seconds", "output_resolution"}
DEFAULT_OUTPUT_RESOLUTION = "1280x720"


def _normalize_output_resolution(value: str | None) -> str:
    resolution = str(value or DEFAULT_OUTPUT_RESOLUTION).strip().lower().replace(" ", "")
    return resolution if resolution in ALLOWED_RESOLUTIONS else DEFAULT_OUTPUT_RESOLUTION


class ProjectSettingsError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def update_project_settings(project_id: str, payload: ProjectSettingsUpdate | dict) -> dict:
    data = payload.model_dump(exclude_unset=True) if isinstance(payload, ProjectSettingsUpdate) else dict(payload or {})
    allowed = {"title", "aspect_ratio", "duration_seconds", "output_resolution"}
    unknown = set(data) - allowed
    if unknown:
        raise ProjectSettingsError("只能修改项目名称、目标时长、画幅比例或输出分辨率。")
    if not data:
        raise ProjectSettingsError("没有可保存的项目设置。")
    with connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise ProjectSettingsError("项目不存在。", 404)
    current = dict(row)
    updates: dict = {}
    if "title" in data:
        title = str(data.get("title") or "").strip()
        if not title:
            raise ProjectSettingsError("项目名称不能为空。")
        updates["title"] = title[:120]
    if "aspect_ratio" in data:
        ratio = str(data.get("aspect_ratio") or "").strip()
        if ratio not in ALLOWED_ASPECT_RATIOS:
            raise ProjectSettingsError("画幅比例无效。可用：16:9、9:16、1:1。")
        updates["aspect_ratio"] = ratio
    if "duration_seconds" in data:
        try:
            duration = int(data.get("duration_seconds"))
        except (TypeError, ValueError):
            raise ProjectSettingsError("目标时长无效。可用：5、6、7、8、9、10 秒。") from None
        if duration not in ALLOWED_DURATIONS:
            raise ProjectSettingsError("目标时长无效。可用：5、6、7、8、9、10 秒。")
        updates["duration_seconds"] = duration
    if "output_resolution" in data:
        resolution = str(data.get("output_resolution") or "").strip().lower().replace(" ", "")
        if resolution not in ALLOWED_RESOLUTIONS:
            raise ProjectSettingsError("输出分辨率无效。可用：1280x720、1920x1080、720x1280、1080x1080。")
        updates["output_resolution"] = resolution
    if not updates:
        raise ProjectSettingsError("没有可保存的项目设置。")
    spec_changed = any(current.get(key) != updates[key] for key in updates if key in ASSEMBLY_SPEC_FIELDS)
    now = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values())
    with connect() as conn:
        finals = conn.execute(
            "SELECT COUNT(*) AS n FROM assets WHERE project_id = ? AND type = 'final-video'",
            (project_id,),
        ).fetchone()["n"]
        stale_sql = ", assembly_stale = 1" if spec_changed and finals else ""
        conn.execute(
            f"UPDATE projects SET {assignments}{stale_sql}, updated_at = ? WHERE id = ?",
            (*values, now, project_id),
        )
    from .job_service import create_job, update_job

    job_id = create_job(project_id, "project_settings", "项目设置已保存")
    update_job(
        job_id,
        "completed",
        100,
        "项目设置已更新，工作区将同步最新配置",
        stage="settings",
        event_type="project.refresh_required",
        detail={"fields": sorted(updates), "assembly_stale": bool(spec_changed and finals)},
    )
    return get_project(project_id)


def list_projects(include_archived: bool = False) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, title, status, style, aspect_ratio, archived, updated_at
            FROM projects
            WHERE (? = 1 OR archived = 0)
            ORDER BY updated_at DESC
            """,
            (1 if include_archived else 0,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_project(project_id: str) -> dict:
    with connect() as conn:
        project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not project:
            return {}

        bible = conn.execute(
            "SELECT * FROM story_bibles WHERE project_id = ?", (project_id,)
        ).fetchone()
        characters = conn.execute(
            "SELECT * FROM characters WHERE project_id = ? ORDER BY created_at", (project_id,)
        ).fetchall()
        scenes = conn.execute(
            "SELECT * FROM scenes WHERE project_id = ? ORDER BY created_at", (project_id,)
        ).fetchall()
        constraints = conn.execute(
            "SELECT * FROM global_constraints WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
        shots = conn.execute(
            "SELECT * FROM shots WHERE project_id = ? ORDER BY shot_index", (project_id,)
        ).fetchall()
        versions = conn.execute(
            """
            SELECT sv.*
            FROM shot_versions sv
            JOIN shots s ON s.id = sv.shot_id
            WHERE s.project_id = ?
            ORDER BY sv.created_at DESC
            """,
            (project_id,),
        ).fetchall()
        assets = conn.execute(
            "SELECT * FROM assets WHERE project_id = ? ORDER BY created_at", (project_id,)
        ).fetchall()
        drafts = conn.execute(
            "SELECT * FROM shot_drafts WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        feedback = conn.execute(
            "SELECT * FROM feedback_records WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        jobs = conn.execute(
            "SELECT * FROM jobs WHERE project_id = ? ORDER BY created_at DESC", (project_id,)
        ).fetchall()
        video_tasks = conn.execute(
            """
            SELECT id, project_id, shot_id, version_id, job_id, provider, model, remote_task_id,
                   status, cloud_status, video_url, result_path, error_code, error_message,
                   created_at, updated_at
            FROM video_tasks
            WHERE project_id = ?
            ORDER BY updated_at DESC
            """,
            (project_id,),
        ).fetchall()
        checkpoint = conn.execute(
            """
            SELECT id, job_id, node, status, updated_at
            FROM workflow_checkpoints
            WHERE project_id = ? AND status = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (project_id, "paused"),
        ).fetchone()
        adaptation_options = conn.execute(
            "SELECT * FROM adaptation_options WHERE project_id = ? ORDER BY option_index",
            (project_id,),
        ).fetchall()
        storyboard_drafts = conn.execute(
            "SELECT * FROM storyboard_drafts WHERE project_id = ? ORDER BY shot_index",
            (project_id,),
        ).fetchall()
        review_records = conn.execute(
            "SELECT * FROM review_records WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()

    result = _row_to_dict(project)
    if not result.get("output_resolution"):
        result["output_resolution"] = "1280x720"
    result["story_bible"] = _row_to_dict(bible) if bible else None
    if result["story_bible"]:
        result["story_bible"]["style_tags"] = from_json(result["story_bible"]["style_tags"], [])
        result["story_bible"]["themes"] = from_json(result["story_bible"]["themes"], [])
        result["story_bible"]["character_cards"] = from_json(result["story_bible"].get("character_cards_json"), [])
        result["story_bible"]["scene_cards"] = from_json(result["story_bible"].get("scene_cards_json"), [])
    result["characters"] = [_row_to_dict(row) for row in characters]
    result["scenes"] = [_row_to_dict(row) for row in scenes]
    result["global_constraints"] = [_row_to_dict(row) for row in constraints]
    video_tasks_by_shot: dict[str, list[dict]] = {}
    video_task_items = [_row_to_dict(row) for row in video_tasks]
    for task in video_task_items:
        video_tasks_by_shot.setdefault(task["shot_id"], []).append(task)
    drafts_by_shot = {_row_to_dict(row)["shot_id"]: _row_to_dict(row) for row in drafts}
    versions_by_shot: dict[str, list[dict]] = {}
    for row in versions:
        item = _row_to_dict(row)
        version_tasks = [task for task in video_task_items if task["version_id"] == item["id"]]
        item["generation"] = version_tasks[0] if version_tasks else None
        if not item.get("provider") and item["generation"]:
            item["provider"] = item["generation"].get("provider")
            item["model"] = item["generation"].get("model")
        versions_by_shot.setdefault(item["shot_id"], []).append(item)
    result["shots"] = []
    for row in shots:
        shot = _normalize_shot(row)
        shot["versions"] = versions_by_shot.get(shot["id"], [])
        shot["video_tasks"] = video_tasks_by_shot.get(shot["id"], [])
        shot["active_video_task"] = _active_video_task(shot["video_tasks"])
        current = next((item for item in shot["versions"] if item["id"] == shot["current_version_id"]), None)
        shot["draft"] = drafts_by_shot.get(shot["id"])
        shot["has_unsaved_changes"] = _draft_differs(shot["draft"], current)
        result["shots"].append(shot)
    result["assets"] = [_row_to_dict(row) for row in assets]
    result["feedback_records"] = [_row_to_dict(row) for row in feedback]
    result["jobs"] = [_row_to_dict(row) for row in jobs]
    result["video_tasks"] = video_task_items
    result["checkpoint"] = _row_to_dict(checkpoint) if checkpoint else None
    result["adaptation_options"] = [_row_to_dict(row) for row in adaptation_options]
    result["storyboard_drafts"] = []
    for row in storyboard_drafts:
        item = _row_to_dict(row)
        item["characters"] = from_json(item.get("characters"), [])
        result["storyboard_drafts"].append(item)
    result["review_records"] = [_row_to_dict(row) for row in review_records]
    result["generation_mode"] = result.get("generation_mode") or "mock"
    result["stale_stages"] = from_json(result.get("stale_stages"), []) or []
    from .job_service import get_recent_job_events, list_active_jobs

    result["job_events"] = get_recent_job_events(project_id, limit=40)
    result["active_jobs"] = list_active_jobs(project_id)
    from .medium_text_service import attach_medium_text
    from .video_service import get_assembly_status

    result["assembly"] = get_assembly_status(project_id)
    result = attach_medium_text(result)
    from .model_config_service import attach_model_state
    from .vision_review_service import list_vision_reviews

    result = attach_model_state(result)
    result["vision_reviews"] = list_vision_reviews(project_id)
    from .workflow_control_service import attach_workflow_control

    return attach_workflow_control(result)


_DRAFT_COMPARE_FIELDS = (
    "description",
    "camera_motion",
    "visual_prompt",
    "negative_prompt",
    "audio_prompt",
    "video_mode",
    "provider",
    "model",
    "duration_seconds",
    "first_frame_path",
    "last_frame_path",
    "reference_frame_path",
)


def _draft_differs(draft: dict | None, version: dict | None) -> bool:
    if not draft or not version:
        return False
    for key in _DRAFT_COMPARE_FIELDS:
        if str(draft.get(key) or "") != str(version.get(key) or ""):
            return True
    return False


def _normalize_shot(row: Any) -> dict:
    shot = _row_to_dict(row)
    shot["characters"] = from_json(shot.get("characters"), [])
    shot["rag_evidence"] = from_json(shot.get("rag_evidence"), [])
    return shot


def _active_video_task(tasks: list[dict]) -> dict | None:
    if not tasks:
        return None
    for task in tasks:
        if task["status"] in {"running", "pending_remote"}:
            return task
    for task in tasks:
        if task["status"] == "failed":
            return task
    return None


def update_project_status(project_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
            (status, utc_now(), project_id),
        )


def clear_generated_project_data(project_id: str) -> None:
    project_dir = PROJECTS_DIR / project_id
    if project_dir.exists():
        workspace_root = PROJECTS_DIR.resolve()
        resolved = project_dir.resolve()
        if resolved == workspace_root or workspace_root not in resolved.parents:
            raise RuntimeError(f"Refusing to clear unsafe project path: {resolved}")
        if str(resolved).startswith(str(workspace_root)):
            for child in resolved.iterdir():
                if child.is_file():
                    child.unlink()
    with connect() as conn:
        conn.execute("DELETE FROM shot_drafts WHERE project_id = ?", (project_id,))
        conn.execute(
            """
            DELETE FROM shot_versions
            WHERE shot_id IN (SELECT id FROM shots WHERE project_id = ?)
            """,
            (project_id,),
        )
        conn.execute("DELETE FROM feedback_records WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM global_constraints WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM characters WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM scenes WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM shots WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM assets WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM story_bibles WHERE project_id = ?", (project_id,))


def save_story_bible(project_id: str, summary: str, worldview: str, style_tags: list[str], themes: list[str]) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO story_bibles
            (project_id, summary, worldview, style_tags, themes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (project_id, summary, worldview, to_json(style_tags), to_json(themes), utc_now()),
        )


def delete_project(project_id: str) -> bool:
    project_dir = PROJECTS_DIR / project_id
    if project_dir.exists():
        workspace_root = PROJECTS_DIR.resolve()
        resolved = project_dir.resolve()
        if resolved == workspace_root or workspace_root not in resolved.parents:
            raise RuntimeError(f"Refusing to delete unsafe project path: {resolved}")
        shutil.rmtree(resolved)
    with connect() as conn:
        cursor = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return cursor.rowcount > 0


def archive_project(project_id: str, archived: bool = True) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE projects SET archived = ?, updated_at = ? WHERE id = ?",
            (1 if archived else 0, utc_now(), project_id),
        )
    return cursor.rowcount > 0


def cleanup_demo_data(
    keep_project_id: str | None = None,
    archive_failed: bool = True,
    remove_invalid_videos: bool = True,
) -> dict:
    """Clean confusing demo leftovers without deleting real model outputs."""

    cleaned_assets = 0
    cleared_version_refs = 0
    archived_projects: list[str] = []
    if remove_invalid_videos:
        cleaned_assets, cleared_version_refs = _remove_invalid_video_assets()
    if archive_failed:
        archived_projects = _archive_non_demo_failures(keep_project_id)
    return {
        "removed_invalid_video_assets": cleaned_assets,
        "cleared_version_video_refs": cleared_version_refs,
        "archived_projects": archived_projects,
    }


def _remove_invalid_video_assets() -> tuple[int, int]:
    with connect() as conn:
        invalid_assets = conn.execute(
            """
            SELECT id, project_id, file_path, embedding_ref
            FROM assets
            WHERE type = 'video'
              AND (
                embedding_ref IS NULL
                OR embedding_ref = ''
                OR embedding_ref LIKE 'fallback:%'
                OR embedding_ref LIKE 'provider:ffmpeg%'
              )
            """
        ).fetchall()
        paths = [row["file_path"] for row in invalid_assets]
        cleared = 0
        for path in paths:
            cleared += conn.execute(
                "UPDATE shot_versions SET video_path = NULL WHERE video_path = ?",
                (path,),
            ).rowcount
        conn.executemany("DELETE FROM assets WHERE id = ?", [(row["id"],) for row in invalid_assets])
        for row in invalid_assets:
            _delete_project_asset_file(row["project_id"], row["file_path"])
    return len(invalid_assets), cleared


def _archive_non_demo_failures(keep_project_id: str | None) -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
              p.id,
              p.status,
              COUNT(s.id) AS shot_count,
              SUM(CASE WHEN a.type = 'video' AND a.embedding_ref LIKE 'provider:%'
                        AND a.embedding_ref NOT LIKE 'provider:ffmpeg%' THEN 1 ELSE 0 END) AS real_video_count,
              SUM(CASE WHEN a.type = 'final-video' THEN 1 ELSE 0 END) AS final_count
            FROM projects p
            LEFT JOIN shots s ON s.project_id = p.id
            LEFT JOIN shot_versions sv ON sv.id = s.current_version_id
            LEFT JOIN assets a ON a.project_id = p.id AND a.file_path = sv.video_path
            WHERE p.archived = 0
            GROUP BY p.id
            """
        ).fetchall()
        to_archive = []
        for row in rows:
            if keep_project_id and row["id"] == keep_project_id:
                continue
            shot_count = row["shot_count"] or 0
            real_video_count = row["real_video_count"] or 0
            final_count = row["final_count"] or 0
            failed_or_empty = row["status"] in {"failed", "video_failed", "draft"}
            no_real_output = real_video_count == 0 and final_count == 0
            partial_failed = row["status"] == "failed" and real_video_count < shot_count
            if (failed_or_empty and no_real_output) or partial_failed:
                to_archive.append(row["id"])
        now = utc_now()
        conn.executemany(
            "UPDATE projects SET archived = 1, updated_at = ? WHERE id = ?",
            [(now, project_id) for project_id in to_archive],
        )
    return to_archive


def _delete_project_asset_file(project_id: str, public_path: str | None) -> None:
    if not public_path:
        return
    filename = public_path.rsplit("/", 1)[-1]
    if not filename:
        return
    project_dir = PROJECTS_DIR / project_id
    path = project_dir / filename
    try:
        workspace_root = PROJECTS_DIR.resolve()
        resolved = path.resolve()
        if resolved == workspace_root or workspace_root not in resolved.parents:
            return
        if path.exists() and path.is_file():
            path.unlink()
    except OSError:
        return


def rollback_shot_version(project_id: str, shot_id: str, version_id: str) -> dict:
    now = utc_now()
    with connect() as conn:
        shot = conn.execute(
            "SELECT * FROM shots WHERE id = ? AND project_id = ?",
            (shot_id, project_id),
        ).fetchone()
        version = conn.execute(
            "SELECT * FROM shot_versions WHERE id = ? AND shot_id = ?",
            (version_id, shot_id),
        ).fetchone()
        if not shot or not version:
            return {}
        status = "video_ready" if version["video_path"] else "keyframes_ready"
        camera = (version["camera_motion"] if "camera_motion" in version.keys() and version["camera_motion"] else shot["camera_motion"]) or ""
        conn.execute(
            """
            UPDATE shots
            SET description = ?, visual_prompt = ?, negative_prompt = ?, audio_prompt = ?,
                camera_motion = ?, status = ?, current_version_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                version["description"],
                version["visual_prompt"],
                version["negative_prompt"],
                version["audio_prompt"],
                camera,
                status,
                version_id,
                now,
                shot_id,
            ),
        )
        conn.execute(
            "UPDATE projects SET updated_at = ? WHERE id = ?",
            (now, project_id),
        )
    return get_project(project_id)
