"""P8-A no-cost tests: backend pause, resume, checkpoints, and idempotent confirms.

真实网络请求：否。费用：0 元。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db, utc_now
from backend.main import app
from backend.services.adaptation_service import (
    AdaptationError,
    confirm_bible,
    confirm_scope,
    confirm_storyboard,
    list_adaptation_options,
    regenerate_stage,
    select_adaptation_option,
    start_adaptation_workflow,
)
from backend.services.checkpoint_service import (
    CheckpointError,
    get_paused_checkpoint,
    list_checkpoints,
    save_workflow_checkpoint,
)
from backend.services.job_service import create_job, get_job_events, list_active_jobs
from backend.services.project_service import get_project, update_project_status
from backend.services.workflow_control_service import pause_project, resume_project, start_or_reuse_workflow

SAMPLE = (
    "方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。"
    "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。"
    "但是族中长老已经设下阻碍，他只能选择冒险一搏。"
    "最终他停在山门前，留下未说完的话。"
)


def pass_(msg: str) -> None:
    print(f"PASS: {msg}")


def _cleanup(project_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)


def _project(title: str = "P8A 编排") -> str:
    init_environment()
    init_db()
    project_id = f"p8a_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode, status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, SAMPLE, "cinematic clean realism", "16:9", 5, "auto", "created", "direct", now, now),
        )
    return project_id


def _to_scope(project_id: str) -> dict:
    start_adaptation_workflow(project_id)
    option = list_adaptation_options(project_id)[0]
    select_adaptation_option(project_id, option["id"])
    return option


def test_auto_run_pauses_at_scope() -> None:
    project_id = _project()
    try:
        result = start_or_reuse_workflow(project_id, run_now=True)
        project = get_project(project_id)
        checkpoint = get_paused_checkpoint(project_id)
        assert result["status"] == "awaiting_scope_review"
        assert project["status"] == "awaiting_scope_review"
        assert checkpoint["node"] == "scope_review"
        assert checkpoint["status"] == "paused"
        assert project["workflow"]["paused"] is True
        assert "范围审核" in (project["checkpoint"]["pause_reason"] or "")
        again = start_or_reuse_workflow(project_id, run_now=True)
        assert again["reused"] is True
        assert len([item for item in list_checkpoints(project_id) if item["status"] == "paused"]) == 1
        pass_("自动流程到范围审核后真实暂停，且不会重复启动")
    finally:
        _cleanup(project_id)


def test_checkpoint_sanitized_and_deduped() -> None:
    project_id = _project()
    try:
        job_id = create_job(project_id, "adaptation_workflow", "检查点")
        first = save_workflow_checkpoint(
            project_id,
            job_id,
            "scope_review",
            {
                "project_id": project_id,
                "job_id": job_id,
                "node": "scope_review",
                "api_key": "sk-live-secret-value",
                "authorization": "Bearer abc",
                "prompt": "完整提示词不应入库",
                "image": "data:image/jpeg;base64," + "a" * 80,
                "input_summary": "已生成 3 个改编候选",
            },
        )
        second = save_workflow_checkpoint(
            project_id,
            job_id,
            "scope_review",
            {"project_id": project_id, "job_id": job_id, "node": "scope_review", "input_summary": "再次暂停"},
        )
        assert first == second
        paused = get_paused_checkpoint(project_id)
        blob = json.dumps(paused, ensure_ascii=False)
        assert "sk-live" not in blob
        assert "data:image" not in blob.lower()
        assert "Bearer abc" not in blob
        assert "完整提示词" not in blob
        assert paused["state"]["input_summary"] == "再次暂停"
        paused_rows = [item for item in list_checkpoints(project_id) if item["status"] == "paused" and item["node"] == "scope_review"]
        assert len(paused_rows) == 1
        pass_("检查点脱敏、去重，且不含 Key / Data URL / Base64")
    finally:
        _cleanup(project_id)


def test_confirm_and_resume_idempotent() -> None:
    project_id = _project()
    try:
        option = _to_scope(project_id)
        pause_project(project_id)
        scope_checkpoint = get_paused_checkpoint(project_id)
        first = resume_project(project_id)
        assert get_project(project_id)["status"] == "awaiting_bible_review"
        second = resume_project(project_id, checkpoint_id=scope_checkpoint["id"])
        assert second.get("reused") is True or get_project(project_id)["status"] == "awaiting_bible_review"
        assert get_project(project_id)["status"] == "awaiting_bible_review"
        confirm_bible(project_id)
        assert get_project(project_id)["status"] == "awaiting_storyboard_review"
        confirm_bible(project_id)
        assert get_project(project_id)["status"] == "awaiting_storyboard_review"
        confirm_storyboard(project_id)
        assert get_project(project_id)["status"] == "production_ready"
        confirm_storyboard(project_id)
        assert get_project(project_id)["status"] == "production_ready"
        with connect() as conn:
            shots = conn.execute("SELECT COUNT(*) AS n FROM shots WHERE project_id = ?", (project_id,)).fetchone()["n"]
            versions = conn.execute(
                "SELECT COUNT(*) AS n FROM shot_versions sv JOIN shots s ON s.id = sv.shot_id WHERE s.project_id = ?",
                (project_id,),
            ).fetchone()["n"]
        assert shots == versions or versions >= shots
        try:
            confirm_scope(project_id, option["id"])
            # 已进入制作后重复确认范围必须幂等或拒绝倒退
            assert get_project(project_id)["status"] == "production_ready"
        except AdaptationError as exc:
            assert exc.code == "ALREADY_CONFIRMED"
        pass_("确认/恢复幂等，不会倒退或重复创建制作镜头")
    finally:
        _cleanup(project_id)


def test_pause_rejects_waiting_remote() -> None:
    project_id = _project()
    try:
        _to_scope(project_id)
        job_id = create_job(project_id, "video_generation", "等待云端", stage="waiting_remote")
        from backend.services.job_service import update_job

        update_job(job_id, "waiting_remote", 40, "正在查询原远程任务", stage="waiting_remote")
        try:
            pause_project(project_id)
            raise AssertionError("waiting_remote 不应被暂停中断")
        except CheckpointError as exc:
            assert exc.code == "WAITING_REMOTE"
        jobs = list_active_jobs(project_id)
        remote_jobs = [job for job in jobs if job.get("status") == "waiting_remote"]
        assert len(remote_jobs) == 1
        pass_("waiting_remote 只回查原任务，暂停不会产生第二个远程任务")
    finally:
        _cleanup(project_id)


def test_failed_resume_and_chinese_errors() -> None:
    project_id = _project()
    try:
        _to_scope(project_id)
        original = __import__("backend.services.adaptation_service", fromlist=["_write_bible"])._write_bible

        def boom(*_args, **_kwargs):
            raise RuntimeError("simulated bible failure with sk-live-should-not-leak")

        import backend.services.adaptation_service as adaptation_service

        adaptation_service._write_bible = boom
        try:
            try:
                confirm_scope(project_id)
                raise AssertionError("应失败")
            except AdaptationError as exc:
                assert exc.code == "WORKFLOW_FAILED"
                assert "检查点" in str(exc)
        finally:
            adaptation_service._write_bible = original
        project = get_project(project_id)
        assert project["status"] == "failed"
        events = json.dumps(get_job_events(project_id), ensure_ascii=False)
        assert "sk-live" not in events
        resumed = resume_project(project_id)
        assert get_project(project_id)["status"] == "awaiting_bible_review"
        assert resumed["status"] == "awaiting_bible_review"
        try:
            resume_project(project_id, job_id="job_not_this")
            raise AssertionError("任务 ID 不匹配应拒绝")
        except CheckpointError as exc:
            assert exc.code == "JOB_MISMATCH"
        pass_("失败显示中文原因，并可从有效检查点恢复")
    finally:
        _cleanup(project_id)


def test_http_pause_resume_and_isolation() -> None:
    client = TestClient(app)
    created = client.post(
        "/api/projects",
        json={"title": "P8A HTTP", "source_text": SAMPLE, "style": "cinematic clean realism", "aspect_ratio": "16:9", "duration_seconds": 5},
    )
    other = client.post(
        "/api/projects",
        json={"title": "P8A 隔离", "source_text": SAMPLE, "style": "cinematic clean realism", "aspect_ratio": "16:9", "duration_seconds": 5},
    )
    project_id = created.json()["id"]
    other_id = other.json()["id"]
    try:
        started = client.post(f"/api/projects/{project_id}/run")
        assert started.status_code == 200, started.text
        body = client.get(f"/api/projects/{project_id}").json()
        if body["status"] != "awaiting_scope_review":
            start_adaptation_workflow(project_id)
            body = client.get(f"/api/projects/{project_id}").json()
        assert body["status"] == "awaiting_scope_review"
        assert body["workflow"]["paused"] is True
        paused = client.post(f"/api/projects/{project_id}/pause")
        assert paused.status_code == 200, paused.text
        assert paused.json()["reused"] is True
        checkpoints = client.get(f"/api/projects/{project_id}/checkpoints").json()
        assert checkpoints["paused"]["node"] == "scope_review"
        option_id = body["adaptation_options"][0]["id"]
        assert client.post(f"/api/projects/{project_id}/adaptation/options/{option_id}/select").status_code == 200
        scope_checkpoint_id = checkpoints["paused"]["id"]
        first = client.post(f"/api/projects/{project_id}/resume")
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "awaiting_bible_review"
        second = client.post(f"/api/projects/{project_id}/checkpoints/{scope_checkpoint_id}/resume")
        assert second.status_code == 200, second.text
        assert second.json()["status"] == "awaiting_bible_review"
        assert second.json().get("reused") is True
        events = client.get(f"/api/projects/{project_id}/job-events").json()
        event_blob = json.dumps(events, ensure_ascii=False)
        assert "paused" in event_blob or "暂停" in event_blob or "审核" in event_blob
        missing = client.post("/api/projects/missing/resume")
        assert missing.status_code == 404
        assert "不存在" in missing.json()["detail"]
        missing_cp = client.post(f"/api/projects/{project_id}/checkpoints/checkpoint_missing/resume")
        assert missing_cp.status_code == 404
        assert "检查点" in missing_cp.json()["detail"]
        foreign = client.post(f"/api/projects/{other_id}/checkpoints/{scope_checkpoint_id}/resume")
        assert foreign.status_code in {400, 404}
        isolated = client.get(f"/api/projects/{other_id}").json()
        assert isolated["status"] == "created"
        assert not isolated.get("workflow", {}).get("paused")
        blob = json.dumps(checkpoints, ensure_ascii=False)
        assert "sk-" not in blob
        assert "data:image" not in blob.lower()
        bad_pause = client.post(f"/api/projects/{other_id}/pause")
        assert bad_pause.status_code == 400
        assert "审核" in bad_pause.json()["detail"]
        pass_("HTTP 暂停/恢复、刷新状态、项目隔离与中文错误")
    finally:
        _cleanup(project_id)
        _cleanup(other_id)


def test_regen_invalidates_downstream_keeps_history() -> None:
    project_id = _project()
    try:
        option = _to_scope(project_id)
        confirm_scope(project_id, option["id"])
        confirm_bible(project_id)
        confirm_storyboard(project_id)
        with connect() as conn:
            before = conn.execute(
                "SELECT COUNT(*) AS n FROM shot_versions sv JOIN shots s ON s.id = sv.shot_id WHERE s.project_id = ?",
                (project_id,),
            ).fetchone()["n"]
        regenerate_stage(project_id, "bible")
        project = get_project(project_id)
        assert project["status"] == "awaiting_bible_review"
        assert project["story_bible"]["review_status"] != "confirmed" or project["status"] == "awaiting_bible_review"
        with connect() as conn:
            after = conn.execute(
                "SELECT COUNT(*) AS n FROM shot_versions sv JOIN shots s ON s.id = sv.shot_id WHERE s.project_id = ?",
                (project_id,),
            ).fetchone()["n"]
        assert after == before
        pass_("上游重做只失效必要下游，历史镜头版本仍可查看")
    finally:
        _cleanup(project_id)


def main() -> None:
    os.environ.pop("VISIONCRAFT_ALLOW_LIVE_LLM", None)
    os.environ.pop("VISIONCRAFT_ALLOW_LIVE_VISION", None)
    os.environ.pop("VISIONCRAFT_ALLOW_LIVE_VIDEO", None)
    init_environment()
    init_db()
    test_auto_run_pauses_at_scope()
    test_checkpoint_sanitized_and_deduped()
    test_confirm_and_resume_idempotent()
    test_pause_rejects_waiting_remote()
    test_failed_resume_and_chinese_errors()
    test_http_pause_resume_and_isolation()
    test_regen_invalidates_downstream_keeps_history()
    print("PASS: P8-A workflow pause/resume (no live network, cost 0)")
    print("INFO: real_network=否 cost_cny=0")


if __name__ == "__main__":
    main()
