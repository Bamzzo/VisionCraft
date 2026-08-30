import os
import shutil
import subprocess
import uuid
from pathlib import Path

from ..config import PROJECTS_DIR
from ..database import connect, utc_now
from ..providers.capabilities import validate_video_generation
from ..providers.llm_provider import rewrite_video_prompt_for_safety
from ..providers.video_provider import VideoAssetRequest, generate_video_asset, refresh_remote_video_task
from ..services.job_service import ACTIVE_JOB_STATUSES, create_job, list_active_jobs, update_job
from ..services.asset_service import public_asset_path


class AssemblyError(Exception):
    """合成前置校验失败，由路由转成中文 400，不得创建任务。"""

    def __init__(self, message: str, code: str = "ASSEMBLY_INVALID"):
        super().__init__(message)
        self.code = code
        self.message = message


def prepare_shot_video_generation(
    project_id: str,
    shot_id: str,
    video_mode: str = "t2v",
    provider: str | None = None,
    model: str | None = None,
    duration_seconds: int | None = None,
    version_id: str | None = None,
    allow_fork: bool = True,
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
        if version["shot_id"] != shot_id:
            raise RuntimeError("指定版本不属于该镜头")
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
    should_fork = allow_fork and (bool(version["video_path"]) or task_count > 0 or (already_targeted and not same_spec))
    if should_fork:
        version = _fork_generation_version(project_id, shot, dict(version), plan)
    elif allow_fork:
        version = _stamp_generation_version(dict(version), plan)
    else:
        now = utc_now()
        with connect() as conn:
            conn.execute(
                "UPDATE shots SET status = ?, updated_at = ? WHERE id = ?",
                ("video_running", now, shot_id),
            )
        version = dict(version)
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
    allow_fork: bool = True,
) -> None:
    update_job(job_id, "running", 8, "正在校验镜头生成参数", shot_id=shot_id, stage="prepare")
    try:
        prepared = prepare_shot_video_generation(
            project_id,
            shot_id,
            video_mode=video_mode,
            provider=provider,
            model=model,
            duration_seconds=duration_seconds,
            version_id=version_id,
            allow_fork=allow_fork,
        )
        project = prepared["project"]
        shot = prepared["shot"]
        version = prepared["version"]

        update_job(
            job_id,
            "running",
            24,
            f"正在提交至{prepared['provider_label']}",
            shot_id=shot_id,
            stage="submit_provider",
            detail={"provider": prepared["provider"], "model": prepared["model"]},
        )
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
                "云端任务仍在运行，正在回查同一任务，不会重复提交或重复计费",
                video_path.message,
                shot_id=shot_id,
                stage="waiting_remote",
                detail={"remote_task_id": video_path.remote_task_id, "provider": video_path.provider},
            )
            return
        with connect() as conn:
            asset = conn.execute(
                "SELECT embedding_ref FROM assets WHERE project_id = ? AND file_path = ?",
                (project_id, video_path.video_path),
            ).fetchone()
        source = asset["embedding_ref"] if asset else "unknown"
        update_job(
            job_id,
            "completed",
            100,
            "视频已生成，可在镜头卡片中预览",
            shot_id=shot_id,
            stage="persist_asset",
            event_type="asset.ready",
            detail={"source": source, "asset_path": video_path.video_path, "provider": video_path.provider, "model": video_path.model},
        )
    except Exception as exc:
        with connect() as conn:
            conn.execute(
                "UPDATE shots SET status = ?, updated_at = ? WHERE id = ? AND project_id = ?",
                ("video_failed", utc_now(), shot_id, project_id),
            )
        update_job(job_id, "failed", 100, "视频生成失败", str(exc), shot_id=shot_id, stage="failed")


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
    update_job(job_id, "running", 8, "正在回查同一云端任务，不会重复提交或重复计费", stage="poll_remote")
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
                update_job(job_id, "completed", 100, "没有待回查的云端任务", stage="completed")
                return

        completed: list[str] = []
        pending: list[str] = []
        failed: list[str] = []
        total = len(rows)
        for index, row in enumerate(rows, start=1):
            update_job(
                job_id,
                "running",
                10 + int((index - 1) / total * 80),
                f"正在回查同一云端任务：{row['title']}（不会重复提交）",
                shot_id=row["shot_id"],
                stage="poll_remote",
            )
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
                f"云端任务回查结束，{len(failed)} 个镜头失败",
                "\n".join(failed),
                stage="failed",
            )
        elif pending:
            update_job(
                job_id,
                "waiting_remote",
                92,
                "云端任务仍在运行，正在回查同一任务，不会重复提交或重复计费",
                "\n".join(pending),
                stage="waiting_remote",
            )
        else:
            update_job(
                job_id,
                "completed",
                100,
                f"云端回查完成，已恢复 {len(completed)} 个视频",
                stage="persist_asset",
                event_type="asset.ready",
            )
    except Exception as exc:
        update_job(job_id, "failed", 100, "云端任务回查失败", str(exc), stage="failed")


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


def _shot_label(index: int | None) -> str:
    try:
        return f"镜头 {int(index):02d}"
    except (TypeError, ValueError):
        return "镜头"


def _is_real_shot_video(embedding_ref: str | None) -> bool:
    if not embedding_ref:
        return False
    if embedding_ref.startswith("fallback:"):
        return False
    if embedding_ref.startswith("provider:ffmpeg"):
        return False
    return embedding_ref.startswith("provider:")


def _local_asset_path(project_id: str, public_path: str) -> Path:
    expected_prefix = f"/assets/{project_id}/"
    raw = str(public_path or "").replace("\\", "/")
    if not raw.startswith(expected_prefix):
        raise AssemblyError(f"视频路径不属于当前项目资产目录。")
    filename = raw[len(expected_prefix) :].split("/", 1)[0]
    if not filename or filename in {".", ".."} or ".." in filename:
        raise AssemblyError("视频路径无效。")
    project_dir = (PROJECTS_DIR / project_id).resolve()
    resolved = (project_dir / filename).resolve()
    if resolved.parent != project_dir:
        raise AssemblyError("视频路径超出当前项目资产目录。")
    return resolved


def _ffmpeg_executable() -> str | None:
    """定位 ffmpeg。优先 PATH，再检查工作区便携目录，不改系统 PATH。"""
    found = shutil.which("ffmpeg")
    if found:
        return found
    env_dir = os.environ.get("VISIONCRAFT_FFMPEG_DIR", "").strip()
    repo_root = Path(__file__).resolve().parents[2]
    candidates = []
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.extend(
        [
            repo_root.parent / ".tools" / "ffmpeg" / "bin",
            repo_root.parent / ".tools" / "ffmpeg",
            repo_root / ".tools" / "ffmpeg" / "bin",
            repo_root.parent / "tools" / "ffmpeg" / "bin",
            Path(r"C:\ffmpeg\bin"),
        ]
    )
    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for directory in candidates:
        for folder in (directory, directory / "bin"):
            exe = folder / exe_name
            if exe.is_file():
                return str(exe)
    return None


def _ffmpeg_concat_path(path: Path) -> str:
    # concat demuxer 用单引号包裹；路径中的单引号按 ffmpeg 规则转义。
    return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")


def _latest_final_assets(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM assets
            WHERE project_id = ? AND type = 'final-video'
            ORDER BY created_at DESC
            """,
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _active_assembly_job(project_id: str) -> dict | None:
    jobs = [
        job
        for job in list_active_jobs(project_id)
        if job.get("type") == "sequence_assembly" and job.get("status") in ACTIVE_JOB_STATUSES
    ]
    return jobs[0] if jobs else None


def validate_assembly(project_id: str) -> dict:
    """静态校验当前有效镜头是否可以合成。不创建任务、不调用 FFmpeg。"""
    errors: list[str] = []
    shot_rows: list[dict] = []
    video_paths: list[str] = []
    with connect() as conn:
        project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not project:
            return {
                "ok": False,
                "errors": ["项目不存在。"],
                "shots": [],
                "video_paths": [],
                "shot_count": 0,
                "ready_count": 0,
                "stale": False,
                "current_final": None,
                "history": [],
                "active_job": None,
                "project": None,
                "ffmpeg_available": bool(_ffmpeg_executable()),
            }
        rows = conn.execute(
            """
            SELECT s.id AS shot_id, s.shot_index, s.title, s.status, s.current_version_id,
                   sv.id AS version_id, sv.video_path,
                   a.id AS asset_id, a.project_id AS asset_project_id, a.type AS asset_type,
                   a.embedding_ref AS video_ref, a.file_path AS asset_path
            FROM shots s
            LEFT JOIN shot_versions sv ON sv.id = s.current_version_id
            LEFT JOIN assets a ON a.project_id = s.project_id AND a.file_path = sv.video_path
            WHERE s.project_id = ?
            ORDER BY s.shot_index
            """,
            (project_id,),
        ).fetchall()
        foreign = conn.execute(
            """
            SELECT a.project_id, a.file_path
            FROM shots s
            JOIN shot_versions sv ON sv.id = s.current_version_id
            JOIN assets a ON a.file_path = sv.video_path
            WHERE s.project_id = ? AND a.project_id != s.project_id
            """,
            (project_id,),
        ).fetchall()

    if not rows:
        errors.append("项目还没有制作镜头，请先完成分镜并生成镜头视频。")

    seen_index: set[int] = set()
    for row in rows:
        item = dict(row)
        label = _shot_label(item.get("shot_index"))
        issue = ""
        index = item.get("shot_index")
        if index in seen_index:
            issue = f"{label} 的镜头顺序重复，请检查分镜排序后再合成。"
        elif index is not None:
            seen_index.add(index)
        if not issue and not item.get("current_version_id"):
            issue = f"{label} 尚未生成视频，请先完成该镜头的视频生成。"
        elif not issue and item.get("status") in {"video_invalid"}:
            issue = f"{label} 的当前版本已失效，请重新生成当前版本。"
        elif not issue and not item.get("video_path"):
            issue = f"{label} 尚未生成视频，请先完成该镜头的视频生成。"
        elif not issue and (not item.get("asset_id") or item.get("asset_project_id") != project_id):
            if any(str(foreign_row["file_path"]) == str(item.get("video_path")) for foreign_row in foreign):
                issue = f"{label} 的视频属于其他项目，不能进入成片。"
            else:
                issue = f"{label} 的视频不属于当前项目，不能进入成片。"
        elif not issue and item.get("asset_type") not in {None, "video"}:
            issue = f"{label} 的当前素材不是镜头视频，不能进入成片。"
        elif not issue and not _is_real_shot_video(item.get("video_ref")):
            issue = f"{label} 使用的是占位视频，不能进入成片，请替换为模型生成视频。"
        else:
            try:
                local = _local_asset_path(project_id, item["video_path"])
            except AssemblyError:
                issue = f"{label} 的视频文件不存在或已失效，请重新生成当前版本。"
                local = None
            if not issue and (local is None or not local.is_file() or local.stat().st_size <= 0):
                issue = f"{label} 的视频文件不存在或已失效，请重新生成当前版本。"
            elif not issue:
                video_paths.append(item["video_path"])
        if issue:
            errors.append(issue)
        item["issue"] = issue
        item["ready"] = not issue
        shot_rows.append(item)

    finals = _latest_final_assets(project_id)
    current_final = dict(finals[0]) if finals else None
    history = [dict(item) for item in finals[1:]]
    active = _active_assembly_job(project_id)
    return {
        "ok": not errors,
        "errors": errors,
        "shots": shot_rows,
        "video_paths": video_paths,
        "shot_count": len(shot_rows),
        "ready_count": sum(1 for item in shot_rows if item["ready"]),
        "stale": bool(project["assembly_stale"]),
        "current_final": current_final,
        "history": history,
        "active_job": dict(active) if active else None,
        "project": dict(project),
        "ffmpeg_available": bool(_ffmpeg_executable()),
    }


def get_assembly_status(project_id: str) -> dict:
    report = validate_assembly(project_id)
    current = report["current_final"]
    return {
        "ok": report["ok"],
        "can_assemble": report["ok"] and report["active_job"] is None,
        "errors": report["errors"],
        "shot_count": report["shot_count"],
        "ready_count": report["ready_count"],
        "stale": report["stale"],
        "ffmpeg_available": report["ffmpeg_available"],
        "audio_scope": "video_only",
        "audio_note": "当前合成只拼接并统一视频流规格，不混音、不加旁白或配乐。",
        "active_job": (
            {
                "id": report["active_job"]["id"],
                "status": report["active_job"]["status"],
                "progress": report["active_job"]["progress"],
                "message": report["active_job"]["message"],
                "stage": report["active_job"].get("stage") or "",
                "error_message": report["active_job"].get("error_message") or "",
            }
            if report["active_job"]
            else None
        ),
        "current_final": (
            {
                "id": current["id"],
                "file_path": current["file_path"],
                "created_at": current["created_at"],
                "description": current.get("description") or "",
                "shot_count": _shot_count_from_description(current.get("description") or ""),
            }
            if current
            else None
        ),
        "history": [
            {
                "id": item["id"],
                "file_path": item["file_path"],
                "created_at": item["created_at"],
                "description": item.get("description") or "",
            }
            for item in report["history"]
        ],
        "shots": [
            {
                "shot_id": item.get("shot_id"),
                "shot_index": item.get("shot_index"),
                "title": item.get("title") or "",
                "ready": item.get("ready"),
                "issue": item.get("issue") or "",
                "video_path": item.get("video_path") or "",
                "status": item.get("status") or "",
            }
            for item in report["shots"]
        ],
    }


def _shot_count_from_description(description: str) -> int | None:
    marker = "from "
    if marker not in description:
        return None
    try:
        return int(description.split("from ", 1)[1].split(" ", 1)[0])
    except (IndexError, ValueError):
        return None


def enqueue_project_assembly(project_id: str) -> dict:
    """入队前完成静态校验。已有活动任务时复用，不创建并发合成。"""
    report = validate_assembly(project_id)
    if not report["ok"]:
        raise AssemblyError(report["errors"][0] if report["errors"] else "当前项目不满足合成条件。")
    active = report["active_job"]
    if active:
        return {
            "job_id": active["id"],
            "status": active["status"],
            "reused": True,
            "message": "成片合成任务已在进行中，已返回现有任务。",
            "shot_count": report["shot_count"],
        }
    job_id = create_job(project_id, "sequence_assembly", "成片合成已排队", stage="queued")
    return {
        "job_id": job_id,
        "status": "queued",
        "reused": False,
        "message": "成片合成已排队",
        "shot_count": report["shot_count"],
    }


def assemble_project_video(project_id: str, job_id: str) -> None:
    concat_file: Path | None = None
    output_path: Path | None = None
    update_job(job_id, "running", 8, "正在检查成片合成条件", stage="validate_inputs")
    try:
        report = validate_assembly(project_id)
        if not report["ok"]:
            raise RuntimeError(" ".join(report["errors"]))
        project = report["project"]
        video_paths = report["video_paths"]
        update_job(job_id, "running", 30, "正在统一规格并合成镜头视频", stage="concat")
        asset_id = f"asset_{uuid.uuid4().hex[:10]}"
        filename = f"{asset_id}.mp4"
        output_path = PROJECTS_DIR / project_id / filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        concat_file = PROJECTS_DIR / project_id / f"{asset_id}_concat.txt"
        concat_file.write_text(
            "\n".join(f"file '{_ffmpeg_concat_path(_local_asset_path(project_id, path))}'" for path in video_paths),
            encoding="utf-8",
        )
        ffmpeg_exe = _ffmpeg_executable()
        if not ffmpeg_exe:
            raise RuntimeError("本机未找到 FFmpeg，无法合成成片。请安装 ffmpeg 与 ffprobe 后重试。")
        command = [
            ffmpeg_exe,
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
            "-an",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        try:
            completed = subprocess.run(command, check=False, capture_output=True, text=True)
        finally:
            if concat_file is not None:
                concat_file.unlink(missing_ok=True)
                concat_file = None
        if completed.returncode != 0:
            raise RuntimeError("成片合成失败，请检查各镜头视频是否完整后重试。")
        if output_path is None or not output_path.is_file() or output_path.stat().st_size == 0:
            if output_path is not None:
                output_path.unlink(missing_ok=True)
            raise RuntimeError("成片文件无效，未登记资产。请重新合成。")
        video_path = public_asset_path(project_id, filename)
        shot_count = len(video_paths)
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
                    f"Assembled final cut from {shot_count} shot videos.",
                    "FFmpeg sequence assembly (video only)",
                    video_path,
                    "provider:ffmpeg:sequence-assembly",
                    utc_now(),
                ),
            )
            conn.execute(
                "UPDATE projects SET assembly_stale = 0, updated_at = ? WHERE id = ?",
                (utc_now(), project_id),
            )
        update_job(
            job_id,
            "completed",
            100,
            "成片已生成，可在工作区预览或下载",
            stage="persist_asset",
            event_type="asset.ready",
            detail={"asset_path": video_path, "shot_count": shot_count},
        )
    except Exception as exc:
        if concat_file is not None:
            concat_file.unlink(missing_ok=True)
        if output_path is not None and output_path.exists() and output_path.stat().st_size == 0:
            output_path.unlink(missing_ok=True)
        message = str(exc)
        if "ffmpeg" in message.lower() and "成片合成失败" not in message:
            message = "成片合成失败，请检查各镜头视频是否完整后重试。"
        update_job(job_id, "failed", 100, "成片合成失败", message, stage="failed")


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
