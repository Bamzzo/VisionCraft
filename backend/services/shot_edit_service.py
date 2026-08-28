"""Shot edit drafts, immutable versions, and pointer-only rollback."""
from __future__ import annotations

import uuid

from ..database import connect, utc_now
from ..providers.capabilities import CapabilityError, validate_video_generation
from ..services.project_service import rollback_shot_version as _rollback_pointer


class ShotEditError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


DRAFT_FIELDS = (
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


def save_shot_draft(project_id: str, shot_id: str, payload: dict) -> dict:
    shot, version = _load_shot(project_id, shot_id)
    current = _draft_from_version(shot, version)
    merged = {**current, **_pick_fields(payload)}
    _upsert_draft(project_id, shot_id, merged)
    return get_shot_editor(project_id, shot_id)


def freeze_shot_version(project_id: str, shot_id: str, payload: dict | None = None, *, created_by: str = "shot_edit") -> dict:
    if payload:
        save_shot_draft(project_id, shot_id, payload)
    editor = get_shot_editor(project_id, shot_id)
    draft = editor["draft"]
    current = editor["current_version"]
    if current and _fingerprint(draft) == _fingerprint(_draft_from_version(editor["shot"], current)):
        return {
            "created": False,
            "version_id": current["id"],
            "version_number": current["version_number"],
            "reason": "没有未保存的实质修改，沿用当前版本",
            "editor": editor,
        }
    version = _insert_version(project_id, shot_id, draft, created_by, _change_summary(current, draft))
    _mark_assembly_stale(project_id)
    return {
        "created": True,
        "version_id": version["id"],
        "version_number": version["version_number"],
        "reason": "已冻结为新的不可变镜头版本",
        "editor": get_shot_editor(project_id, shot_id),
    }


def prepare_version_for_generation(project_id: str, shot_id: str, payload: dict | None = None, version_id: str | None = None) -> dict:
    if version_id:
        version = _require_version(project_id, shot_id, version_id)
        draft = _draft_from_version(_load_shot(project_id, shot_id)[0], version)
        _validate_generation(project_id, draft)
        if version.get("video_path") or _version_has_task(version["id"]):
            version = _insert_version(project_id, shot_id, draft, "video_generation", "局部重生成（保留旧版本视频）")
            _mark_assembly_stale(project_id)
        return dict(version)
    if payload:
        save_shot_draft(project_id, shot_id, payload)
    editor = get_shot_editor(project_id, shot_id)
    _validate_generation(project_id, editor["draft"])
    frozen = freeze_shot_version(project_id, shot_id, None, created_by="video_generation")
    version = _require_version(project_id, shot_id, frozen["version_id"])
    if version.get("video_path") or _version_has_task(version["id"]):
        version = _insert_version(project_id, shot_id, editor["draft"], "video_generation", "局部重生成（保留旧版本视频）")
        _mark_assembly_stale(project_id)
    return dict(version)


def rollback_shot_to_version(project_id: str, shot_id: str, version_id: str) -> dict:
    _require_version(project_id, shot_id, version_id)
    project = _rollback_pointer(project_id, shot_id, version_id)
    if not project:
        raise ShotEditError("VERSION_NOT_FOUND", "找不到可回滚的镜头版本。")
    shot, version = _load_shot(project_id, shot_id)
    _upsert_draft(project_id, shot_id, _draft_from_version(shot, version))
    _mark_assembly_stale(project_id)
    return get_shot_editor(project_id, shot_id)


def get_shot_editor(project_id: str, shot_id: str) -> dict:
    shot, version = _load_shot(project_id, shot_id)
    draft = _load_draft(shot_id) or _draft_from_version(shot, version)
    versions = _list_versions(shot_id)
    current = next((item for item in versions if item["id"] == shot["current_version_id"]), version and dict(version))
    return {
        "shot": dict(shot),
        "draft": draft,
        "current_version": dict(current) if current else None,
        "versions": versions,
        "has_unsaved_changes": bool(current) and _fingerprint(draft) != _fingerprint(_draft_from_version(shot, current)),
        "assembly_stale": _assembly_stale(project_id),
    }


def _validate_generation(project_id: str, draft: dict) -> None:
    with connect() as conn:
        project = conn.execute("SELECT aspect_ratio FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not project:
        raise ShotEditError("PROJECT_NOT_FOUND", "项目不存在。")
    try:
        validate_video_generation(
            provider=draft.get("provider"),
            model=draft.get("model"),
            video_mode=draft.get("video_mode") or "t2v",
            duration_seconds=int(draft.get("duration_seconds") or 5),
            aspect_ratio=project["aspect_ratio"],
            first_frame_path=draft.get("first_frame_path"),
            last_frame_path=draft.get("last_frame_path"),
        )
    except CapabilityError as exc:
        raise ShotEditError(exc.code, str(exc)) from exc


def _load_shot(project_id: str, shot_id: str):
    with connect() as conn:
        shot = conn.execute("SELECT * FROM shots WHERE id = ? AND project_id = ?", (shot_id, project_id)).fetchone()
        if not shot:
            raise ShotEditError("SHOT_NOT_FOUND", "镜头不存在或不属于该项目。")
        version = conn.execute("SELECT * FROM shot_versions WHERE id = ?", (shot["current_version_id"],)).fetchone()
        if not version:
            raise ShotEditError("VERSION_NOT_FOUND", "当前镜头版本不存在。")
    return dict(shot), dict(version)


def _require_version(project_id: str, shot_id: str, version_id: str) -> dict:
    with connect() as conn:
        shot = conn.execute("SELECT id FROM shots WHERE id = ? AND project_id = ?", (shot_id, project_id)).fetchone()
        version = conn.execute("SELECT * FROM shot_versions WHERE id = ? AND shot_id = ?", (version_id, shot_id)).fetchone()
    if not shot:
        raise ShotEditError("SHOT_NOT_FOUND", "镜头不存在或不属于该项目。")
    if not version:
        raise ShotEditError("VERSION_MISMATCH", "指定版本不属于当前镜头或项目。")
    return dict(version)


def _list_versions(shot_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM shot_versions WHERE shot_id = ? ORDER BY version_number DESC", (shot_id,)).fetchall()
    return [dict(row) for row in rows]


def _load_draft(shot_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM shot_drafts WHERE shot_id = ?", (shot_id,)).fetchone()
    return _row_to_draft(row) if row else None


def _upsert_draft(project_id: str, shot_id: str, draft: dict) -> None:
    now = utc_now()
    values = _draft_values(draft)
    with connect() as conn:
        existing = conn.execute("SELECT shot_id FROM shot_drafts WHERE shot_id = ?", (shot_id,)).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE shot_drafts SET
                  description=?, camera_motion=?, visual_prompt=?, negative_prompt=?, audio_prompt=?,
                  video_mode=?, provider=?, model=?, duration_seconds=?, first_frame_path=?, last_frame_path=?,
                  reference_frame_path=?, updated_at=?
                WHERE shot_id=?
                """,
                (*values, now, shot_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO shot_drafts
                (shot_id, project_id, description, camera_motion, visual_prompt, negative_prompt, audio_prompt,
                 video_mode, provider, model, duration_seconds, first_frame_path, last_frame_path, reference_frame_path, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (shot_id, project_id, *values, now),
            )


def _insert_version(project_id: str, shot_id: str, draft: dict, created_by: str, summary: str) -> dict:
    now = utc_now()
    version_id = f"version_{uuid.uuid4().hex[:10]}"
    with connect() as conn:
        version_number = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 AS n FROM shot_versions WHERE shot_id = ?",
            (shot_id,),
        ).fetchone()["n"]
        conn.execute(
            """
            INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
             first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at,
             camera_motion, duration_seconds, reference_frame_path, change_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                shot_id,
                version_number,
                draft.get("description") or "",
                draft.get("visual_prompt") or "",
                draft.get("negative_prompt") or "",
                draft.get("audio_prompt") or "",
                draft.get("first_frame_path"),
                draft.get("last_frame_path"),
                None,
                draft.get("video_mode") or "t2v",
                draft.get("provider"),
                draft.get("model"),
                created_by,
                now,
                draft.get("camera_motion") or "",
                int(draft.get("duration_seconds") or 5),
                draft.get("reference_frame_path"),
                summary,
            ),
        )
        conn.execute(
            """
            UPDATE shots SET description=?, visual_prompt=?, negative_prompt=?, audio_prompt=?, camera_motion=?,
              status=?, current_version_id=?, updated_at=?
            WHERE id=? AND project_id=?
            """,
            (
                draft.get("description") or "",
                draft.get("visual_prompt") or "",
                draft.get("negative_prompt") or "",
                draft.get("audio_prompt") or "",
                draft.get("camera_motion") or "",
                "keyframes_ready",
                version_id,
                now,
                shot_id,
                project_id,
            ),
        )
        row = conn.execute("SELECT * FROM shot_versions WHERE id = ?", (version_id,)).fetchone()
    return dict(row)


def _draft_from_version(shot: dict, version: dict) -> dict:
    project_duration = None
    with connect() as conn:
        project = conn.execute("SELECT duration_seconds FROM projects WHERE id = ?", (shot["project_id"],)).fetchone()
        if project:
            project_duration = project["duration_seconds"]
    return {
        "description": version.get("description") or shot.get("description") or "",
        "camera_motion": version.get("camera_motion") or shot.get("camera_motion") or "",
        "visual_prompt": version.get("visual_prompt") or shot.get("visual_prompt") or "",
        "negative_prompt": version.get("negative_prompt") or shot.get("negative_prompt") or "",
        "audio_prompt": version.get("audio_prompt") or shot.get("audio_prompt") or "",
        "video_mode": version.get("video_mode") or "t2v",
        "provider": version.get("provider"),
        "model": version.get("model"),
        "duration_seconds": int(version.get("duration_seconds") or project_duration or 5),
        "first_frame_path": version.get("first_frame_path"),
        "last_frame_path": version.get("last_frame_path"),
        "reference_frame_path": version.get("reference_frame_path"),
    }


def _row_to_draft(row) -> dict:
    item = dict(row)
    return {key: item.get(key) for key in DRAFT_FIELDS}


def _draft_values(draft: dict) -> tuple:
    return tuple(draft.get(key) if key != "duration_seconds" else int(draft.get(key) or 5) for key in DRAFT_FIELDS)


def _pick_fields(payload: dict) -> dict:
    return {key: payload[key] for key in DRAFT_FIELDS if key in payload}


def _fingerprint(draft: dict) -> tuple:
    return tuple("" if draft.get(key) is None else str(draft.get(key)) for key in DRAFT_FIELDS)


def _change_summary(previous: dict | None, draft: dict) -> str:
    if not previous:
        return "首个编辑版本"
    changed = [key for key in DRAFT_FIELDS if str(previous.get(key) or "") != str(draft.get(key) or "")]
    if not changed:
        return "配置未变"
    labels = {
        "description": "描述",
        "camera_motion": "运镜",
        "visual_prompt": "视觉提示",
        "video_mode": "模式",
        "provider": "Provider",
        "model": "模型",
        "duration_seconds": "时长",
        "first_frame_path": "首帧",
        "last_frame_path": "尾帧",
        "reference_frame_path": "参考图",
    }
    return "更新：" + "、".join(labels.get(key, key) for key in changed[:6])


def _version_has_task(version_id: str) -> bool:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM video_tasks WHERE version_id = ?", (version_id,)).fetchone()
    return int(row["n"]) > 0


def _assembly_stale(project_id: str) -> bool:
    with connect() as conn:
        project = conn.execute("SELECT assembly_stale FROM projects WHERE id = ?", (project_id,)).fetchone()
    return bool(project and project["assembly_stale"])


def _mark_assembly_stale(project_id: str) -> None:
    with connect() as conn:
        finals = conn.execute(
            "SELECT COUNT(*) AS n FROM assets WHERE project_id = ? AND type = 'final-video'",
            (project_id,),
        ).fetchone()
        if int(finals["n"] or 0) == 0:
            return
        conn.execute("UPDATE projects SET assembly_stale = 1, updated_at = ? WHERE id = ?", (utc_now(), project_id))
