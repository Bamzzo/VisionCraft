import subprocess
import uuid
from pathlib import Path

from ..config import PROJECTS_DIR
from ..database import connect, utc_now
from ..providers.capabilities import validate_video_generation
from ..providers.llm_provider import rewrite_video_prompt_for_safety
from ..providers.video_provider import VideoAssetRequest, generate_video_asset, refresh_remote_video_task
from ..services.job_service import update_job
from ..services.asset_service import public_asset_path


def prepare_shot_video_generation(
    project_id: str,
    shot_id: str,
    video_mode: str = "t2v",
    provider: str | None = None,
    model: str | None = None,
    duration_seconds: int | None = None,
    version_id: str | None = None,
) -> dict:
    with connect() as conn:
        project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        shot = conn.execute("SELECT * FROM shots WHERE id = ? AND project_id = ?", (shot_id, project_id)).fetchone()
        if not project or not shot:
            raise RuntimeError("Project or shot not found")
        version = conn.execute(
            "SELECT * FROM shot_versions WHERE id = ?",
            (version_id or shot["current_version_id"],),
        ).fetchone()
        if not version:
            raise RuntimeError("Current shot version not found")
        task_count = conn.execute(
            "SELECT COUNT(*) AS n FROM video_tasks WHERE version_id = ?",
            (version["id"],),
        ).fetchone()["n"]
        version = dict(version)
        project = dict(project)
        shot = dict(shot)

    plan = validate_video_generation(
        provider=provider,
        model=model,
        video_mode=_safe_video_mode(video_mode),
        duration_seconds=duration_seconds or project["duration_seconds"],
        aspect_ratio=project["aspect_ratio"],
        first_frame_path=version["first_frame_path"],
        last_frame_path=version["last_frame_path"],
    )
    same_spec = (
        (version["video_mode"] or "t2v") == plan["video_mode"]
        and (version["provider"] or "") == plan["provider"]
        and (version["model"] or "") == plan["model"]
    )
    already_targeted = bool(version["provider"] or version["model"])
    should_fork = bool(version["video_path"]) or task_count > 0 or (already_targeted and not same_spec)
    if should_fork:
        version = _fork_generation_version(project_id, shot, dict(version), plan)
    else:
        version = _stamp_generation_version(dict(version), plan)
    return {
        **plan,
        "project": dict(project),
        "shot": dict(shot),
        "version": version,
        "version_id": version["id"],
    }


def generate_shot_video(
    project_id: str,
    shot_id: str,
    job_id: str,
    video_mode: str = "t2v",
    provider: str | None = None,
    model: str | None = None,
    duration_seconds: int | None = None,
    version_id: str | None = None,
) -> None:
    update_job(job_id, "running", 8, "Preparing shot video generation")
    try:
        prepared = prepare_shot_video_generation(
            project_id,
            shot_id,
            video_mode=video_mode,
            provider=provider,
            model=model,
            duration_seconds=duration_seconds,
            version_id=version_id,
        )
        project = prepared["project"]
        shot = prepared["shot"]
        version = prepared["version"]

        update_job(job_id, "running", 24, f"Submitting {prepared['provider_label']} / {prepared['model_label']}")
        video_path = generate_video_asset(
            VideoAssetRequest(
                project_id=project_id,
                shot_id=shot_id,
                version_id=version["id"],
                title=shot["title"],
                description=version["description"] or shot["description"],
                prompt=version["visual_prompt"] or shot["visual_prompt"],
                first_frame_path=version["first_frame_path"],
                last_frame_path=version["last_frame_path"],
                negative_prompt=version["negative_prompt"] or shot["negative_prompt"],
                audio_prompt=version["audio_prompt"] or shot["audio_prompt"],
                video_mode=prepared["video_mode"],
                duration_seconds=prepared["duration_seconds"],
                aspect_ratio=project["aspect_ratio"],
                job_id=job_id,
                provider_override=prepared["provider"],
                model_override=prepared["model"],
            )
        )
        if video_path.status == "pending_remote":
            # Seedance 云端任务可能比本地请求更久，保留任务号供用户稍后回查。
            with connect() as conn:
                conn.execute(
                    "UPDATE shots SET status = ?, updated_at = ? WHERE id = ? AND project_id = ?",
                    ("video_waiting_remote", utc_now(), shot_id, project_id),
                )
            update_job(
                job_id,
                "waiting_remote",
                92,
                f"Seedance cloud task is still running: {video_path.remote_task_id}",
                video_path.message,
            )
            return
        with connect() as conn:
            asset = conn.execute(
                "SELECT embedding_ref FROM assets WHERE project_id = ? AND file_path = ?",
                (project_id, video_path.video_path),
            ).fetchone()
        source = asset["embedding_ref"] if asset else "unknown"
        update_job(job_id, "completed", 100, f"Video ready ({source}): {video_path.video_path}")
    except Exception as exc:
        with connect() as conn:
            conn.execute(
                "UPDATE shots SET status = ?, updated_at = ? WHERE id = ? AND project_id = ?",
                ("video_failed", utc_now(), shot_id, project_id),
            )
        update_job(job_id, "failed", 100, "Video generation failed", str(exc))


def safe_retry_shot_video(project_id: str, shot_id: str, job_id: str) -> None:
    update_job(job_id, "running", 5, "Diagnosing failed video prompt")
    try:
        with connect() as conn:
            project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            shot = conn.execute("SELECT * FROM shots WHERE id = ? AND project_id = ?", (shot_id, project_id)).fetchone()
            if not project or not shot:
                raise RuntimeError("Project or shot not found")
            version = conn.execute("SELECT * FROM shot_versions WHERE id = ?", (shot["current_version_id"],)).fetchone()
            if not version:
                raise RuntimeError("Current shot version not found")
            failed_task = conn.execute(
                """
                SELECT error_code, error_message, cloud_status
                FROM video_tasks
                WHERE project_id = ? AND shot_id = ? AND status = 'failed'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (project_id, shot_id),
            ).fetchone()

        error_context = ""
        if failed_task:
            error_context = f"{failed_task['error_code'] or ''} {failed_task['error_message'] or ''}".strip()
        # 安全重试会新建版本，失败提示词保留在历史中，便于复盘。
        rewrite = rewrite_video_prompt_for_safety(
            {
                "title": shot["title"],
                "description": version["description"] or shot["description"],
                "visual_prompt": version["visual_prompt"] or shot["visual_prompt"],
                "negative_prompt": version["negative_prompt"] or shot["negative_prompt"],
                "audio_prompt": version["audio_prompt"] or shot["audio_prompt"],
            },
            error_context,
        )
        update_job(job_id, "running", 22, "Creating safe rewritten shot version")
        _create_safe_retry_version(project_id, shot_id, rewrite)
        update_job(job_id, "running", 34, "Submitting safe T2V retry")
        generate_shot_video(project_id, shot_id, job_id, "t2v")
    except Exception as exc:
        with connect() as conn:
            conn.execute(
                "UPDATE shots SET status = ?, updated_at = ? WHERE id = ? AND project_id = ?",
                ("video_failed", utc_now(), shot_id, project_id),
            )
        update_job(job_id, "failed", 100, "Safe video retry failed", str(exc))


def _create_safe_retry_version(project_id: str, shot_id: str, rewrite: dict) -> str:
    now = utc_now()
    with connect() as conn:
        shot = conn.execute("SELECT * FROM shots WHERE id = ? AND project_id = ?", (shot_id, project_id)).fetchone()
        if not shot:
            raise RuntimeError("Shot not found")
        version = conn.execute("SELECT * FROM shot_versions WHERE id = ?", (shot["current_version_id"],)).fetchone()
        if not version:
            raise RuntimeError("Current shot version not found")
        version_number = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version FROM shot_versions WHERE shot_id = ?",
            (shot_id,),
        ).fetchone()["next_version"]
        version_id = f"version_{uuid.uuid4().hex[:10]}"
        negative_prompt = rewrite.get("negative_prompt") or version["negative_prompt"] or shot["negative_prompt"]
        conn.execute(
            """
            INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
             first_frame_path, last_frame_path, video_path, video_mode, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                shot_id,
                version_number,
                rewrite.get("description") or version["description"] or shot["description"],
                rewrite.get("visual_prompt") or version["visual_prompt"] or shot["visual_prompt"],
                negative_prompt,
                rewrite.get("audio_prompt") or version["audio_prompt"] or shot["audio_prompt"],
                version["first_frame_path"],
                version["last_frame_path"],
                None,
                "t2v",
                "video_safety_rewrite",
                now,
            ),
        )
        conn.execute(
            """
            UPDATE shots
            SET description = ?, visual_prompt = ?, negative_prompt = ?, audio_prompt = ?,
                status = ?, current_version_id = ?, retry_count = retry_count + 1, updated_at = ?
            WHERE id = ? AND project_id = ?
            """,
            (
                rewrite.get("description") or shot["description"],
                rewrite.get("visual_prompt") or shot["visual_prompt"],
                negative_prompt,
                rewrite.get("audio_prompt") or shot["audio_prompt"],
                "keyframes_ready",
                version_id,
                now,
                shot_id,
                project_id,
            ),
        )
        conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))
    return version_id


def generate_project_videos(project_id: str, job_id: str) -> None:
    update_job(job_id, "running", 5, "Preparing batch video generation")
    try:
        with connect() as conn:
            project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not project:
                raise RuntimeError("Project not found")
            rows = conn.execute(
                """
                SELECT
                  s.id AS shot_id,
                  s.title,
                  s.shot_index,
                  sv.id AS version_id,
                  sv.description,
                  sv.visual_prompt,
                  sv.negative_prompt,
                  sv.audio_prompt,
                  sv.first_frame_path,
                  sv.last_frame_path,
                  sv.video_path,
                  sv.video_mode,
                  a.embedding_ref AS video_ref
                FROM shots s
                JOIN shot_versions sv ON sv.id = s.current_version_id
                LEFT JOIN assets a ON a.project_id = s.project_id AND a.file_path = sv.video_path
                WHERE s.project_id = ?
                ORDER BY s.shot_index
                """,
                (project_id,),
            ).fetchall()
        if not rows:
            raise RuntimeError("No shots available for video generation")

        failures: list[str] = []
        pending: list[str] = []
        generated = 0
        total = len(rows)
        for index, row in enumerate(rows, start=1):
            base_progress = 8 + int((index - 1) / total * 84)
            if row["video_path"]:
                if _is_real_shot_video(row["video_ref"]):
                    update_job(job_id, "running", base_progress, f"Skipping existing real video {index}/{total}: {row['title']}")
                    continue
                with connect() as conn:
                    conn.execute("UPDATE shot_versions SET video_path = ? WHERE id = ?", (None, row["version_id"]))
                    conn.execute(
                        "UPDATE shots SET status = ?, updated_at = ? WHERE id = ?",
                        ("keyframes_ready", utc_now(), row["shot_id"]),
                    )
                update_job(job_id, "running", base_progress, f"Discarded placeholder video, regenerating {index}/{total}: {row['title']}")
            with connect() as conn:
                conn.execute(
                    "UPDATE shots SET status = ?, updated_at = ? WHERE id = ?",
                    ("video_running", utc_now(), row["shot_id"]),
                )
            update_job(job_id, "running", base_progress, f"Generating video {index}/{total}: {row['title']}")
            try:
                result = generate_video_asset(
                    VideoAssetRequest(
                        project_id=project_id,
                        shot_id=row["shot_id"],
                        version_id=row["version_id"],
                        title=row["title"],
                        description=row["description"],
                        prompt=row["visual_prompt"],
                        first_frame_path=row["first_frame_path"],
                        last_frame_path=row["last_frame_path"],
                        negative_prompt=row["negative_prompt"],
                        audio_prompt=row["audio_prompt"],
                        video_mode=row["video_mode"] or "t2v",
                        duration_seconds=project["duration_seconds"],
                        aspect_ratio=project["aspect_ratio"],
                        job_id=job_id,
                    )
                )
                if result.status == "pending_remote":
                    pending.append(f"{row['title']}: {result.remote_task_id}")
                    with connect() as conn:
                        conn.execute(
                            "UPDATE shots SET status = ?, updated_at = ? WHERE id = ?",
                            ("video_waiting_remote", utc_now(), row["shot_id"]),
                        )
                else:
                    generated += 1
            except Exception as exc:
                failures.append(f"{row['title']}: {exc}")
                with connect() as conn:
                    conn.execute(
                        "UPDATE shots SET status = ?, updated_at = ? WHERE id = ?",
                        ("video_failed", utc_now(), row["shot_id"]),
                    )

        if failures:
            update_job(
                job_id,
                "failed",
                100,
                f"Batch video generation finished with {len(failures)} failure(s)",
                "\n".join(failures),
            )
        elif pending:
            update_job(
                job_id,
                "waiting_remote",
                92,
                f"Batch video generation has {len(pending)} Seedance task(s) still running",
                "\n".join(pending),
            )
        else:
            update_job(job_id, "completed", 100, f"Batch video generation completed, generated {generated} new video(s)")
    except Exception as exc:
        update_job(job_id, "failed", 100, "Batch video generation failed", str(exc))


def refresh_project_video_tasks(project_id: str, job_id: str) -> None:
    update_job(job_id, "running", 8, "Checking Seedance cloud tasks")
    try:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT vt.*, s.title
                FROM video_tasks vt
                JOIN shots s ON s.id = vt.shot_id
                WHERE vt.project_id = ?
                  AND vt.status IN ('running', 'pending_remote')
                ORDER BY vt.updated_at DESC
                """,
                (project_id,),
            ).fetchall()
        if not rows:
            claimed = _claim_legacy_remote_tasks(project_id, job_id)
            if claimed:
                with connect() as conn:
                    rows = conn.execute(
                        """
                        SELECT vt.*, s.title
                        FROM video_tasks vt
                        JOIN shots s ON s.id = vt.shot_id
                        WHERE vt.project_id = ?
                          AND vt.status IN ('running', 'pending_remote')
                        ORDER BY vt.updated_at DESC
                        """,
                        (project_id,),
                    ).fetchall()
            if not rows:
                update_job(job_id, "completed", 100, "No pending Seedance cloud tasks")
                return

        completed: list[str] = []
        pending: list[str] = []
        failed: list[str] = []
        total = len(rows)
        for index, row in enumerate(rows, start=1):
            update_job(job_id, "running", 10 + int((index - 1) / total * 80), f"Checking {index}/{total}: {row['title']}")
            try:
                result = refresh_remote_video_task(row["id"])
                if result.status == "completed":
                    completed.append(f"{row['title']}: {result.video_path}")
                elif result.status == "pending_remote":
                    pending.append(f"{row['title']}: {result.remote_task_id}")
                    with connect() as conn:
                        conn.execute(
                            "UPDATE shots SET status = ?, updated_at = ? WHERE id = ?",
                            ("video_waiting_remote", utc_now(), row["shot_id"]),
                        )
            except Exception as exc:
                failed.append(f"{row['title']}: {exc}")
                with connect() as conn:
                    conn.execute(
                        "UPDATE shots SET status = ?, updated_at = ? WHERE id = ?",
                        ("video_failed", utc_now(), row["shot_id"]),
                    )

        if failed:
            update_job(
                job_id,
                "failed",
                100,
                f"Seedance task refresh finished with {len(failed)} failure(s)",
                "\n".join(failed),
            )
        elif pending:
            update_job(
                job_id,
                "waiting_remote",
                92,
                f"{len(pending)} Seedance task(s) are still running",
                "\n".join(pending),
            )
        else:
            update_job(job_id, "completed", 100, f"Seedance refresh completed, recovered {len(completed)} video(s)")
    except Exception as exc:
        update_job(job_id, "failed", 100, "Seedance task refresh failed", str(exc))


def _claim_legacy_remote_tasks(project_id: str, job_id: str) -> int:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT j.error_message, s.id AS shot_id, s.current_version_id, s.title, s.visual_prompt
            FROM jobs j
            JOIN shots s ON s.project_id = j.project_id
            WHERE j.project_id = ?
              AND j.error_message LIKE '%cgt-%'
              AND s.status = 'video_failed'
            ORDER BY j.updated_at DESC, s.shot_index DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchall()
        if not rows:
            return 0
        row = rows[0]
        remote_task_id = _extract_cloud_task_id(row["error_message"] or "")
        if not remote_task_id:
            return 0
        now = utc_now()
        conn.execute(
            """
            INSERT OR IGNORE INTO video_tasks
            (id, project_id, shot_id, version_id, job_id, provider, model, remote_task_id,
             status, cloud_status, prompt, submit_payload, status_payload, error_code, error_message,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"vt_{uuid.uuid4().hex[:10]}",
                project_id,
                row["shot_id"],
                row["current_version_id"],
                job_id,
                "ark",
                "doubao-seedance-2-0-260128",
                remote_task_id,
                "pending_remote",
                "running",
                row["visual_prompt"],
                "{}",
                "{}",
                "LEGACY_TIMEOUT",
                "Claimed from a pre-recovery timeout job.",
                now,
                now,
            ),
        )
        conn.execute(
            "UPDATE shots SET status = ?, updated_at = ? WHERE id = ?",
            ("video_waiting_remote", now, row["shot_id"]),
        )
    return 1


def _extract_cloud_task_id(text: str) -> str | None:
    marker = "cgt-"
    start = text.find(marker)
    if start < 0:
        return None
    end = start
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")
    while end < len(text) and text[end] in allowed:
        end += 1
    return text[start:end]


def assemble_project_video(project_id: str, job_id: str) -> None:
    update_job(job_id, "running", 8, "Preparing sequence assembly")
    try:
        with connect() as conn:
            project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not project:
                raise RuntimeError("Project not found")
            rows = conn.execute(
                """
                SELECT s.shot_index, s.title, sv.video_path, a.embedding_ref AS video_ref
                FROM shots s
                JOIN shot_versions sv ON sv.id = s.current_version_id
                LEFT JOIN assets a ON a.project_id = s.project_id AND a.file_path = sv.video_path
                WHERE s.project_id = ?
                ORDER BY s.shot_index
                """,
                (project_id,),
            ).fetchall()
        video_paths = [row["video_path"] for row in rows if row["video_path"]]
        if not video_paths:
            raise RuntimeError("No shot videos available for assembly")
        missing = [row["title"] for row in rows if not row["video_path"]]
        if missing:
            raise RuntimeError("Some shots have no video: " + ", ".join(missing))
        invalid = [row["title"] for row in rows if not _is_real_shot_video(row["video_ref"])]
        if invalid:
            # 只有每个镜头都有模型视频时才允许合成，避免占位素材混入成片。
            raise RuntimeError(
                "Some shot videos are placeholders or non-model outputs and cannot be assembled: "
                + ", ".join(invalid)
            )

        update_job(job_id, "running", 30, "Normalizing and concatenating video clips")
        asset_id = f"asset_{uuid.uuid4().hex[:10]}"
        filename = f"{asset_id}.mp4"
        output_path = PROJECTS_DIR / project_id / filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        concat_file = PROJECTS_DIR / project_id / f"{asset_id}_concat.txt"
        concat_file.write_text(
            "\n".join(f"file '{_ffmpeg_concat_path(_local_asset_path(project_id, path))}'" for path in video_paths),
            encoding="utf-8",
        )
        command = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-vf",
            "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
            "-r",
            "24",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        concat_file.unlink(missing_ok=True)
        video_path = public_asset_path(project_id, filename)
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO assets
                (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    project_id,
                    "final-video",
                    f"{project['title']} Final Cut",
                    f"Assembled final cut from {len(video_paths)} shot videos.",
                    "FFmpeg sequence assembly",
                    video_path,
                    "provider:ffmpeg:sequence-assembly",
                    utc_now(),
                ),
            )
        update_job(job_id, "completed", 100, f"Final cut ready: {video_path}")
    except Exception as exc:
        update_job(job_id, "failed", 100, "Sequence assembly failed", str(exc))


def _local_asset_path(project_id: str, public_path: str) -> Path:
    filename = public_path.rsplit("/", 1)[-1]
    return PROJECTS_DIR / project_id / filename


def _ffmpeg_concat_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")


def _is_real_shot_video(embedding_ref: str | None) -> bool:
    if not embedding_ref:
        return False
    if embedding_ref.startswith("fallback:"):
        return False
    if embedding_ref.startswith("provider:ffmpeg"):
        return False
    return embedding_ref.startswith("provider:")


def _safe_video_mode(video_mode: str | None) -> str:
    mode = (video_mode or "t2v").lower()
    return mode if mode in {"t2v", "i2v", "keyframes"} else "t2v"


def _stamp_generation_version(version: dict, plan: dict) -> dict:
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            UPDATE shot_versions
            SET video_mode = ?, provider = ?, model = ?
            WHERE id = ?
            """,
            (plan["video_mode"], plan["provider"], plan["model"], version["id"]),
        )
        conn.execute(
            "UPDATE shots SET status = ?, updated_at = ? WHERE id = ?",
            ("video_running", now, version["shot_id"]),
        )
    version["video_mode"] = plan["video_mode"]
    version["provider"] = plan["provider"]
    version["model"] = plan["model"]
    return version


def _fork_generation_version(project_id: str, shot: dict, version: dict, plan: dict) -> dict:
    now = utc_now()
    version_id = f"version_{uuid.uuid4().hex[:10]}"
    with connect() as conn:
        version_number = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version FROM shot_versions WHERE shot_id = ?",
            (shot["id"],),
        ).fetchone()["next_version"]
        conn.execute(
            """
            INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
             first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                shot["id"],
                version_number,
                version["description"] or shot["description"],
                version["visual_prompt"] or shot["visual_prompt"],
                version["negative_prompt"] or shot["negative_prompt"],
                version["audio_prompt"] or shot["audio_prompt"],
                version["first_frame_path"],
                version["last_frame_path"],
                None,
                plan["video_mode"],
                plan["provider"],
                plan["model"],
                "video_generation",
                now,
            ),
        )
        conn.execute(
            """
            UPDATE shots
            SET status = ?, current_version_id = ?, retry_count = retry_count + 1, updated_at = ?
            WHERE id = ? AND project_id = ?
            """,
            ("video_running", version_id, now, shot["id"], project_id),
        )
        conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))
    forked = dict(version)
    forked.update(
        {
            "id": version_id,
            "version_number": version_number,
            "video_path": None,
            "video_mode": plan["video_mode"],
            "provider": plan["provider"],
            "model": plan["model"],
            "created_by": "video_generation",
            "created_at": now,
        }
    )
    return forked
