import uuid

from ..database import connect, utc_now
from ..providers.image_provider import ImageAssetRequest, generate_image_asset
from ..services.asset_service import create_linked_asset


GLOBAL_HINTS = ("从现在", "现在开始", "以后", "后续", "所有", "全局", "一直", "都", "统一", "永久")


def parse_feedback(user_text: str) -> dict:
    scope = "global" if any(hint in user_text for hint in GLOBAL_HINTS) else "local"
    target = "current_shot" if scope == "local" else "global_constraints"
    positive = user_text.strip()
    negative = "avoid inconsistent style, avoid broken character continuity"
    reason = (
        "检测到持续性或全局性表达，因此写入全局约束。"
        if scope == "global"
        else "未检测到持续性表达，因此只作用于当前镜头。"
    )
    return {
        "scope": scope,
        "target": target,
        "positive_prompt": positive,
        "negative_prompt": negative,
        "reason": reason,
    }


def apply_feedback(project_id: str, shot_id: str, user_text: str) -> dict:
    parsed = parse_feedback(user_text)
    feedback_id = f"feedback_{uuid.uuid4().hex[:10]}"
    now = utc_now()

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO feedback_records
            (id, project_id, shot_id, user_text, scope, target, positive_prompt, negative_prompt, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feedback_id,
                project_id,
                shot_id,
                user_text,
                parsed["scope"],
                parsed["target"],
                parsed["positive_prompt"],
                parsed["negative_prompt"],
                parsed["reason"],
                now,
            ),
        )

        if parsed["scope"] == "global":
            constraint_id = f"constraint_{uuid.uuid4().hex[:10]}"
            conn.execute(
                """
                INSERT INTO global_constraints
                (id, project_id, target, positive_prompt, negative_prompt, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    constraint_id,
                    project_id,
                    parsed["target"],
                    parsed["positive_prompt"],
                    parsed["negative_prompt"],
                    "user_feedback",
                    now,
                ),
            )

    version_result = regenerate_shot_from_feedback(project_id, shot_id, parsed)

    return {"id": feedback_id, **parsed, **version_result}


def regenerate_shot_from_feedback(project_id: str, shot_id: str, parsed: dict) -> dict:
    with connect() as conn:
        project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        shot = conn.execute("SELECT * FROM shots WHERE id = ? AND project_id = ?", (shot_id, project_id)).fetchone()
        if not project or not shot:
            raise RuntimeError("Project or shot not found")
        prev_version = conn.execute(
            """
            SELECT sv.last_frame_path
            FROM shots s
            JOIN shot_versions sv ON sv.id = s.current_version_id
            WHERE s.project_id = ? AND s.shot_index = ?
            """,
            (project_id, shot["shot_index"] - 1),
        ).fetchone()
        version_number = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version FROM shot_versions WHERE shot_id = ?",
            (shot_id,),
        ).fetchone()["next_version"]

    new_visual = f"{shot['visual_prompt']}, user revision: {parsed['positive_prompt']}"
    negative_prompt = f"{shot['negative_prompt']}, {parsed['negative_prompt']}"
    previous_last_path = prev_version["last_frame_path"] if prev_version else ""

    if previous_last_path:
        first_asset_id = create_linked_asset(
            project_id,
            "first-frame",
            f"Shot {shot['shot_index']} First Frame v{version_number}",
            f"Continuity frame inherited after feedback. {shot['description']}",
            new_visual,
            previous_last_path,
            f"continuity:strict:feedback-from-shot-{shot['shot_index'] - 1}",
        )
    else:
        first_asset_id = generate_image_asset(
            ImageAssetRequest(
                project_id=project_id,
                asset_type="first-frame",
                name=f"Shot {shot['shot_index']} First Frame v{version_number}",
                description=f"Feedback revision: {parsed['positive_prompt']}",
                prompt=new_visual,
                accent="#2563eb",
            )
        )

    last_asset_id = generate_image_asset(
        ImageAssetRequest(
            project_id=project_id,
            asset_type="last-frame",
            name=f"Shot {shot['shot_index']} Last Frame v{version_number}",
            description=f"Feedback revision: {parsed['positive_prompt']}",
            prompt=new_visual,
            accent="#14b8a6",
        )
    )

    now = utc_now()
    version_id = f"version_{uuid.uuid4().hex[:10]}"
    with connect() as conn:
        first_path = _asset_path(conn, first_asset_id)
        last_path = _asset_path(conn, last_asset_id)
        conn.execute(
            """
            INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
             first_frame_path, last_frame_path, video_path, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                shot_id,
                version_number,
                shot["description"],
                new_visual,
                negative_prompt,
                shot["audio_prompt"],
                first_path,
                last_path,
                None,
                "user_feedback",
                now,
            ),
        )
        conn.execute(
            """
            UPDATE shots
            SET visual_prompt = ?, negative_prompt = ?, status = ?, current_version_id = ?, retry_count = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_visual, negative_prompt, "keyframes_ready", version_id, shot["retry_count"] + 1, now, shot_id),
        )
        propagated = _propagate_next_first_frame(conn, project_id, shot["shot_index"], last_path, new_visual, now)

    return {
        "version_id": version_id,
        "version_number": version_number,
        "first_frame_path": first_path,
        "last_frame_path": last_path,
        "propagated_next_shot": propagated,
    }


def _propagate_next_first_frame(conn, project_id: str, shot_index: int, last_path: str, prompt: str, now: str) -> bool:
    next_shot = conn.execute(
        """
        SELECT s.id, s.current_version_id
        FROM shots s
        WHERE s.project_id = ? AND s.shot_index = ?
        """,
        (project_id, shot_index + 1),
    ).fetchone()
    if not next_shot or not next_shot["current_version_id"]:
        return False

    asset_id = f"asset_{uuid.uuid4().hex[:10]}"
    first_path = last_path
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
            f"Shot {shot_index + 1} First Frame continuity refresh",
            f"First frame refreshed from Shot {shot_index} feedback revision.",
            prompt,
            first_path,
            f"continuity:strict:feedback-refresh-from-shot-{shot_index}",
            now,
        ),
    )
    conn.execute(
        "UPDATE shot_versions SET first_frame_path = ?, video_path = ? WHERE id = ?",
        (first_path, None, next_shot["current_version_id"]),
    )
    conn.execute(
        "UPDATE shots SET status = ?, updated_at = ? WHERE id = ?",
        ("keyframes_ready", now, next_shot["id"]),
    )
    return True


def _asset_path(conn, asset_id: str) -> str:
    row = conn.execute("SELECT file_path FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return row["file_path"] if row else ""
