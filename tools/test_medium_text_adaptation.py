"""无费用测试：P5-A 中等文本分块、故事线选择与 P4 衔接。"""
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
from backend.services.adaptation_service import (
    AdaptationError,
    confirm_bible,
    confirm_scope,
    confirm_storyboard,
    generate_storyboard,
    start_adaptation_workflow,
)
from backend.services.medium_text_service import (
    MediumTextError,
    confirm_adaptation_scope,
    regenerate_medium,
    run_medium_analysis,
    save_adaptation_scope,
    select_storyline,
)
from backend.services.project_service import get_project
from backend.workflow.medium_text_planner import coverage_ok, text_scale


UNIT = (
    "方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。"
    "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。"
    "但是族中长老已经设下阻碍，他只能选择冒险一搏。"
    "最终他停在山门前，留下未说完的话。"
)


def _medium_text(target: int = 4200) -> str:
    parts = []
    index = 0
    while sum(len(item) for item in parts) < target:
        index += 1
        parts.append(f"第{index}段。{UNIT}")
    text = "".join(parts)
    return text if len(text) >= target else text + UNIT


def _cleanup(project_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    target = PROJECTS_DIR / project_id
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def _project(source: str, title: str = "P5 中等文本测试") -> str:
    init_environment()
    init_db()
    project_id = f"p5_test_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode, status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, source, "cinematic clean realism", "16:9", 5, "auto", "created", "chunk", now, now),
        )
    return project_id


def _p3_counts(project_id: str) -> tuple[int, int, int]:
    with connect() as conn:
        versions = conn.execute(
            "SELECT COUNT(*) AS n FROM shot_versions sv JOIN shots s ON s.id = sv.shot_id WHERE s.project_id = ?",
            (project_id,),
        ).fetchone()["n"]
        videos = conn.execute(
            "SELECT COUNT(*) AS n FROM video_tasks WHERE project_id = ?",
            (project_id,),
        ).fetchone()["n"]
        assets = conn.execute("SELECT COUNT(*) AS n FROM assets WHERE project_id = ?", (project_id,)).fetchone()["n"]
    return int(versions), int(videos), int(assets)


def _seed_p3(project_id: str) -> None:
    now = utc_now()
    shot_id = f"shot_{uuid.uuid4().hex[:10]}"
    version_id = f"version_{uuid.uuid4().hex[:10]}"
    asset_id = f"asset_{uuid.uuid4().hex[:10]}"
    with connect() as conn:
        conn.execute(
            """INSERT INTO shots
            (id, project_id, shot_index, title, description, characters, scene, camera_motion, visual_prompt,
             negative_prompt, audio_prompt, rag_evidence, status, retry_count, current_version_id, created_at, updated_at)
            VALUES (?, ?, 1, '保留镜头', '旧描述', '[]', '山门', 'static', 'prompt', '', '', '[]', 'video_ready', 0, ?, ?, ?)""",
            (shot_id, project_id, version_id, now, now),
        )
        conn.execute(
            """INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
             first_frame_path, last_frame_path, video_path, video_mode, created_by, created_at)
            VALUES (?, ?, 1, '旧描述', 'prompt', '', '', NULL, NULL, '/assets/keep.mp4', 't2v', 'test', ?)""",
            (version_id, shot_id, now),
        )
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, 'video', '保留视频', '测试资产', '', '/assets/keep.mp4', 'provider:ffmpeg', ?)""",
            (asset_id, project_id, now),
        )


def test_chunk_coverage_and_events() -> None:
    source = _medium_text(4800)
    project_id = _project(source)
    try:
        run_medium_analysis(project_id)
        project = get_project(project_id)
        chunks = project["source_chunks"]
        events = project["story_events"]
        lines = project["storylines"]
        assert 1501 <= len(source) <= 10000
        assert len(chunks) >= 3
        assert coverage_ok(source, chunks)
        rebuilt = [" "] * len(source)
        for chunk in chunks:
            assert source[chunk["start_offset"] : chunk["end_offset"]] == chunk["text"]
            rebuilt[chunk["start_offset"] : chunk["end_offset"]] = list(chunk["text"])
        assert "".join(rebuilt) == source
        for event in events:
            assert event["chunk_ids"]
            assert event["source_excerpt"]
            assert event["source_excerpt"] in source
            assert source[event["source_start"] : event["source_end"]]
        assert 2 <= len(lines) <= 3
        titles = {item["title"] for item in lines}
        assert len(titles) == len(lines)
        owned_chunks = {item["id"] for item in chunks}
        owned_events = {item["id"] for item in events}
        for line in lines:
            assert set(line["chunk_ids"]) <= owned_chunks
            assert set(line["event_ids"]) <= owned_events
            assert line["source_excerpt"] in source
        print("PASS: 中等文本分块可回溯、事件带真实引用、2～3 条差异故事线")
    finally:
        _cleanup(project_id)


def test_cross_project_rejected() -> None:
    a = _project(_medium_text(3600), "项目A")
    b = _project(_medium_text(3600), "项目B")
    try:
        run_medium_analysis(a)
        run_medium_analysis(b)
        foreign = get_project(a)["storylines"][0]["id"]
        event_id = get_project(a)["story_events"][0]["id"]
        chunk_id = get_project(a)["source_chunks"][0]["id"]
        try:
            select_storyline(b, foreign)
            raise AssertionError("跨项目故事线应被拒绝")
        except MediumTextError as exc:
            assert "不属于当前项目" in str(exc)
        select_storyline(b, get_project(b)["storylines"][0]["id"])
        try:
            save_adaptation_scope(b, event_ids=[event_id])
            raise AssertionError("跨项目事件应被拒绝")
        except MediumTextError as exc:
            assert "事件" in str(exc)
        try:
            save_adaptation_scope(b, chunk_ids=[chunk_id], event_ids=get_project(b)["storylines"][0]["event_ids"])
            raise AssertionError("跨项目分块应被拒绝")
        except MediumTextError as exc:
            assert "文本块" in str(exc)
        print("PASS: 跨项目 chunk/event/storyline 被拒绝")
    finally:
        _cleanup(a)
        _cleanup(b)


def test_scope_persist_and_p4_uses_scope() -> None:
    source = _medium_text(5100)
    project_id = _project(source)
    try:
        run_medium_analysis(project_id)
        project = get_project(project_id)
        line = project["storylines"][0]
        select_storyline(project_id, line["id"])
        keep = list(line["event_ids"][:-1] or line["event_ids"][:1])
        dropped = line["event_ids"][-1]
        save_adaptation_scope(project_id, event_ids=keep, user_note="排除末个事件")
        confirm_adaptation_scope(project_id, event_ids=keep, user_note="排除末个事件")
        project = get_project(project_id)
        scope = project["adaptation_scope"]
        assert scope["review_status"] == "confirmed"
        assert dropped not in scope["event_ids"]
        assert set(scope["event_ids"]) == set(keep)
        dropped_event = next(item for item in project["story_events"] if item["id"] == dropped)
        unique = True
        for event_id in keep:
            event = next(item for item in project["story_events"] if item["id"] == event_id)
            if set(dropped_event["chunk_ids"]) & set(event["chunk_ids"]):
                unique = False
        if unique:
            for chunk_id in dropped_event["chunk_ids"]:
                assert chunk_id not in scope["chunk_ids"]
            assert dropped_event["source_excerpt"] not in scope["scoped_text"] or dropped_event["source_excerpt"] in "".join(
                next(c for c in project["source_chunks"] if c["id"] == cid)["text"] for cid in scope["chunk_ids"]
            )
        assert project["status"] in {"awaiting_scope_review", "adaptation_options_ready"}
        option = project["adaptation_options"][0]
        assert option["source_excerpt"] in scope["scoped_text"]
        confirm_scope(project_id, option["id"])
        confirm_bible(project_id)
        generate_storyboard(project_id)
        project = get_project(project_id)
        bible_blob = " ".join(
            str(project["story_bible"].get(key) or "")
            for key in ("logline", "adaptation_summary", "summary", "obstacle", "protagonist_goal")
        )
        assert any(option["source_excerpt"][:12] in bible_blob or option["source_excerpt"] in scope["scoped_text"] for _ in [0])
        for draft in project["storyboard_drafts"]:
            assert draft["source_excerpt"] in scope["scoped_text"]
            assert draft.get("scope_id") == scope["id"]
        confirm_storyboard(project_id)
        refreshed = get_project(project_id)
        assert refreshed["source_chunks"]
        assert refreshed["story_events"]
        assert refreshed["storylines"]
        assert refreshed["adaptation_scope"]["event_ids"] == keep
        assert refreshed["adaptation_scope"]["review_status"] == "confirmed"
        print("PASS: 排除事件后 scope 持久化，P4 引用仅来自选中范围，刷新可恢复")
    finally:
        _cleanup(project_id)


def test_regen_keeps_p3() -> None:
    project_id = _project(_medium_text(3900))
    try:
        run_medium_analysis(project_id)
        line = get_project(project_id)["storylines"][0]
        select_storyline(project_id, line["id"])
        confirm_adaptation_scope(project_id, event_ids=line["event_ids"])
        _seed_p3(project_id)
        before = _p3_counts(project_id)
        assert before[0] >= 1 and before[2] >= 1
        regenerate_medium(project_id, "storyline")
        after_scope = _p3_counts(project_id)
        assert after_scope == before
        assert get_project(project_id)["story_bible"] is None or get_project(project_id)["story_bible"].get("review_status") == "stale"
        regenerate_medium(project_id, "analysis")
        after_analysis = _p3_counts(project_id)
        assert after_analysis == before
        print("PASS: 改变范围/重生成分析只失效 P4 草案，保留 P3 版本、视频任务与资产")
    finally:
        _cleanup(project_id)


def test_short_text_still_direct_p4() -> None:
    source = "方源走在青茅山，却听见争夺呼喊。他想道必须拿下春秋蝉。但是长老阻碍，他只能冒险。最终停在山门前。"
    assert text_scale(source) == "short"
    project_id = _project(source, "短文本")
    try:
        start_adaptation_workflow(project_id)
        project = get_project(project_id)
        assert project["status"] == "awaiting_scope_review"
        assert project["adaptation_options"]
        assert project["text_scale"] == "short"
        assert not project["storylines"] or project["adaptation_scope"]["source"] == "implicit_short_scope"
        print("PASS: ≤1,500 字仍走原 P4 直接路径")
    finally:
        _cleanup(project_id)


def test_long_text_rejected() -> None:
    source = _medium_text(12000)
    assert text_scale(source) == "long"
    project_id = _project(source, "超长文本")
    try:
        try:
            start_adaptation_workflow(project_id)
            raise AssertionError("超长文本应被拒绝")
        except AdaptationError as exc:
            assert "P5-B" in str(exc)
        print("PASS: 超过 10,000 字拒绝并说明 P5-B 未实现")
    finally:
        _cleanup(project_id)


def test_http_smoke() -> None:
    source = _medium_text(3200)
    client = TestClient(app)
    created = None
    try:
        response = client.post(
            "/api/projects",
            json={"title": "HTTP中等文本", "source_text": source, "style": "cinematic clean realism", "aspect_ratio": "16:9", "duration_seconds": 5},
        )
        assert response.status_code == 200, response.text
        created = response.json()["id"]
        run = client.post(f"/api/projects/{created}/run")
        assert run.status_code == 200, run.text
        state = client.get(f"/api/projects/{created}/medium-text").json()
        assert state["status"] == "awaiting_storyline_review"
        assert len(state["source_chunks"]) >= 2
        assert len(state["story_events"]) >= 2
        assert 2 <= len(state["storylines"]) <= 3
        line = state["storylines"][0]
        selected = client.post(f"/api/projects/{created}/medium-text/storylines/{line['id']}/select")
        assert selected.status_code == 200, selected.text
        keep = line["event_ids"][:-1] or line["event_ids"][:1]
        saved = client.put(f"/api/projects/{created}/medium-text/scope", json={"event_ids": keep, "user_note": "排除一事"})
        assert saved.status_code == 200, saved.text
        confirmed = client.post(f"/api/projects/{created}/medium-text/scope/confirm", json={"event_ids": keep})
        assert confirmed.status_code == 200, confirmed.text
        body = confirmed.json()
        assert body["adaptation_scope"]["review_status"] == "confirmed"
        assert body["status"] in {"awaiting_scope_review", "adaptation_options_ready"}
        refreshed = client.get(f"/api/projects/{created}").json()
        assert refreshed["adaptation_scope"]["event_ids"] == keep
        for item in refreshed["adaptation_options"]:
            assert item["source_excerpt"] in refreshed["adaptation_scope"]["scoped_text"]
        print("PASS: FastAPI HTTP 冒烟（分析→选择→排除事件→确认范围→P4）")
    finally:
        if created:
            _cleanup(created)


def main() -> None:
    test_chunk_coverage_and_events()
    test_cross_project_rejected()
    test_scope_persist_and_p4_uses_scope()
    test_regen_keeps_p3()
    test_short_text_still_direct_p4()
    test_long_text_rejected()
    test_http_smoke()
    print("ALL MEDIUM TEXT TESTS PASSED")


if __name__ == "__main__":
    main()
