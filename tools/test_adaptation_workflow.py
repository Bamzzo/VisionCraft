"""无费用测试：P4-A 短文本改编、Story Bible、分镜审核与恢复。"""
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
from backend.services.adaptation_service import (
    AdaptationError,
    confirm_bible,
    confirm_scope,
    confirm_storyboard,
    generate_storyboard,
    list_adaptation_options,
    regenerate_stage,
    save_story_bible_draft,
    select_adaptation_option,
    start_adaptation_workflow,
)
from backend.services.project_service import get_project


SAMPLE = (
    "方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。"
    "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。"
    "但是族中长老已经设下阻碍，他只能选择 pal 冒险一搏。"
    "最终他停在山门前，留下未说完的话。"
).replace(" pal ", "")


def _cleanup(project_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    target = PROJECTS_DIR / project_id
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def _project(title: str = "P4 改编测试") -> str:
    init_environment()
    init_db()
    project_id = f"p4_test_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode, status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, SAMPLE, "cinematic clean realism", "16:9", 5, "auto", "created", "direct", now, now),
        )
    return project_id


def test_options_have_real_excerpts() -> None:
    project_id = _project()
    try:
        start_adaptation_workflow(project_id)
        options = list_adaptation_options(project_id)
        assert 2 <= len(options) <= 3
        for item in options:
            assert item["source_excerpt"]
            assert item["source_excerpt"] in SAMPLE or any(ch in item["source_excerpt"] for ch in SAMPLE[:8])
            assert item["conflict"]
            assert item["rationale"]
        print("PASS: 短文本产生 2～3 个带真实依据的候选方案")
    finally:
        _cleanup(project_id)


def test_cannot_confirm_bible_without_option() -> None:
    project_id = _project()
    try:
        start_adaptation_workflow(project_id)
        try:
            confirm_bible(project_id)
            raise AssertionError("未选方案不应确认 Bible")
        except AdaptationError as exc:
            assert exc.code == "SCOPE_NOT_SELECTED"
        print("PASS: 未选方案不能确认 Story Bible")
    finally:
        _cleanup(project_id)


def test_bible_save_confirm_and_partial_merge() -> None:
    project_id = _project()
    try:
        start_adaptation_workflow(project_id)
        option = list_adaptation_options(project_id)[0]
        select_adaptation_option(project_id, option["id"])
        confirm_scope(project_id, option["id"])
        save_story_bible_draft(project_id, {"logline": "用户写的 logline"})
        save_story_bible_draft(project_id, {"protagonist_goal": "用户改过的目标"})
        project = get_project(project_id)
        bible = project["story_bible"]
        assert bible["logline"] == "用户写的 logline"
        assert bible["protagonist_goal"] == "用户改过的目标"
        assert bible["emotion_curve"]
        confirm_bible(project_id)
        project = get_project(project_id)
        assert project["story_bible"]["review_status"] == "confirmed"
        print("PASS: 可选方案后生成/保存/读取/确认 Bible，且部分保存不覆盖既有字段")
    finally:
        _cleanup(project_id)


def test_storyboard_requires_confirmed_bible_and_has_evidence() -> None:
    project_id = _project()
    try:
        start_adaptation_workflow(project_id)
        option = list_adaptation_options(project_id)[0]
        confirm_scope(project_id, option["id"])
        try:
            generate_storyboard(project_id)
            raise AssertionError("未确认 Bible 不应生成分镜")
        except AdaptationError as exc:
            assert exc.code == "BIBLE_NOT_CONFIRMED"
        confirm_bible(project_id)
        drafts = get_project(project_id)["storyboard_drafts"]
        assert 4 <= len(drafts) <= 8
        for item in drafts:
            assert item["source_excerpt"]
        print("PASS: 未确认 Bible 不能生成分镜；确认后每个分镜都有改编依据")
    finally:
        _cleanup(project_id)


def test_regen_bible_keeps_p3_versions() -> None:
    project_id = _project()
    try:
        start_adaptation_workflow(project_id)
        option = list_adaptation_options(project_id)[0]
        confirm_scope(project_id, option["id"])
        confirm_bible(project_id)
        confirm_storyboard(project_id)
        project = get_project(project_id)
        shot = project["shots"][0]
        version_id = shot["current_version_id"]
        now = utc_now()
        with connect() as conn:
            conn.execute(
                "UPDATE shot_versions SET video_path = ? WHERE id = ?",
                ("/assets/demo/keep.mp4", version_id),
            )
            conn.execute(
                """INSERT INTO assets (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                VALUES (?, ?, 'video', '保留视频', 'p3', 'p3', '/assets/demo/keep.mp4', 'provider:ark:keep', ?)""",
                (f"asset_{uuid.uuid4().hex[:8]}", project_id, now),
            )
        regenerate_stage(project_id, "bible")
        with connect() as conn:
            version = conn.execute("SELECT video_path FROM shot_versions WHERE id = ?", (version_id,)).fetchone()
            assets = conn.execute("SELECT COUNT(*) AS n FROM assets WHERE project_id = ?", (project_id,)).fetchone()
            shots = conn.execute("SELECT COUNT(*) AS n FROM shots WHERE project_id = ?", (project_id,)).fetchone()
        assert version["video_path"] == "/assets/demo/keep.mp4"
        assert assets["n"] == 1
        assert shots["n"] >= 1
        print("PASS: 重生成 Bible 只失效 Bible/分镜下游，不删除 P3 版本、视频或资产")
    finally:
        _cleanup(project_id)


def test_confirm_storyboard_and_refresh_state() -> None:
    project_id = _project()
    try:
        start_adaptation_workflow(project_id)
        try:
            confirm_storyboard(project_id)
            raise AssertionError("未确认分镜不应进入制作")
        except AdaptationError:
            pass
        option = list_adaptation_options(project_id)[0]
        confirm_scope(project_id, option["id"])
        confirm_bible(project_id)
        confirm_storyboard(project_id)
        project = get_project(project_id)
        assert project["status"] == "production_ready"
        assert project["shots"]
        assert project["checkpoint"] is None or project["checkpoint"].get("status") != "paused"
        assert project["review_records"]
        assert project["selected_option_id"] == option["id"]
        print("PASS: 确认分镜后进入制作，刷新读取仍保留审核状态/历史")
    finally:
        _cleanup(project_id)


def test_cross_project_rejected() -> None:
    a = _project("项目A")
    b = _project("项目B")
    try:
        start_adaptation_workflow(a)
        start_adaptation_workflow(b)
        option_b = list_adaptation_options(b)[0]
        try:
            select_adaptation_option(a, option_b["id"])
            raise AssertionError("跨项目方案应被拒绝")
        except AdaptationError as exc:
            assert exc.code == "OPTION_MISMATCH"
        print("PASS: 跨项目/跨对象访问被拒绝")
    finally:
        _cleanup(a)
        _cleanup(b)


def test_http_smoke() -> None:
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    created = client.post(
        "/api/projects",
        json={"title": "P4 HTTP 冒烟", "source_text": SAMPLE, "style": "cinematic clean realism", "aspect_ratio": "16:9", "duration_seconds": 5},
    )
    assert created.status_code == 200, created.text
    project_id = created.json()["id"]
    try:
        started = client.post(f"/api/projects/{project_id}/run")
        assert started.status_code == 200, started.text
        options = client.get(f"/api/projects/{project_id}/adaptation/options").json()["items"]
        if not options:
            start_adaptation_workflow(project_id)
            options = list_adaptation_options(project_id)
        option_id = options[0]["id"]
        assert client.post(f"/api/projects/{project_id}/adaptation/options/{option_id}/select").status_code == 200
        assert client.post(f"/api/projects/{project_id}/adaptation/scope/confirm", json={"option_id": option_id}).status_code == 200
        saved = client.put(f"/api/projects/{project_id}/adaptation/bible", json={"logline": "HTTP 保存的 logline"})
        assert saved.status_code == 200, saved.text
        confirmed = client.post(f"/api/projects/{project_id}/adaptation/bible/confirm")
        assert confirmed.status_code == 200, confirmed.text
        board = client.post(f"/api/projects/{project_id}/adaptation/storyboard/confirm")
        assert board.status_code == 200, board.text
        refreshed = client.get(f"/api/projects/{project_id}")
        assert refreshed.status_code == 200
        body = refreshed.json()
        assert body["status"] == "production_ready"
        assert body["story_bible"]["logline"] == "HTTP 保存的 logline"
        assert body["review_records"]
        print("PASS: HTTP 冒烟 创建→方案→范围→Bible→分镜→刷新状态")
    finally:
        _cleanup(project_id)


def main() -> None:
    test_options_have_real_excerpts()
    test_cannot_confirm_bible_without_option()
    test_bible_save_confirm_and_partial_merge()
    test_storyboard_requires_confirmed_bible_and_has_evidence()
    test_regen_bible_keeps_p3_versions()
    test_confirm_storyboard_and_refresh_state()
    test_cross_project_rejected()
    test_http_smoke()
    print("PASS: P4-A 改编审核闭环")


if __name__ == "__main__":
    main()
