"""无费用测试：镜头草稿、不可变版本、局部生成绑定与回滚指针。"""
from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db, utc_now
from backend.providers.video_provider import VideoGenerationResult
from backend.services.job_service import get_job_events
from backend.services.project_service import get_project
from backend.services.shot_edit_service import (
    ShotEditError,
    freeze_shot_version,
    get_shot_editor,
    prepare_version_for_generation,
    rollback_shot_to_version,
    save_shot_draft,
)


def _cleanup(project_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    target = PROJECTS_DIR / project_id
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def _seed(first_frame: str | None = "/assets/demo/first.jpg", video_path: str | None = None) -> tuple[str, str, str]:
    init_environment()
    init_db()
    project_id = f"p3_test_{uuid.uuid4().hex[:10]}"
    shot_id = f"shot_{uuid.uuid4().hex[:8]}"
    version_id = f"version_{uuid.uuid4().hex[:8]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode, status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, "P3 版本测试", "测试文本", "test", "16:9", 5, "auto", "testing", "direct", now, now),
        )
        conn.execute(
            """INSERT INTO shots
            (id, project_id, shot_index, title, description, characters, scene, camera_motion,
             visual_prompt, negative_prompt, audio_prompt, status, retry_count, current_version_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (shot_id, project_id, 1, "镜头 01", "原描述", "[]", "scene", "static", "原提示", "", "", "keyframes_ready", 0, version_id, now, now),
        )
        conn.execute(
            """INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
             first_frame_path, last_frame_path, video_path, video_mode, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, shot_id, 1, "原描述", "原提示", "", "", first_frame, None, video_path, "t2v", "seed", now),
        )
    return project_id, shot_id, version_id


def _version_count(shot_id: str) -> int:
    with connect() as conn:
        return int(conn.execute("SELECT COUNT(*) AS n FROM shot_versions WHERE shot_id = ?", (shot_id,)).fetchone()["n"])


def test_save_draft_does_not_overwrite_history() -> None:
    project_id, shot_id, version_id = _seed()
    try:
        save_shot_draft(project_id, shot_id, {"description": "草稿描述", "camera_motion": "缓慢推进"})
        save_shot_draft(project_id, shot_id, {"description": "再次保存草稿"})
        with connect() as conn:
            version = conn.execute("SELECT * FROM shot_versions WHERE id = ?", (version_id,)).fetchone()
            draft = conn.execute("SELECT * FROM shot_drafts WHERE shot_id = ?", (shot_id,)).fetchone()
        assert _version_count(shot_id) == 1
        assert version["description"] == "原描述"
        assert draft["description"] == "再次保存草稿"
        print("PASS: 保存草稿不会覆盖历史版本")
    finally:
        _cleanup(project_id)


def test_freeze_creates_version_only_on_material_change() -> None:
    project_id, shot_id, version_id = _seed()
    try:
        first = freeze_shot_version(project_id, shot_id, {"description": "新的镜头描述"})
        assert first["created"] is True
        assert first["version_id"] != version_id
        second = freeze_shot_version(project_id, shot_id, {"description": "新的镜头描述"})
        assert second["created"] is False
        assert second["version_id"] == first["version_id"]
        assert _version_count(shot_id) == 2
        print("PASS: 实质修改才创建新版本，无修改不重复创建")
    finally:
        _cleanup(project_id)


def test_local_generate_binds_new_version() -> None:
    project_id, shot_id, old_id = _seed(video_path="/assets/demo/old.mp4")
    try:
        frozen = prepare_version_for_generation(
            project_id,
            shot_id,
            {"description": "局部重生成描述", "video_mode": "t2v", "provider": "ark"},
        )
        assert frozen["id"] != old_id
        assert frozen["video_path"] is None
        with connect() as conn:
            old = conn.execute("SELECT video_path FROM shot_versions WHERE id = ?", (old_id,)).fetchone()
            shot = conn.execute("SELECT current_version_id FROM shots WHERE id = ?", (shot_id,)).fetchone()
        assert old["video_path"] == "/assets/demo/old.mp4"
        assert shot["current_version_id"] == frozen["id"]
        print("PASS: 局部生成绑定新 version_id 且保留旧视频")
    finally:
        _cleanup(project_id)


def test_i2v_without_first_frame_rejected() -> None:
    project_id, shot_id, version_id = _seed(first_frame=None)
    try:
        before = _version_count(shot_id)
        try:
            prepare_version_for_generation(project_id, shot_id, {"video_mode": "i2v", "provider": "ark"})
            raise AssertionError("缺首帧的 I2V 应当被拒绝")
        except ShotEditError as exc:
            assert "首帧" in str(exc) or exc.code == "MISSING_FIRST_FRAME"
        assert _version_count(shot_id) == before
        print("PASS: I2V 缺首帧在提交前被拒绝且不创建新版本")
    finally:
        _cleanup(project_id)


def test_rollback_only_moves_pointer() -> None:
    project_id, shot_id, v1 = _seed(video_path="/assets/demo/v1.mp4")
    try:
        frozen = freeze_shot_version(project_id, shot_id, {"description": "第二版描述", "camera_motion": "摇镜"})
        v2 = frozen["version_id"]
        editor = rollback_shot_to_version(project_id, shot_id, v1)
        assert editor["shot"]["current_version_id"] == v1
        ids = {item["id"] for item in editor["versions"]}
        assert {v1, v2} <= ids
        with connect() as conn:
            tasks = conn.execute("SELECT COUNT(*) AS n FROM video_tasks WHERE project_id = ?", (project_id,)).fetchone()
            versions = conn.execute("SELECT COUNT(*) AS n FROM shot_versions WHERE shot_id = ?", (shot_id,)).fetchone()
        assert tasks["n"] == 0
        assert versions["n"] == 2
        print("PASS: 回滚只切换 current_version_id，不删除版本/资产、不创建云端任务")
    finally:
        _cleanup(project_id)


def test_version_mismatch_rejected() -> None:
    project_a, shot_a, version_a = _seed()
    project_b, shot_b, version_b = _seed()
    try:
        try:
            prepare_version_for_generation(project_a, shot_a, None, version_b)
            raise AssertionError("跨镜头 version_id 应当被拒绝")
        except ShotEditError as exc:
            assert exc.code == "VERSION_MISMATCH"
        try:
            rollback_shot_to_version(project_a, shot_a, version_b)
            raise AssertionError("跨镜头回滚应当被拒绝")
        except ShotEditError as exc:
            assert exc.code == "VERSION_MISMATCH"
        print("PASS: 版本与镜头/项目不匹配时被拒绝")
    finally:
        _cleanup(project_a)
        _cleanup(project_b)


def test_history_video_still_readable() -> None:
    project_id, shot_id, v1 = _seed(video_path="/assets/demo/history.mp4")
    try:
        freeze_shot_version(project_id, shot_id, {"description": "新版本无视频"})
        project = get_project(project_id)
        shot = project["shots"][0]
        history = next(item for item in shot["versions"] if item["id"] == v1)
        current = next(item for item in shot["versions"] if item["id"] == shot["current_version_id"])
        assert history["video_path"] == "/assets/demo/history.mp4"
        assert not current.get("video_path")
        print("PASS: 历史版本视频仍可查询")
    finally:
        _cleanup(project_id)


def test_traceability_and_no_secrets() -> None:
    project_id, shot_id, _ = _seed()
    try:
        frozen = prepare_version_for_generation(
            project_id,
            shot_id,
            {
                "description": "可追溯版本",
                "video_mode": "i2v",
                "provider": "ark",
                "model": None,
                "duration_seconds": 5,
                "first_frame_path": "/assets/demo/first.jpg",
            },
        )
        blob = str(frozen)
        assert frozen["id"]
        assert frozen["video_mode"] == "i2v"
        assert frozen["provider"] == "ark"
        assert frozen["first_frame_path"] == "/assets/demo/first.jpg"
        assert "sk-" not in blob
        assert "api_key" not in blob.lower() or frozen.get("api_key") is None
        events = get_job_events(project_id)
        assert "Bearer" not in str(events)
        print("PASS: 版本可追溯且无敏感内容")
    finally:
        _cleanup(project_id)


def test_http_draft_freeze_rollback_and_enqueue() -> None:
    from fastapi.testclient import TestClient
    from backend.main import app

    project_id, shot_id, v1 = _seed(video_path="/assets/demo/old.mp4")
    try:
        client = TestClient(app)
        save_res = client.put(
            f"/api/projects/{project_id}/shots/{shot_id}/draft",
            json={"description": "HTTP 草稿", "camera_motion": "跟拍"},
        )
        assert save_res.status_code == 200, save_res.text
        freeze_res = client.post(
            f"/api/projects/{project_id}/shots/{shot_id}/versions",
            json={"description": "HTTP 新版本"},
        )
        assert freeze_res.status_code == 200, freeze_res.text
        payload = freeze_res.json()
        assert payload["created"] is True
        rollback_res = client.post(f"/api/projects/{project_id}/shots/{shot_id}/versions/{v1}/rollback")
        assert rollback_res.status_code == 200, rollback_res.text
        assert rollback_res.json()["shots"][0]["current_version_id"] == v1
        mismatch = client.post(f"/api/projects/{project_id}/shots/{shot_id}/versions/{uuid.uuid4().hex}/rollback")
        assert mismatch.status_code in {400, 404}

        fake = VideoGenerationResult(status="completed", video_path="/assets/demo/mock.mp4", provider="ark", model="mock")
        with patch("backend.services.video_service.generate_video_asset", return_value=fake):
            queued = client.post(
                f"/api/projects/{project_id}/shots/{shot_id}/video",
                json={"description": "入队绑定版本", "video_mode": "t2v", "provider": "ark"},
            )
        assert queued.status_code == 200, queued.text
        body = queued.json()
        assert body["job_id"]
        assert body["version_id"]
        assert body["version_id"] != v1
        events = client.get(f"/api/projects/{project_id}/events?once=true")
        assert events.status_code == 200
        assert "text/event-stream" in events.headers.get("content-type", "")
        print("PASS: HTTP 草稿/冻结/回滚/入队与 SSE once 冒烟成功")
    finally:
        _cleanup(project_id)


def main() -> None:
    test_save_draft_does_not_overwrite_history()
    test_freeze_creates_version_only_on_material_change()
    test_local_generate_binds_new_version()
    test_i2v_without_first_frame_rejected()
    test_rollback_only_moves_pointer()
    test_version_mismatch_rejected()
    test_history_video_still_readable()
    test_traceability_and_no_secrets()
    test_http_draft_freeze_rollback_and_enqueue()
    print("PASS: P3 镜头版本与局部重生成闭环")


if __name__ == "__main__":
    main()
