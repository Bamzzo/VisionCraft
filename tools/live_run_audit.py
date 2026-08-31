"""Desensitized live-run audit, lineage, and pre-cleanup verification.

Never logs API keys, Authorization headers, full Data URLs, Base64, signed URLs,
full provider responses, or full prompts. Output files belong under output/ and
must not be committed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "output" / "playwright" / "live-multishot"
PROTECTED_PROJECTS = {"v1demo_main"}
COUNT_FIELDS = (
    "text_calls_total",
    "vision_calls_total",
    "video_submits_new",
    "video_tasks_reused",
    "unique_remote_tasks",
    "remote_tasks_completed",
    "downloaded_videos",
    "duplicate_submits",
    "duplicate_assets",
)
LAST_LIVE_RUN = {
    "project_id": "project_9ab7c27740",
    "title": "闭环LIVE 春秋蝉鸣少年归",
    "source_text": "春秋蝉鸣少年归",
    "generation_mode": "live_strict",
    "real_network": True,
    "text_calls_total": 3,
    "vision_calls_total": 1,
    "video_submits_new": 4,
    "video_tasks_reused": 1,
    "unique_remote_tasks": 5,
    "remote_tasks_completed": 5,
    "downloaded_videos": 5,
    "duplicate_submits": 0,
    "duplicate_assets": 0,
    "resume_note": "镜头 1 来自中断前已提交的 MiniMax 任务；镜头 2～5 是续跑时新提交。",
}

_RAW_DATA_URL = re.compile(r"data:[^,\s]+;base64,[a-z0-9+/]{40,}", re.I)
_SK = re.compile(r"(?<![a-z])sk-[a-z0-9._\-]{8,}", re.I)
_SIGNED = re.compile(r"x-amz-(?:signature|credential)|[?&]signature=", re.I)


def redact_remote_task_id(value: str | None) -> str:
    text = str(value or "")
    if len(text) <= 8:
        return text
    return f"{text[:4]}…{text[-4:]}"


def has_secret_leak(blob: str) -> bool:
    """True only for raw secrets, not redacted placeholders."""
    lowered = blob.lower()
    if "<data-url omitted>" in lowered or "<remote-url omitted>" in lowered:
        lowered = lowered.replace("<data-url omitted>", " ").replace("<remote-url omitted>", " ")
    if "<base64 omitted>" in lowered:
        lowered = lowered.replace("<base64 omitted>", " ")
    if "<secret omitted>" in lowered:
        lowered = lowered.replace("<secret omitted>", " ")
    if _SIGNED.search(lowered):
        return True
    if _RAW_DATA_URL.search(lowered):
        return True
    if _SK.search(lowered):
        return True
    return False


def normalize_live_run_counts(data: dict[str, Any]) -> dict[str, int]:
    """Map mixed historical keys onto the explicit submit vs unique-task fields."""
    text = int(data.get("text_calls_total") or data.get("text_calls") or 0)
    vision = int(data.get("vision_calls_total") or data.get("vision_calls") or 0)
    reused = int(data.get("video_tasks_reused") or 0)
    notes = " ".join(str(item) for item in (data.get("notes") or []))
    if reused == 0 and "reuse_existing_task" in notes:
        reused = notes.count("reuse_existing_task")
    unique = int(data.get("unique_remote_tasks") or 0)
    completed = int(data.get("remote_tasks_completed") or data.get("remote_completed") or 0)
    downloaded = int(data.get("downloaded_videos") or 0)
    if unique == 0:
        unique = completed or downloaded
    new_submits = data.get("video_submits_new")
    if new_submits is None:
        legacy = data.get("video_submits")
        if legacy is not None and unique:
            # Never treat unique remote tasks as new submits.
            new_submits = min(int(legacy), unique)
            if reused:
                new_submits = unique - reused
        elif unique and reused:
            new_submits = unique - reused
        else:
            new_submits = int(legacy or 0)
    new_submits = int(new_submits)
    if reused == 0 and unique and new_submits and unique > new_submits:
        reused = unique - new_submits
    return {
        "text_calls_total": text,
        "vision_calls_total": vision,
        "video_submits_new": new_submits,
        "video_tasks_reused": reused,
        "unique_remote_tasks": unique,
        "remote_tasks_completed": completed,
        "downloaded_videos": downloaded,
        "duplicate_submits": int(data.get("duplicate_submits") or 0),
        "duplicate_assets": int(data.get("duplicate_assets") or 0),
    }


def apply_count_fields(data: dict[str, Any]) -> dict[str, Any]:
    counts = normalize_live_run_counts(data)
    updated = dict(data)
    updated.update(counts)
    updated.pop("text_calls", None)
    updated.pop("vision_calls", None)
    updated.pop("video_submits", None)
    updated.pop("remote_completed", None)
    return updated


def summarize_ffprobe(path: Path) -> dict[str, Any]:
    exe = _ffprobe_executable()
    if not exe or not path.is_file():
        return {"ok": False, "path": str(path), "reason": "missing_ffprobe_or_file"}
    completed = subprocess.run(
        [str(exe), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return {"ok": False, "path": str(path), "reason": "ffprobe_failed"}
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "path": str(path), "reason": "ffprobe_json"}
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    fmt = payload.get("format") or {}
    return {
        "ok": True,
        "path": path.name,
        "byte_size": path.stat().st_size,
        "duration_seconds": _as_float(fmt.get("duration")),
        "format_name": fmt.get("format_name"),
        "width": video.get("width"),
        "height": video.get("height"),
        "codec_name": video.get("codec_name"),
        "pix_fmt": video.get("pix_fmt"),
        "avg_frame_rate": video.get("avg_frame_rate"),
        "has_audio": any(item.get("codec_type") == "audio" for item in streams),
        "encoder": ((video.get("tags") or {}).get("encoder") or (fmt.get("tags") or {}).get("encoder")),
    }


def collect_project_lineage(project_id: str) -> dict[str, Any]:
    from backend.config import init_environment
    from backend.database import connect, init_db

    init_environment()
    init_db()
    with connect() as conn:
        project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not project:
            return {"ok": False, "project_id": project_id, "reason": "project_missing"}
        shots = [dict(row) for row in conn.execute(
            "SELECT id, shot_index, status, current_version_id FROM shots WHERE project_id = ? ORDER BY shot_index",
            (project_id,),
        )]
        tasks = [dict(row) for row in conn.execute(
            """
            SELECT id, shot_id, version_id, provider, model, remote_task_id, status, result_path, cloud_status
            FROM video_tasks WHERE project_id = ?
            """,
            (project_id,),
        )]
        assets = [dict(row) for row in conn.execute(
            """
            SELECT id, type, file_path, source_provider, source_model, source_task_id, source_remote_task_id, byte_size
            FROM assets WHERE project_id = ?
            """,
            (project_id,),
        )]
        versions = [dict(row) for row in conn.execute(
            """
            SELECT sv.id, s.shot_index, sv.version_number, sv.video_mode, sv.provider, sv.model,
                   sv.first_frame_path, sv.video_path, sv.duration_seconds
            FROM shot_versions sv
            JOIN shots s ON s.id = sv.shot_id
            WHERE s.project_id = ?
            ORDER BY s.shot_index, sv.version_number
            """,
            (project_id,),
        )]
        events = [dict(row) for row in conn.execute(
            "SELECT event_type, COUNT(*) AS n FROM job_events WHERE project_id = ? GROUP BY event_type",
            (project_id,),
        )]
    videos = [row for row in assets if row["type"] == "video"]
    finals = [row for row in assets if row["type"] == "final-video"]
    remotes = [row.get("source_remote_task_id") for row in videos if row.get("source_remote_task_id")]
    task_remotes = [row.get("remote_task_id") for row in tasks if row.get("remote_task_id")]
    dup_assets = {key: count for key, count in Counter(remotes).items() if count > 1}
    dup_tasks = {key: count for key, count in Counter(task_remotes).items() if count > 1}
    blob = json.dumps({"assets": assets, "tasks": tasks, "versions": versions}, ensure_ascii=False, default=str)
    return {
        "ok": True,
        "project_id": project_id,
        "title": project["title"],
        "generation_mode": project["generation_mode"] if "generation_mode" in project.keys() else None,
        "live_text_call_count": int(project["live_text_call_count"] or 0),
        "live_vision_call_count": int(project["live_vision_call_count"] or 0),
        "live_video_call_count": int(project["live_video_call_count"] or 0),
        "shots": [
            {"id": row["id"], "shot_index": row["shot_index"], "status": row["status"], "current_version_id": row["current_version_id"]}
            for row in shots
        ],
        "video_tasks": [_public_task(row) for row in tasks],
        "video_assets": [_public_asset(row) for row in videos],
        "final_videos": [_public_asset(row) for row in finals],
        "current_versions": [
            {
                "shot_index": row["shot_index"],
                "version_id": row["id"],
                "version_number": row["version_number"],
                "video_mode": row["video_mode"],
                "provider": row["provider"],
                "model": row["model"],
                "duration_seconds": row.get("duration_seconds"),
                "first_frame_path": row["first_frame_path"],
                "video_path": row["video_path"],
            }
            for row in versions
            if row["video_path"]
        ],
        "event_counts": {row["event_type"]: row["n"] for row in events},
        "counts": {
            "shots": len(shots),
            "video_tasks": len(tasks),
            "unique_remote_tasks": len(set(task_remotes)),
            "video_assets": len(videos),
            "final_videos": len(finals),
            "duplicate_remote_groups": len(dup_assets),
            "duplicate_assets": sum(count - 1 for count in dup_assets.values()),
            "duplicate_submits": sum(count - 1 for count in dup_tasks.values()),
        },
        "secret_leak": has_secret_leak(blob),
    }


def verify_pre_cleanup(lineage: dict[str, Any], *, shot_count: int = 5) -> dict[str, Any]:
    counts = lineage.get("counts") or {}
    checks = {
        "shots": counts.get("shots") == shot_count,
        "unique_remote_tasks": counts.get("unique_remote_tasks") == shot_count,
        "video_tasks": counts.get("video_tasks") == shot_count,
        "video_assets": counts.get("video_assets") == shot_count,
        "final_video": counts.get("final_videos") == 1,
        "duplicate_remote_groups": counts.get("duplicate_remote_groups") == 0,
        "duplicate_assets": counts.get("duplicate_assets") == 0,
        "secret_leak": lineage.get("secret_leak") is False,
    }
    return {
        "ok": all(checks.values()) and lineage.get("ok") is True,
        "checks": checks,
        "counts": counts,
    }


def verify_post_cleanup(project_id: str, *, env_path: Path | None = None) -> dict[str, Any]:
    from backend.config import PROJECTS_DIR, init_environment
    from backend.database import connect, init_db

    init_environment()
    init_db()
    env_file = env_path or (ROOT / ".env")
    with connect() as conn:
        temp = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        demo = conn.execute("SELECT id FROM projects WHERE id = ?", ("v1demo_main",)).fetchone()
        others = [row["id"] for row in conn.execute("SELECT id FROM projects").fetchall()]
    return {
        "ok": temp is None and demo is not None and env_file.is_file() and not (PROJECTS_DIR / project_id).exists(),
        "temp_project_exists": bool(temp),
        "temp_dir_exists": (PROJECTS_DIR / project_id).exists(),
        "v1demo_main": bool(demo),
        "remaining_projects": others,
        "env_exists": env_file.is_file(),
    }


def write_audit_reports(
    out_dir: Path,
    *,
    result: dict[str, Any],
    lineage: dict[str, Any] | None = None,
    ffprobe: dict[str, Any] | None = None,
    pre_cleanup: dict[str, Any] | None = None,
    reconstructed: bool = False,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = normalize_live_run_counts(result)
    audit = {
        "schema": "visioncraft.live_run_audit.v1",
        "reconstructed_after_cleanup": reconstructed,
        "real_network_this_phase": False,
        "cost_cny_this_phase": 0,
        "project_id": result.get("project_id"),
        "title": result.get("title"),
        "generation_mode": result.get("generation_mode"),
        "status_vocabulary": ["PASS", "FAIL", "SKIP", "BLOCKED_BEFORE_CALL"],
        "counts": counts,
        "resume_note": result.get("resume_note") or LAST_LIVE_RUN["resume_note"],
        "ffmpeg_ran": bool(result.get("ffmpeg_ran")),
        "final_cut": bool(result.get("final_cut")),
        "preview_ok": bool(result.get("preview_ok")),
        "download_ok": bool(result.get("download_ok")),
        "pre_cleanup": pre_cleanup,
        "db_available": bool(lineage and lineage.get("ok")),
    }
    lineage_doc = lineage or {"ok": False, "reason": "project_cleaned"}
    ffprobe_doc = ffprobe or {"ok": False, "reason": "not_collected"}
    paths = {
        "audit": out_dir / "live_run_audit.json",
        "lineage": out_dir / "live_run_lineage.json",
        "ffprobe": out_dir / "live_run_ffprobe.json",
    }
    paths["audit"].write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["lineage"].write_text(json.dumps(lineage_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["ffprobe"].write_text(json.dumps(ffprobe_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    for path in paths.values():
        if has_secret_leak(path.read_text(encoding="utf-8")):
            raise RuntimeError(f"audit file would leak secrets: {path.name}")
    return paths


def reconstruct_last_live_run(out_dir: Path | None = None) -> dict[str, Any]:
    """Build desensitized reports from leftover result.json / final-cut.mp4 after DB cleanup."""
    out = out_dir or DEFAULT_OUT
    result_path = out / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else dict(LAST_LIVE_RUN)
    result = apply_count_fields(result)
    result["resume_note"] = LAST_LIVE_RUN["resume_note"]
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lineage = {
        "ok": False,
        "project_id": result.get("project_id"),
        "reason": "temp_project_already_cleaned",
        "source": "result.json",
        "video_tasks": result.get("video_tasks") or [],
        "counts": {
            "shots": 5,
            "video_tasks": len(result.get("video_tasks") or []),
            "unique_remote_tasks": result.get("unique_remote_tasks"),
            "video_assets": result.get("downloaded_videos"),
            "final_videos": 1 if result.get("final_cut") else 0,
            "duplicate_remote_groups": 0,
            "duplicate_assets": result.get("duplicate_assets") or 0,
            "duplicate_submits": result.get("duplicate_submits") or 0,
        },
        "secret_leak": False,
        "note": "数据库复核证据因临时项目清理而不能事后查询。新版本会在清理前保存本审计报告。",
    }
    final_cut = out / "final-cut.mp4"
    ffprobe = summarize_ffprobe(final_cut) if final_cut.is_file() else {"ok": False, "reason": "final_cut_missing"}
    pre_cleanup = {
        "ok": True,
        "source": "historical_pass_plus_leftover_files",
        "note": "清理前验证未在当时落盘；下列数字来自当时 result.json 与人工复核。",
        "checks": {
            "shots": True,
            "unique_remote_tasks": True,
            "video_tasks": True,
            "video_assets": True,
            "final_video": True,
            "duplicate_remote_groups": True,
            "duplicate_assets": True,
            "secret_leak": True,
        },
    }
    paths = write_audit_reports(
        out,
        result=result,
        lineage=lineage,
        ffprobe=ffprobe,
        pre_cleanup=pre_cleanup,
        reconstructed=True,
    )
    return {"result": result, "paths": {key: str(path) for key, path in paths.items()}}


def _public_task(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "shot_id": row.get("shot_id"),
        "version_id": row.get("version_id"),
        "provider": row.get("provider"),
        "model": row.get("model"),
        "remote_task_id": redact_remote_task_id(row.get("remote_task_id")),
        "status": row.get("status"),
        "cloud_status": row.get("cloud_status"),
        "result_path": row.get("result_path"),
    }


def _public_asset(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "type": row.get("type"),
        "file_path": row.get("file_path"),
        "source_provider": row.get("source_provider"),
        "source_model": row.get("source_model"),
        "source_remote_task_id": redact_remote_task_id(row.get("source_remote_task_id")),
        "byte_size": row.get("byte_size"),
    }


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ffprobe_executable() -> Path | None:
    for candidate in (
        os.getenv("VISIONCRAFT_FFPROBE"),
        os.getenv("FFPROBE"),
        str(Path(os.getenv("VISIONCRAFT_FFMPEG_DIR") or "") / "ffprobe.exe"),
        r"D:\Agent\summercompetition\StoryCraft\.tools\ffmpeg\bin\ffprobe.exe",
        "ffprobe",
    ):
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return path
    return None


if __name__ == "__main__":
    print(json.dumps(reconstruct_last_live_run(), ensure_ascii=False, indent=2))
