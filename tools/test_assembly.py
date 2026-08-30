"""No-cost tests for P6-A sequence assembly validation and persistence."""
from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db, utc_now
from backend.services import video_service
from backend.services.asset_service import public_asset_path
from backend.services.job_service import create_job, get_job


def seed_project(project_id: str) -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode,
             status, routing_mode, assembly_stale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, "P6 合成测试", "测试文本", "test", "16:9", 5, "auto", "testing", "direct", 1, now, now),
        )


def seed_shot(project_id: str, index: int, *, real: bool = True, with_file: bool = True) -> None:
    shot_id = f"shot_{uuid.uuid4().hex[:10]}"
    version_id = f"version_{uuid.uuid4().hex[:10]}"
    filename = f"{shot_id}.mp4" if with_file else None
    video_path = public_asset_path(project_id, filename) if filename else None
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO shots
            (id, project_id, shot_index, title, description, characters, scene, camera_motion,
             visual_prompt, negative_prompt, audio_prompt, status, retry_count, current_version_id,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (shot_id, project_id, index, f"镜头 {index:02d}", "描述", "[]", "场景", "固定",
             "提示词", "", "", "video_ready" if with_file else "keyframes_ready", 0,
             version_id, now, now),
        )
        conn.execute(
            """INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
             first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, shot_id, 1, "描述", "提示词", "", "", None, None, video_path, "t2v",
             "ark" if real else "ffmpeg", "model", "test", now),
        )
        if with_file:
            conn.execute(
                """INSERT INTO assets
                (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)""",
                (f"asset_{uuid.uuid4().hex[:10]}", project_id, f"镜头 {index:02d} 视频", "测试视频", "测试",
                 video_path, "provider:ark:model" if real else "provider:ffmpeg", now),
            )
    if filename:
        path = PROJECTS_DIR / project_id / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-video")


def cleanup(project_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)


def test_success_persists_asset_and_clears_stale() -> None:
    project_id = f"assembly_test_{uuid.uuid4().hex[:10]}"
    seed_project(project_id)
    original_run = video_service.subprocess.run
    try:
        seed_shot(project_id, 1)
        seed_shot(project_id, 2)

        def fake_run(command, **_kwargs):
            Path(command[-1]).write_bytes(b"assembled-video")
            return video_service.subprocess.CompletedProcess(command, 0, "", "")

        video_service.subprocess.run = fake_run
        job_id = create_job(project_id, "sequence_assembly", "成片合成已排队")
        video_service.assemble_project_video(project_id, job_id)
        job = get_job(job_id)
        assert job["status"] == "completed"
        assert job["message"] == "成片已生成，可在工作区预览或下载"
        assert any(event["event_type"] == "asset.ready" for event in job["events"])
        with connect() as conn:
            project = conn.execute("SELECT assembly_stale FROM projects WHERE id = ?", (project_id,)).fetchone()
            final = conn.execute(
                "SELECT file_path, embedding_ref FROM assets WHERE project_id = ? AND type = 'final-video'",
                (project_id,),
            ).fetchone()
        assert project["assembly_stale"] == 0
        assert final and final["embedding_ref"] == "provider:ffmpeg:sequence-assembly"
        output = PROJECTS_DIR / project_id / final["file_path"].rsplit("/", 1)[-1]
        assert output.read_bytes() == b"assembled-video"
        print("PASS: assembly persists final asset and clears assembly_stale")
    finally:
        video_service.subprocess.run = original_run
        cleanup(project_id)


def test_rejects_missing_and_placeholder_videos() -> None:
    project_id = f"assembly_test_{uuid.uuid4().hex[:10]}"
    seed_project(project_id)
    try:
        seed_shot(project_id, 1, with_file=True)
        seed_shot(project_id, 2, with_file=False)
        job_id = create_job(project_id, "sequence_assembly", "成片合成已排队")
        video_service.assemble_project_video(project_id, job_id)
        job = get_job(job_id)
        assert job["status"] == "failed"
        assert "尚未生成视频" in (job["error_message"] or "")

        with connect() as conn:
            conn.execute("DELETE FROM shots WHERE project_id = ?", (project_id,))
        seed_shot(project_id, 1, real=False)
        job_id = create_job(project_id, "sequence_assembly", "成片合成已排队")
        video_service.assemble_project_video(project_id, job_id)
        job = get_job(job_id)
        assert job["status"] == "failed"
        assert "占位视频" in (job["error_message"] or "")
        print("PASS: assembly rejects missing and placeholder videos")
    finally:
        cleanup(project_id)


def main() -> None:
    init_environment()
    init_db()
    test_success_persists_asset_and_clears_stale()
    test_rejects_missing_and_placeholder_videos()
    print("PASS: P6-A assembly contract")


if __name__ == "__main__":
    main()
