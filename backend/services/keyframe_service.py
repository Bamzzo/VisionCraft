import uuid

from ..database import connect, utc_now
from ..providers.image_provider import ImageAssetRequest, generate_image_asset


IMAGE_ASSET_TYPES = {"character", "scene", "first-frame", "last-frame"}


def select_shot_keyframes(
    project_id: str,
    shot_id: str,
    first_frame_path: str | None = None,
    last_frame_path: str | None = None,
) -> dict:
    if not first_frame_path and not last_frame_path:
        raise RuntimeError("No keyframe path was provided")
    with connect() as conn:
        shot, version = _load_current_shot_version(conn, project_id, shot_id)
        # 手动选择关键帧会生成新版本，原始结果仍然保留用于回滚。
        first_path = _validated_image_path(conn, project_id, first_frame_path) if first_frame_path else version["first_frame_path"]
        last_path = _validated_image_path(conn, project_id, last_frame_path) if last_frame_path else version["last_frame_path"]
        if first_frame_path:
            first_path = _linked_asset(
                conn,
                project_id,
                "first-frame",
                f"Shot {shot['shot_index']} First Frame manual selection",
                f"Manual first-frame selection for {shot['title']}.",
                version["visual_prompt"],
                first_path,
                "manual:keyframe-select:first",
            )
        if last_frame_path:
            last_path = _linked_asset(
                conn,
                project_id,
                "last-frame",
                f"Shot {shot['shot_index']} Last Frame manual selection",
                f"Manual last-frame selection for {shot['title']}.",
                version["visual_prompt"],
                last_path,
                "manual:keyframe-select:last",
            )
        result = _create_keyframe_version(conn, project_id, shot, version, first_path, last_path, "manual_keyframe_select")
        if last_frame_path:
            result["propagated_next_shot"] = _propagate_next_first_frame(
                conn,
                project_id,
                shot["shot_index"],
                last_path,
                version["visual_prompt"],
                utc_now(),
            )
        else:
            result["propagated_next_shot"] = False
    return result


def redraw_shot_keyframes(project_id: str, shot_id: str, target: str = "both") -> dict:
    if target not in {"first", "last", "both"}:
        raise RuntimeError("target must be first, last, or both")
    with connect() as conn:
        project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        shot, version = _load_current_shot_version(conn, project_id, shot_id)
    first_path = version["first_frame_path"]
    last_path = version["last_frame_path"]
    if target in {"first", "both"}:
        first_asset_id = generate_image_asset(
            ImageAssetRequest(
                project_id=project_id,
                asset_type="first-frame",
                name=f"Shot {shot['shot_index']} First Frame redraw",
                description=f"Manual redraw for the opening frame of {shot['title']}.",
                prompt=f"{version['visual_prompt']}, opening frame, coherent continuity, aspect ratio {project['aspect_ratio']}",
                accent="#2563eb",
            )
        )
        first_path = _asset_path(first_asset_id)
    if target in {"last", "both"}:
        last_asset_id = generate_image_asset(
            ImageAssetRequest(
                project_id=project_id,
                asset_type="last-frame",
                name=f"Shot {shot['shot_index']} Last Frame redraw",
                description=f"Manual redraw for the closing frame of {shot['title']}.",
                prompt=f"{version['visual_prompt']}, closing frame, clear narrative continuation, aspect ratio {project['aspect_ratio']}",
                accent="#14b8a6",
            )
        )
        last_path = _asset_path(last_asset_id)

    with connect() as conn:
        shot, version = _load_current_shot_version(conn, project_id, shot_id)
        result = _create_keyframe_version(conn, project_id, shot, version, first_path, last_path, f"manual_redraw_{target}")
        # 尾帧变化后同步更新下一镜头首帧，这是项目的连续性规则。
        result["propagated_next_shot"] = (
            _propagate_next_first_frame(conn, project_id, shot["shot_index"], last_path, version["visual_prompt"], utc_now())
            if target in {"last", "both"}
            else False
        )
    return result


def _load_current_shot_version(conn, project_id: str, shot_id: str):
    shot = conn.execute("SELECT * FROM shots WHERE id = ? AND project_id = ?", (shot_id, project_id)).fetchone()
    if not shot:
        raise RuntimeError("Shot not found")
    version = conn.execute("SELECT * FROM shot_versions WHERE id = ?", (shot["current_version_id"],)).fetchone()
    if not version:
        raise RuntimeError("Current shot version not found")
    return shot, version


def _validated_image_path(conn, project_id: str, path: str | None) -> str:
    if not path:
        raise RuntimeError("Image path is empty")
    asset = conn.execute(
        "SELECT * FROM assets WHERE project_id = ? AND file_path = ?",
        (project_id, path),
    ).fetchone()
    if not asset:
        raise RuntimeError("Selected keyframe asset does not belong to this project")
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if asset["type"] not in IMAGE_ASSET_TYPES or suffix not in {"png", "jpg", "jpeg", "webp", "svg"}:
        raise RuntimeError("Selected asset is not an image keyframe candidate")
    return asset["file_path"]


def _create_keyframe_version(conn, project_id: str, shot, version, first_path: str, last_path: str, created_by: str) -> dict:
    version_number = conn.execute(
        "SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version FROM shot_versions WHERE shot_id = ?",
        (shot["id"],),
    ).fetchone()["next_version"]
    version_id = f"version_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    conn.execute(
        """
        INSERT INTO shot_versions
        (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
         first_frame_path, last_frame_path, video_path, video_mode, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            shot["id"],
            version_number,
            version["description"],
            version["visual_prompt"],
            version["negative_prompt"],
            version["audio_prompt"],
            first_path,
            last_path,
            None,
            version["video_mode"] or "t2v",
            created_by,
            now,
        ),
    )
    conn.execute(
        """
        UPDATE shots
        SET status = ?, current_version_id = ?, retry_count = retry_count + 1, updated_at = ?
        WHERE id = ? AND project_id = ?
        """,
        ("keyframes_ready", version_id, now, shot["id"], project_id),
    )
    conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))
    return {
        "version_id": version_id,
        "version_number": version_number,
        "first_frame_path": first_path,
        "last_frame_path": last_path,
    }


def _linked_asset(conn, project_id: str, asset_type: str, name: str, description: str, prompt: str, file_path: str, ref: str) -> str:
    asset_id = f"asset_{uuid.uuid4().hex[:10]}"
    conn.execute(
        """
        INSERT INTO assets
        (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (asset_id, project_id, asset_type, name, description, prompt, file_path, ref, utc_now()),
    )
    return file_path


def _propagate_next_first_frame(conn, project_id: str, shot_index: int, last_path: str, prompt: str, now: str) -> bool:
    next_shot = conn.execute(
        """
        SELECT s.id, s.current_version_id, s.title
        FROM shots s
        WHERE s.project_id = ? AND s.shot_index = ?
        """,
        (project_id, shot_index + 1),
    ).fetchone()
    if not next_shot or not next_shot["current_version_id"]:
        return False
    asset_id = f"asset_{uuid.uuid4().hex[:10]}"
    conn.execute(
        """
        INSERT INTO assets
        (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            asset_id,
            project_id,
            "first-frame",
            f"Shot {shot_index + 1} First Frame continuity update",
            f"First frame inherited from Shot {shot_index} manual keyframe update.",
            prompt,
            last_path,
            f"continuity:strict:manual-update-from-shot-{shot_index}",
            now,
        ),
    )
    conn.execute(
        "UPDATE shot_versions SET first_frame_path = ?, video_path = NULL WHERE id = ?",
        (last_path, next_shot["current_version_id"]),
    )
    conn.execute(
        "UPDATE shots SET status = ?, updated_at = ? WHERE id = ?",
        ("keyframes_ready", now, next_shot["id"]),
    )
    return True


def _asset_path(asset_id: str) -> str:
    with connect() as conn:
        row = conn.execute("SELECT file_path FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return row["file_path"] if row else ""
