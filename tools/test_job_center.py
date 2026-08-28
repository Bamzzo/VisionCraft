"""No-cost tests for P2 job events, redaction, refresh-only remote query, and SSE payloads."""
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
from backend.services.job_service import (
    append_job_event,
    create_job,
    format_sse,
    get_job,
    get_job_events,
    job_center_snapshot,
    update_job,
)
from backend.services import video_service


def _project(title: str = "Job center test") -> str:
    project_id = f"job_test_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode, status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, "Test source", "test", "16:9", 5, "auto", "testing", "direct", now, now),
        )
    return project_id


def test_update_job_appends_events() -> None:
    project_id = _project()
    try:
        job_id = create_job(project_id, "video_generation", "视频生成已排队", shot_id="shot_demo")
        update_job(job_id, "running", 24, "正在提交至阿里 Wan", stage="submit_provider", shot_id="shot_demo")
        update_job(
            job_id,
            "completed",
            100,
            "视频已生成，可在镜头卡片中预览",
            stage="persist_asset",
            event_type="asset.ready",
            shot_id="shot_demo",
            detail={"asset_path": "/assets/demo/out.mp4", "image": "data:image/png;base64,AAAA"},
        )
        job = get_job(job_id)
        events = get_job_events(project_id)
        types = [item["event_type"] for item in events]
        assert job["status"] == "completed"
        assert job["stage"] == "persist_asset"
        assert job["progress"] == 100
        assert "job.update" in types
        assert "asset.ready" in types
        assert "project.refresh_required" in types
        assert events == sorted(events, key=lambda item: item["id"])
        blob = str(events)
        assert "data:image/png;base64,AAAA" not in blob
        assert "<redacted>" in blob or "<data-url-omitted>" in blob
        print("PASS: update_job writes jobs snapshot and ordered, redacted job_events")
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def test_failed_event_redacts_secrets() -> None:
    project_id = _project("Redaction test")
    try:
        job_id = create_job(project_id, "video_generation", "已排队")
        update_job(
            job_id,
            "failed",
            100,
            "视频生成失败",
            "Authorization: Bearer sk-secret token=abcd api_key=K-1 https://cdn.example/file.mp4?X-Amz-Signature=deadbeef",
            stage="failed",
            detail={"api_key": "sk-live", "status_payload": {"url": "data:image/jpeg;base64,QQQQ"}},
        )
        job = get_job(job_id)
        payload = str(job)
        assert "sk-secret" not in payload
        assert "sk-live" not in payload
        assert "deadbeef" not in payload
        assert "QQQQ" not in payload
        failed = [item for item in job["events"] if item["event_type"] == "job.failed"]
        assert failed
        print("PASS: failed events do not persist secrets or signed URLs")
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def test_sse_payload_is_structured() -> None:
    event = {
        "id": 7,
        "event_type": "job.update",
        "project_id": "p1",
        "job_id": "job_1",
        "shot_id": "shot_1",
        "stage": "poll_remote",
        "status": "running",
        "progress": 60,
        "message": "MiniMax H3 云端正在生成镜头 03",
        "detail": {},
        "created_at": utc_now(),
    }
    text = format_sse(event)
    assert "event: job.update" in text
    assert "id: 7" in text
    for field in ("project_id", "job_id", "shot_id", "status", "progress", "message", "stage"):
        assert field in text
    snapshot = job_center_snapshot("missing")
    assert snapshot["events"] == []
    print("PASS: SSE payload is structured")


def test_waiting_remote_refresh_queries_only() -> None:
    project_id = _project("Refresh query test")
    shot_id = f"shot_{uuid.uuid4().hex[:8]}"
    version_id = f"version_{uuid.uuid4().hex[:8]}"
    task_id = f"vt_{uuid.uuid4().hex[:8]}"
    now = utc_now()
    submit_calls: list[str] = []
    query_calls: list[str] = []
    with connect() as conn:
        conn.execute(
            """INSERT INTO shots
            (id, project_id, shot_index, title, description, characters, scene, camera_motion,
             visual_prompt, negative_prompt, audio_prompt, status, retry_count, current_version_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (shot_id, project_id, 1, "镜头 01", "d", "[]", "s", "static", "p", "", "", "video_waiting_remote", 0, version_id, now, now),
        )
        conn.execute(
            """INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
             first_frame_path, last_frame_path, video_path, video_mode, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, shot_id, 1, "d", "p", "", "", None, None, None, "i2v", "test", now),
        )
        conn.execute(
            """INSERT INTO video_tasks
            (id, project_id, shot_id, version_id, job_id, provider, model, remote_task_id,
             status, cloud_status, prompt, submit_payload, status_payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, project_id, shot_id, version_id, None, "ark", "doubao-seedance-2-0-260128", "cgt-test-same-task",
             "pending_remote", "running", "p", "{}", "{}", now, now),
        )

    original_generate = video_service.generate_video_asset
    original_refresh = video_service.refresh_remote_video_task

    def fake_generate(*_args, **_kwargs):
        submit_calls.append("submit")
        raise AssertionError("waiting_remote refresh must not resubmit generation")

    def fake_refresh(video_task_id: str):
        query_calls.append(video_task_id)
        from backend.providers.video_provider import VideoGenerationResult
        return VideoGenerationResult(status="pending_remote", remote_task_id="cgt-test-same-task", provider="ark", model="m")

    video_service.generate_video_asset = fake_generate
    video_service.refresh_remote_video_task = fake_refresh
    try:
        job_id = create_job(project_id, "video_task_refresh", "正在回查同一云端任务，不会重复提交或重复计费")
        video_service.refresh_project_video_tasks(project_id, job_id)
        assert query_calls == [task_id]
        assert submit_calls == []
        print("PASS: waiting_remote refresh queries the same remote task and does not resubmit")
    finally:
        video_service.generate_video_asset = original_generate
        video_service.refresh_remote_video_task = original_refresh
        with connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)


def test_incremental_events_and_simulated_preview_update() -> None:
    project_id = _project("Preview sim")
    try:
        job_id = create_job(project_id, "video_generation", "已排队", shot_id="shot_a")
        first_batch = get_job_events(project_id, after_id=0)
        last_id = first_batch[-1]["id"]
        update_job(job_id, "running", 40, "正在下载并登记生成视频", stage="download_result", shot_id="shot_a")
        incremental = get_job_events(project_id, after_id=last_id)
        assert incremental
        assert all(item["id"] > last_id for item in incremental)
        assert get_job_events(project_id, after_id=last_id)[0]["id"] == incremental[0]["id"]
        state = {"preview": None, "status": "queued"}
        for event in get_job_events(project_id):
            state["status"] = event["status"]
            if event["event_type"] in {"asset.ready", "project.refresh_required"}:
                state["preview"] = "updated"
        update_job(
            job_id,
            "completed",
            100,
            "视频已生成，可在镜头卡片中预览",
            stage="persist_asset",
            event_type="asset.ready",
            shot_id="shot_a",
        )
        for event in get_job_events(project_id, after_id=incremental[-1]["id"]):
            state["status"] = event["status"]
            if event["event_type"] in {"asset.ready", "project.refresh_required"}:
                state["preview"] = "updated"
        assert state["status"] == "completed"
        assert state["preview"] == "updated"
        print("PASS: incremental events can drive preview updates without a full page reload model")
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def test_duplicate_poll_events_are_collapsed() -> None:
    project_id = _project("Dedupe")
    try:
        job_id = create_job(project_id, "video_generation", "已排队")
        for _ in range(3):
            append_job_event(
                job_id,
                event_type="job.update",
                stage="poll_remote",
                status="running",
                progress=55,
                message="云端正在生成",
            )
        poll_events = [item for item in get_job_events(project_id) if item["stage"] == "poll_remote"]
        assert len(poll_events) == 1
        print("PASS: identical poll events are not duplicated")
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def main() -> None:
    init_environment()
    init_db()
    test_update_job_appends_events()
    test_failed_event_redacts_secrets()
    test_sse_payload_is_structured()
    test_waiting_remote_refresh_queries_only()
    test_incremental_events_and_simulated_preview_update()
    test_duplicate_poll_events_are_collapsed()
    test_sse_http_if_available()
    print("PASS: P2 job center contract")


def test_sse_http_if_available() -> None:
    try:
        from fastapi.testclient import TestClient
        from backend.main import app
    except Exception as exc:
        print(f"SKIP: FastAPI SSE HTTP test ({exc.__class__.__name__})")
        return
    project_id = _project("SSE HTTP")
    try:
        job_id = create_job(project_id, "video_generation", "已排队", shot_id="shot_sse")
        update_job(job_id, "running", 20, "正在校验首帧关键帧", stage="prepare", shot_id="shot_sse")
        client = TestClient(app)
        with client.stream("GET", f"/api/projects/{project_id}/events?after_id=0") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")
            buf = ""
            for chunk in response.iter_text():
                buf += chunk
                if "event: job.update" in buf and "job_id" in buf and "progress" in buf:
                    break
                if len(buf) > 8000:
                    break
        assert "event: snapshot" in buf or "event: job.update" in buf
        assert "job_id" in buf
        print("PASS: SSE HTTP stream emits structured events")
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


if __name__ == "__main__":
    main()
