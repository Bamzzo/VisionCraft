"""无费用测试：PATCH 项目设置落库。不调用付费 API，不删除用户项目。"""
from __future__ import annotations

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
from backend.services.project_service import get_project

CREATED: list[str] = []
SAMPLE = (
    "方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。"
    "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。"
)


def pass_(msg: str) -> None:
    print(f"PASS: {msg}")


def _client() -> TestClient:
    init_environment()
    init_db()
    return TestClient(app)


def _cleanup(project_id: str) -> None:
    if not project_id or not str(project_id).startswith("pset_"):
        return
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
    print(f"CLEANED: {project_id}")


def _seed(title: str = "设置落库样本") -> str:
    project_id = f"pset_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, output_resolution,
             shot_count_mode, status, routing_mode, assembly_stale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, SAMPLE, "cinematic clean realism", "16:9", 5, "1280x720",
             "auto", "created", "direct", 0, now, now),
        )
        shot_id = f"shot_{uuid.uuid4().hex[:8]}"
        version_id = f"version_{uuid.uuid4().hex[:8]}"
        conn.execute(
            """INSERT INTO shots
            (id, project_id, shot_index, title, description, characters, scene, camera_motion,
             visual_prompt, negative_prompt, audio_prompt, status, retry_count, current_version_id,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (shot_id, project_id, 1, "样本镜头", "描述", "[]", "夜路", "固定", "prompt", "", "",
             "draft", 0, version_id, now, now),
        )
        conn.execute(
            """INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
             first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, shot_id, 1, "描述", "prompt", "", "", None, None, None, "t2v",
             "ark", "local-fixture", "pset", now),
        )
        conn.execute(
            """INSERT INTO jobs
            (id, project_id, type, status, progress, message, retry_count, created_at, updated_at, stage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"job_{uuid.uuid4().hex[:8]}", project_id, "adaptation_workflow", "completed", 100,
             "已有任务", 0, now, now, "completed"),
        )
    CREATED.append(project_id)
    return project_id


def _add_final(project_id: str) -> str:
    now = utc_now()
    asset_id = f"asset_{uuid.uuid4().hex[:8]}"
    path = f"/assets/{project_id}/{asset_id}.mp4"
    with connect() as conn:
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, 'final-video', ?, ?, ?, ?, ?, ?)""",
            (asset_id, project_id, "成片", "占位成片", "fixture", path, "provider:ffmpeg:local", now),
        )
    return asset_id


def _version_snapshot(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT sv.id, sv.shot_id, sv.version_number, sv.description, sv.video_path,
                   sv.provider, sv.model, sv.created_at
            FROM shot_versions sv
            JOIN shots s ON s.id = sv.shot_id
            WHERE s.project_id = ?
            ORDER BY sv.id
            """,
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def test_patch_title_keeps_spec_and_versions() -> None:
    client = _client()
    project_id = _seed()
    try:
        before = _version_snapshot(project_id)
        jobs_before = len(get_project(project_id)["jobs"])
        response = client.patch(
            f"/api/projects/{project_id}",
            json={"title": "只改标题的项目"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["title"] == "只改标题的项目"
        assert body["duration_seconds"] == 5
        assert body["aspect_ratio"] == "16:9"
        assert body["output_resolution"] == "1280x720"
        assert body["assembly_stale"] in {0, False}
        assert body["source_text"].startswith("方源")
        after = _version_snapshot(project_id)
        assert after == before
        assert len(body["jobs"]) >= jobs_before + 1
        assert any(job.get("type") == "project_settings" for job in body["jobs"])
        events = client.get(f"/api/projects/{project_id}/job-events").json()
        types = [item.get("event_type") for item in events.get("events", [])]
        assert "project.refresh_required" in types
        pass_("只改标题不标记成片过期，且不改写 shot_versions")
    finally:
        _cleanup(project_id)


def test_unset_fields_stay() -> None:
    client = _client()
    project_id = _seed()
    try:
        response = client.patch(f"/api/projects/{project_id}", json={"duration_seconds": 8})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["duration_seconds"] == 8
        assert body["title"].startswith("设置落库")
        assert body["aspect_ratio"] == "16:9"
        assert body["output_resolution"] == "1280x720"
        pass_("未提供字段保持不变")
    finally:
        _cleanup(project_id)


def test_spec_change_marks_stale_when_final_exists() -> None:
    client = _client()
    project_id = _seed()
    try:
        _add_final(project_id)
        before = _version_snapshot(project_id)
        response = client.patch(
            f"/api/projects/{project_id}",
            json={"output_resolution": "1920x1080", "aspect_ratio": "16:9"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["output_resolution"] == "1920x1080"
        assert body["assembly_stale"] in {1, True}
        assert _version_snapshot(project_id) == before
        finals = [item for item in body["assets"] if item["type"] == "final-video"]
        assert len(finals) == 1
        pass_("影响成片规格的修改会设置 assembly_stale，且保留成片历史与镜头版本")
    finally:
        _cleanup(project_id)


def test_validation_chinese() -> None:
    client = _client()
    project_id = _seed()
    try:
        empty = client.patch(f"/api/projects/{project_id}", json={"title": "  "})
        assert empty.status_code == 400
        assert "不能为空" in empty.json()["detail"]

        ratio = client.patch(f"/api/projects/{project_id}", json={"aspect_ratio": "4:3"})
        assert ratio.status_code == 400
        assert "画幅比例无效" in ratio.json()["detail"]

        duration = client.patch(f"/api/projects/{project_id}", json={"duration_seconds": 3})
        assert duration.status_code == 400
        assert "目标时长无效" in duration.json()["detail"]

        bad_str = client.patch(f"/api/projects/{project_id}", json={"duration_seconds": "abc"})
        assert bad_str.status_code == 400
        assert "目标时长无效" in bad_str.json()["detail"]

        resolution = client.patch(f"/api/projects/{project_id}", json={"output_resolution": "4k"})
        assert resolution.status_code == 400
        assert "输出分辨率无效" in resolution.json()["detail"]

        unknown = client.patch(f"/api/projects/{project_id}", json={"source_text": "不应写入"})
        assert unknown.status_code == 400
        assert "只能修改" in unknown.json()["detail"]

        missing = client.patch("/api/projects/does-not-exist", json={"title": "幽灵"})
        assert missing.status_code == 404
        assert missing.json()["detail"] == "项目不存在。"

        empty_body = client.patch(f"/api/projects/{project_id}", json={})
        assert empty_body.status_code == 400
        assert "没有可保存" in empty_body.json()["detail"]
        pass_("空标题、非法时长/比例/分辨率返回中文 400，缺项目返回中文 404")
    finally:
        _cleanup(project_id)


def test_create_persists_resolution() -> None:
    client = _client()
    payload = {
        "title": "创建分辨率",
        "source_text": SAMPLE,
        "style": "cinematic clean realism",
        "aspect_ratio": "9:16",
        "duration_seconds": 6,
        "output_resolution": "720x1280",
        "shot_count_mode": "auto",
        "review_mode": False,
    }
    created = client.post("/api/projects", json=payload)
    assert created.status_code == 200, created.text
    project_id = created.json()["id"]
    CREATED.append(project_id)
    try:
        fetched = client.get(f"/api/projects/{project_id}").json()
        assert fetched["output_resolution"] == "720x1280"
        assert fetched["aspect_ratio"] == "9:16"
        assert fetched["duration_seconds"] == 6
        pass_("创建项目时输出分辨率一并落库，刷新后可恢复")
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
        print(f"CLEANED: {project_id}")


def main() -> None:
    try:
        test_patch_title_keeps_spec_and_versions()
        test_unset_fields_stay()
        test_spec_change_marks_stale_when_final_exists()
        test_validation_chinese()
        test_create_persists_resolution()
        print("PASS: 项目设置落库合同")
    finally:
        for project_id in list(CREATED):
            _cleanup(project_id)


if __name__ == "__main__":
    main()
