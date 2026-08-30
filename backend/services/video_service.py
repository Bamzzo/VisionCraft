import json
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


def _local_asset_path(project_id: str, public_path: str, *, label: str = "视频") -> Path:
    expected_prefix = f"/assets/{project_id}/"
    raw = str(public_path or "").replace("\\", "/")
    if not raw.startswith(expected_prefix):
        raise AssemblyError(f"{label}路径不属于当前项目资产目录。")
    filename = raw[len(expected_prefix) :].split("/", 1)[0]
    if not filename or filename in {".", ".."} or ".." in filename:
        raise AssemblyError(f"{label}路径无效。")
    project_dir = (PROJECTS_DIR / project_id).resolve()
    resolved = (project_dir / filename).resolve()
    if resolved.parent != project_dir:
        raise AssemblyError(f"{label}路径超出当前项目资产目录。")
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


DEFAULT_ASSEMBLY_SETTINGS = {
    "subtitle_enabled": False,
    "subtitle_text": "",
    "subtitle_srt_path": "",
    "audio_enabled": False,
    "audio_asset_path": "",
    "audio_volume": 0.4,
    "keep_source_audio": False,
    "subtitle_font_size": 28,
    "subtitle_position": "bottom",
}

_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".ogg"}
_SRT_EXTS = {".srt"}
_SUBTITLE_ALIGN = {"bottom": "2", "top": "8", "center": "5"}


def _ffprobe_executable() -> str | None:
    ffmpeg = _ffmpeg_executable()
    if ffmpeg:
        probe = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if probe.is_file():
            return str(probe)
    return shutil.which("ffprobe")


_FILTER_CACHE: dict[str, bool] = {}
# 能力探测绑定原始 run，避免测试 mock 成片 subprocess.run 时误伤 -filters。
_QUERY_RUN = subprocess.run


def _ffmpeg_has_filter(name: str) -> bool:
    if name in _FILTER_CACHE:
        return _FILTER_CACHE[name]
    exe = _ffmpeg_executable()
    if not exe:
        _FILTER_CACHE[name] = False
        return False
    completed = _QUERY_RUN([exe, "-hide_banner", "-filters"], capture_output=True, text=True, check=False)
    blob = completed.stdout or ""
    found = False
    for line in blob.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == name:
            found = True
            break
    _FILTER_CACHE[name] = found
    return found


def _subtitle_font_file() -> Path | None:
    candidates = [
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\msyh.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _row_to_settings(row) -> dict:
    if not row:
        return dict(DEFAULT_ASSEMBLY_SETTINGS)
    item = dict(row)
    return {
        "subtitle_enabled": bool(item.get("subtitle_enabled")),
        "subtitle_text": str(item.get("subtitle_text") or ""),
        "subtitle_srt_path": str(item.get("subtitle_srt_path") or ""),
        "audio_enabled": bool(item.get("audio_enabled")),
        "audio_asset_path": str(item.get("audio_asset_path") or ""),
        "audio_volume": float(item.get("audio_volume") or 0.4),
        "keep_source_audio": bool(item.get("keep_source_audio")),
        "subtitle_font_size": int(item.get("subtitle_font_size") or 28),
        "subtitle_position": item.get("subtitle_position") or "bottom",
    }


def get_assembly_settings(project_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM assembly_settings WHERE project_id = ?", (project_id,)).fetchone()
    return _row_to_settings(row)


def _resolve_owned_media(project_id: str, public_path: str, *, kind: str) -> Path:
    local = _local_asset_path(project_id, public_path, label="音频" if kind == "audio" else "字幕")
    suffix = local.suffix.lower()
    allowed = _AUDIO_EXTS if kind == "audio" else _SRT_EXTS
    if suffix not in allowed:
        raise AssemblyError("音频文件格式不受支持。" if kind == "audio" else "字幕文件必须是 SRT。")
    if not local.is_file() or local.stat().st_size <= 0:
        raise AssemblyError("背景音频文件不存在或已失效。" if kind == "audio" else "字幕文件不存在或已失效。")
    with connect() as conn:
        asset = conn.execute(
            "SELECT id, type FROM assets WHERE project_id = ? AND file_path = ?",
            (project_id, str(public_path).replace("\\", "/")),
        ).fetchone()
    expected_type = "audio" if kind == "audio" else "subtitle"
    if not asset or asset["type"] != expected_type:
        raise AssemblyError("只能使用当前项目已登记的音频资产。" if kind == "audio" else "只能使用当前项目已登记的字幕文件。")
    return local


def validate_assembly_settings(project_id: str, settings: dict | None = None) -> list[str]:
    settings = settings or get_assembly_settings(project_id)
    errors: list[str] = []
    if settings.get("audio_enabled"):
        path = str(settings.get("audio_asset_path") or "").strip()
        if not path:
            errors.append("已启用背景音频，请选择当前项目中的音频文件。")
        else:
            try:
                _resolve_owned_media(project_id, path, kind="audio")
            except AssemblyError as exc:
                errors.append(exc.message)
    if settings.get("subtitle_enabled"):
        text = str(settings.get("subtitle_text") or "").strip()
        srt_path = str(settings.get("subtitle_srt_path") or "").strip()
        if not text and not srt_path:
            errors.append("已启用字幕，请填写字幕文本或选择当前项目的 SRT 文件。")
        if srt_path:
            try:
                _resolve_owned_media(project_id, srt_path, kind="subtitle")
            except AssemblyError as exc:
                errors.append(exc.message)
        if text or srt_path:
            if not _ffmpeg_has_filter("subtitles"):
                errors.append("本机 FFmpeg 未启用字幕滤镜，无法烧录字幕。请关闭字幕或更换带 libass 的 FFmpeg 后重试。")
            elif _subtitle_font_file() is None:
                errors.append("本机没有可用的字幕字体，无法烧录中文字幕。请关闭字幕或安装系统字体后重试。")
    return errors


def save_assembly_settings(project_id: str, payload: dict) -> dict:
    with connect() as conn:
        exists = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not exists:
        raise AssemblyError("项目不存在。")
    merged = dict(DEFAULT_ASSEMBLY_SETTINGS)
    merged.update({key: payload.get(key, merged[key]) for key in DEFAULT_ASSEMBLY_SETTINGS})
    if merged["subtitle_position"] not in _SUBTITLE_ALIGN:
        raise AssemblyError("字幕位置只能是底部、顶部或居中。")
    merged["audio_volume"] = min(1.0, max(0.05, float(merged["audio_volume"])))
    merged["subtitle_font_size"] = min(64, max(16, int(merged["subtitle_font_size"])))
    errors = validate_assembly_settings(project_id, merged)
    if errors:
        raise AssemblyError(errors[0])
    previous = get_assembly_settings(project_id)
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO assembly_settings
            (project_id, subtitle_enabled, subtitle_text, subtitle_srt_path, audio_enabled,
             audio_asset_path, audio_volume, keep_source_audio, subtitle_font_size, subtitle_position, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
              subtitle_enabled = excluded.subtitle_enabled,
              subtitle_text = excluded.subtitle_text,
              subtitle_srt_path = excluded.subtitle_srt_path,
              audio_enabled = excluded.audio_enabled,
              audio_asset_path = excluded.audio_asset_path,
              audio_volume = excluded.audio_volume,
              keep_source_audio = excluded.keep_source_audio,
              subtitle_font_size = excluded.subtitle_font_size,
              subtitle_position = excluded.subtitle_position,
              updated_at = excluded.updated_at
            """,
            (
                project_id,
                1 if merged["subtitle_enabled"] else 0,
                merged["subtitle_text"],
                merged["subtitle_srt_path"],
                1 if merged["audio_enabled"] else 0,
                merged["audio_asset_path"],
                merged["audio_volume"],
                1 if merged["keep_source_audio"] else 0,
                merged["subtitle_font_size"],
                merged["subtitle_position"],
                now,
            ),
        )
        finals = conn.execute(
            "SELECT COUNT(*) AS n FROM assets WHERE project_id = ? AND type = 'final-video'",
            (project_id,),
        ).fetchone()["n"]
        changed = previous != merged
        if finals and changed:
            conn.execute("UPDATE projects SET assembly_stale = 1, updated_at = ? WHERE id = ?", (now, project_id))
    return get_assembly_settings_payload(project_id)


def get_assembly_settings_payload(project_id: str) -> dict:
    settings = get_assembly_settings(project_id)
    errors = validate_assembly_settings(project_id, settings)
    with connect() as conn:
        project = conn.execute("SELECT assembly_stale FROM projects WHERE id = ?", (project_id,)).fetchone()
        audio_assets = [
            {"id": row["id"], "name": row["name"], "file_path": row["file_path"]}
            for row in conn.execute(
                "SELECT id, name, file_path FROM assets WHERE project_id = ? AND type = 'audio' ORDER BY created_at",
                (project_id,),
            ).fetchall()
        ]
        subtitle_assets = [
            {"id": row["id"], "name": row["name"], "file_path": row["file_path"]}
            for row in conn.execute(
                "SELECT id, name, file_path FROM assets WHERE project_id = ? AND type = 'subtitle' ORDER BY created_at",
                (project_id,),
            ).fetchall()
        ]
    return {
        "ok": not errors,
        "errors": errors,
        "settings": settings,
        "stale": bool(project and project["assembly_stale"]),
        "audio_assets": audio_assets,
        "subtitle_assets": subtitle_assets,
        "capabilities": _assembly_capabilities(),
    }


def _assembly_capabilities() -> dict:
    ffmpeg = bool(_ffmpeg_executable())
    return {
        "ffmpeg_available": ffmpeg,
        "subtitles_filter": _ffmpeg_has_filter("subtitles") if ffmpeg else False,
        "font_available": _subtitle_font_file() is not None,
        "local_audio": True,
        "tts": False,
        "music_generation": False,
    }


def _assembly_note(settings: dict, *, source_audio_shot_count: int = 0, shot_count: int = 0) -> str:
    parts = ["当前有效镜头视频是合成的唯一画面来源。"]
    keep = bool(settings.get("keep_source_audio"))
    bg = bool(settings.get("audio_enabled"))
    sub = bool(settings.get("subtitle_enabled"))
    if not keep and not bg and not sub:
        parts.append("未启用背景音频、原声和字幕时，行为与 P6-C 一致：只拼接视频流并使用 -an。")
    else:
        if keep:
            if source_audio_shot_count:
                parts.append(
                    f"将保留 {source_audio_shot_count}/{shot_count or source_audio_shot_count} 个镜头的原声；"
                    "没有音轨的镜头对应片段为静音，不会伪造原声。"
                )
            else:
                parts.append("已开启保留原声，但当前镜头没有可用音轨，不会伪造原声。")
        if bg:
            parts.append("背景音频将循环或裁剪到成片时长后混入。")
        if keep and bg and source_audio_shot_count:
            parts.append("原声音量 1.0，背景音按配置音量混合。")
        if sub:
            parts.append("字幕会烧录到画面，不接入字幕大模型。")
    parts.append("不接入真实 TTS 或音乐生成。")
    return "".join(parts)


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
                "settings": dict(DEFAULT_ASSEMBLY_SETTINGS),
                "settings_errors": [],
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

    settings = get_assembly_settings(project_id)
    settings_errors = validate_assembly_settings(project_id, settings)
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
        "settings": settings,
        "settings_errors": settings_errors,
    }


def get_assembly_status(project_id: str) -> dict:
    report = validate_assembly(project_id)
    current = report["current_final"]
    settings = report.get("settings") or dict(DEFAULT_ASSEMBLY_SETTINGS)
    settings_errors = report.get("settings_errors") or []
    payload = get_assembly_settings_payload(project_id) if report.get("project") else {
        "audio_assets": [],
        "subtitle_assets": [],
        "capabilities": _assembly_capabilities(),
    }
    shot_payload = []
    source_audio_shot_count = 0
    for item in report["shots"]:
        has_audio = False
        if item.get("ready") and item.get("video_path"):
            try:
                has_audio = _has_audio_stream(_local_asset_path(project_id, item["video_path"]))
            except AssemblyError:
                has_audio = False
        if has_audio:
            source_audio_shot_count += 1
        shot_payload.append(
            {
                "shot_id": item.get("shot_id"),
                "shot_index": item.get("shot_index"),
                "title": item.get("title") or "",
                "ready": item.get("ready"),
                "issue": item.get("issue") or "",
                "video_path": item.get("video_path") or "",
                "status": item.get("status") or "",
                "has_audio": has_audio,
            }
        )
    source_audio_available = source_audio_shot_count > 0
    source_audio_used = bool(settings.get("keep_source_audio") and source_audio_available)
    return {
        "ok": report["ok"],
        "can_assemble": report["ok"] and not settings_errors and report["active_job"] is None,
        "errors": report["errors"],
        "settings_errors": settings_errors,
        "shot_count": report["shot_count"],
        "ready_count": report["ready_count"],
        "stale": report["stale"],
        "ffmpeg_available": report["ffmpeg_available"],
        "audio_scope": "optional_local",
        "audio_note": _assembly_note(
            settings,
            source_audio_shot_count=source_audio_shot_count,
            shot_count=report["shot_count"],
        ),
        "settings": settings,
        "source_audio_available": source_audio_available,
        "source_audio_shot_count": source_audio_shot_count,
        "source_audio_used": source_audio_used,
        "audio_assets": payload.get("audio_assets") or [],
        "subtitle_assets": payload.get("subtitle_assets") or [],
        "capabilities": payload.get("capabilities") or _assembly_capabilities(),
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
        "shots": shot_payload,
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
    settings_errors = report.get("settings_errors") or []
    if settings_errors:
        raise AssemblyError(settings_errors[0])
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


def _ffprobe_json(path: Path) -> dict:
    exe = _ffprobe_executable()
    if not exe:
        return {}
    completed = _QUERY_RUN(
        [exe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return {}
    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def _media_duration(path: Path) -> float:
    payload = _ffprobe_json(path)
    return float((payload.get("format") or {}).get("duration") or 0)


def _has_audio_stream(path: Path) -> bool:
    payload = _ffprobe_json(path)
    return any(stream.get("codec_type") == "audio" for stream in payload.get("streams") or [])


def _format_srt_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(total_ms, 3600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _write_plain_srt(path: Path, text: str, duration: float) -> None:
    body = (text or "").strip() or " "
    end = max(duration, 0.5)
    path.write_text(
        f"1\n00:00:00,000 --> {_format_srt_timestamp(end)}\n{body}\n",
        encoding="utf-8",
    )


def _ffmpeg_filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _subtitles_filter_arg(srt_path: Path, settings: dict) -> str:
    escaped = _ffmpeg_filter_path(srt_path)
    font = _subtitle_font_file()
    font_name = "Microsoft YaHei" if font and "msyh" in font.name.lower() else "Arial"
    align = _SUBTITLE_ALIGN.get(settings.get("subtitle_position") or "bottom", "2")
    size = int(settings.get("subtitle_font_size") or 28)
    style = (
        f"FontName={font_name},FontSize={size},Alignment={align},MarginV=36,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2"
    )
    fontsdir = ""
    if font:
        fontsdir = f":fontsdir='{_ffmpeg_filter_path(font.parent)}'"
    return f"subtitles='{escaped}':charenc=UTF-8{fontsdir}:force_style='{style}'"


_RESOLUTION_CANVAS = {
    "1280x720": (1280, 720),
    "1920x1080": (1920, 1080),
    "720x1280": (720, 1280),
    "1080x1080": (1080, 1080),
    "720x720": (720, 720),
}


def assembly_canvas(project: dict | None) -> tuple[int, int]:
    data = dict(project) if project is not None else {}
    raw = str(data.get("output_resolution") or "1280x720").lower().replace(" ", "")
    return _RESOLUTION_CANVAS.get(raw, (1280, 720))


def _video_norm_filter(width: int, height: int) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=24,format=yuv420p"
    )


_VIDEO_NORM_FILTER = _video_norm_filter(1280, 720)


def _run_ffmpeg(command: list[str], fail_message: str) -> None:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    output = Path(command[-1])
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
        output.unlink(missing_ok=True)
        raise RuntimeError(fail_message)


def _normalize_shot_clip(
    ffmpeg_exe: str,
    src: Path,
    dest: Path,
    *,
    has_audio: bool,
    width: int = 1280,
    height: int = 720,
) -> None:
    duration = _media_duration(src)
    if duration <= 0:
        duration = 1.0
    dest.parent.mkdir(parents=True, exist_ok=True)
    vf = _video_norm_filter(width, height)
    if has_audio:
        command = [
            ffmpeg_exe,
            "-y",
            "-i",
            str(src),
            "-vf",
            vf,
            "-af",
            "aresample=44100,aformat=channel_layouts=stereo,apad",
            "-t",
            f"{duration:.3f}",
            "-r",
            "24",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    else:
        command = [
            ffmpeg_exe,
            "-y",
            "-i",
            str(src),
            "-f",
            "lavfi",
            "-t",
            f"{duration:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-vf",
            vf,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            f"{duration:.3f}",
            "-r",
            "24",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-shortest",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    _run_ffmpeg(command, "成片合成失败，请检查各镜头视频是否完整后重试。")


def _concat_normalized_clips(ffmpeg_exe: str, clip_paths: list[Path], concat_file: Path, output_path: Path) -> None:
    concat_file.write_text(
        "\n".join(f"file '{_ffmpeg_concat_path(path)}'" for path in clip_paths),
        encoding="utf-8",
    )
    copy_command = [
        ffmpeg_exe,
        "-y",
        "-fflags",
        "+genpts",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    completed = subprocess.run(copy_command, check=False, capture_output=True, text=True)
    if completed.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 0:
        return
    output_path.unlink(missing_ok=True)
    encode_command = [
        ffmpeg_exe,
        "-y",
        "-fflags",
        "+genpts",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "24",
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    _run_ffmpeg(encode_command, "成片合成失败，请检查各镜头视频是否完整后重试。")


def _pack_assembly_output(
    ffmpeg_exe: str,
    video_path: Path,
    settings: dict,
    project_id: str,
    temps: list[Path],
) -> None:
    duration = _media_duration(video_path)
    if duration <= 0:
        duration = 1.0
    command = [ffmpeg_exe, "-y", "-i", str(video_path)]
    audio_input = None
    if settings.get("audio_enabled"):
        audio_input = _resolve_owned_media(project_id, settings["audio_asset_path"], kind="audio")
        command.extend(["-stream_loop", "-1", "-i", str(audio_input)])
    vf = None
    if settings.get("subtitle_enabled"):
        srt_temp = video_path.with_name(f"{video_path.stem}_pack.srt")
        temps.append(srt_temp)
        srt_path = str(settings.get("subtitle_srt_path") or "").strip()
        if srt_path:
            source = _resolve_owned_media(project_id, srt_path, kind="subtitle")
            srt_temp.write_bytes(source.read_bytes())
        else:
            _write_plain_srt(srt_temp, settings.get("subtitle_text") or "", duration)
        vf = _subtitles_filter_arg(srt_temp, settings)
    keep_source = bool(settings.get("keep_source_audio")) and _has_audio_stream(video_path)
    volume = float(settings.get("audio_volume") or 0.4)
    filter_parts: list[str] = []
    video_map = "0:v:0"
    audio_map = None
    if vf:
        filter_parts.append(f"[0:v]{vf}[vout]")
        video_map = "[vout]"
    if audio_input and keep_source:
        filter_parts.append(f"[1:a]atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,volume={volume}[bg]")
        filter_parts.append("[0:a]volume=1.0[src]")
        filter_parts.append("[src][bg]amix=inputs=2:duration=first:dropout_transition=0[aout]")
        audio_map = "[aout]"
    elif audio_input:
        filter_parts.append(f"[1:a]atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,volume={volume}[aout]")
        audio_map = "[aout]"
    elif keep_source:
        audio_map = "0:a:0"
    packed = video_path.with_name(f"{video_path.stem}_packed.mp4")
    temps.append(packed)
    if filter_parts:
        command.extend(["-filter_complex", ";".join(filter_parts)])
    command.extend(["-map", video_map])
    if vf:
        command.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24"])
    else:
        command.extend(["-c:v", "copy"])
    if audio_map:
        command.extend(["-map", audio_map, "-c:a", "aac", "-ac", "2", "-ar", "44100"])
    else:
        command.append("-an")
    command.extend(["-movflags", "+faststart", "-t", f"{duration:.3f}", str(packed)])
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0 or not packed.is_file() or packed.stat().st_size <= 0:
        packed.unlink(missing_ok=True)
        raise RuntimeError("成片合成失败，请检查音频、字幕和镜头视频后重试。")
    video_path.unlink(missing_ok=True)
    packed.replace(video_path)
    if packed in temps:
        temps.remove(packed)


def _safe_assembly_message(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if any(token in lowered for token in ("ffmpeg", "ffprobe", "http", "token", "secret", "api_key", "signed")):
        return "成片合成失败，请检查镜头视频、音频和字幕配置后重试。"
    if "\\" in message or ":/" in lowered:
        return "成片合成失败，请检查镜头视频、音频和字幕配置后重试。"
    return message


def assemble_project_video(project_id: str, job_id: str) -> None:
    concat_file: Path | None = None
    output_path: Path | None = None
    temps: list[Path] = []
    update_job(job_id, "running", 8, "正在检查成片合成条件", stage="validate_inputs")
    try:
        report = validate_assembly(project_id)
        if not report["ok"]:
            raise RuntimeError(" ".join(report["errors"]))
        settings_errors = report.get("settings_errors") or []
        if settings_errors:
            raise RuntimeError(settings_errors[0])
        project = report["project"]
        video_paths = report["video_paths"]
        settings = report.get("settings") or get_assembly_settings(project_id)
        local_inputs = [_local_asset_path(project_id, path) for path in video_paths]
        keep_wanted = bool(settings.get("keep_source_audio"))
        source_flags = [_has_audio_stream(path) for path in local_inputs] if keep_wanted else [False] * len(local_inputs)
        keep_source = keep_wanted and any(source_flags)
        canvas_w, canvas_h = assembly_canvas(project)
        vf = _video_norm_filter(canvas_w, canvas_h).replace(",fps=24", "")
        update_job(job_id, "running", 30, "正在统一规格并合成镜头视频", stage="concat")
        asset_id = f"asset_{uuid.uuid4().hex[:10]}"
        filename = f"{asset_id}.mp4"
        output_path = PROJECTS_DIR / project_id / filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_exe = _ffmpeg_executable()
        if not ffmpeg_exe:
            raise RuntimeError("本机未找到 FFmpeg，无法合成成片。请安装 ffmpeg 与 ffprobe 后重试。")
        concat_file = PROJECTS_DIR / project_id / f"{asset_id}_concat.txt"
        temps.append(concat_file)
        if keep_source:
            update_job(job_id, "running", 40, "正在规范化镜头并保留原声", stage="normalize")
            norm_paths: list[Path] = []
            for index, src in enumerate(local_inputs):
                dest = output_path.parent / f"{asset_id}_norm_{index}.mp4"
                temps.append(dest)
                _normalize_shot_clip(
                    ffmpeg_exe,
                    src,
                    dest,
                    has_audio=source_flags[index],
                    width=canvas_w,
                    height=canvas_h,
                )
                norm_paths.append(dest)
            _concat_normalized_clips(ffmpeg_exe, norm_paths, concat_file, output_path)
            for path in norm_paths:
                path.unlink(missing_ok=True)
                if path in temps:
                    temps.remove(path)
        else:
            concat_file.write_text(
                "\n".join(f"file '{_ffmpeg_concat_path(path)}'" for path in local_inputs),
                encoding="utf-8",
            )
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
                vf,
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
            completed = subprocess.run(command, check=False, capture_output=True, text=True)
            if completed.returncode != 0:
                if output_path is not None:
                    output_path.unlink(missing_ok=True)
                raise RuntimeError("成片合成失败，请检查各镜头视频是否完整后重试。")
        concat_file.unlink(missing_ok=True)
        if concat_file in temps:
            temps.remove(concat_file)
        concat_file = None
        if output_path is None or not output_path.is_file() or output_path.stat().st_size == 0:
            if output_path is not None:
                output_path.unlink(missing_ok=True)
            raise RuntimeError("成片文件无效，未登记资产。请重新合成。")
        need_pack = bool(settings.get("audio_enabled") or settings.get("subtitle_enabled"))
        if need_pack:
            update_job(job_id, "running", 70, "正在混入音频并烧录字幕", stage="pack")
            try:
                _pack_assembly_output(ffmpeg_exe, output_path, settings, project_id, temps)
            except Exception:
                if output_path is not None:
                    output_path.unlink(missing_ok=True)
                raise
        video_path = public_asset_path(project_id, filename)
        shot_count = len(video_paths)
        flags = []
        if settings.get("audio_enabled"):
            flags.append("bg-audio")
        if settings.get("subtitle_enabled"):
            flags.append("subtitles")
        if keep_source:
            flags.append("keep-source-audio")
        prompt = "FFmpeg sequence assembly (" + (", ".join(flags) if flags else "video only") + ")"
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
                    prompt,
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
        for temp in list(temps):
            temp.unlink(missing_ok=True)
        if concat_file is not None:
            concat_file.unlink(missing_ok=True)
        if output_path is not None:
            output_path.unlink(missing_ok=True)
        update_job(job_id, "failed", 100, "成片合成失败", _safe_assembly_message(exc), stage="failed")
    else:
        for temp in list(temps):
            temp.unlink(missing_ok=True)


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
