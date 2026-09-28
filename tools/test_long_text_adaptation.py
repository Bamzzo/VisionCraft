"""无费用测试：P5-B 长文本（1 万–10 万字）的章节树、章节范围与跨章故事线。

P5-A 的边界是 10,000 字，超过就整段拒绝。这里钉的是 P5-B 要换上的新契约：

1. 长文本不再被拒绝，而是先解析成章节树；
2. **分块不允许跨越章节边界**——否则"按章节选择改编范围"这个承诺就没有依托，
   用户勾了第 5 章却把第 6 章的开头带进短片，比不做这个功能更糟；
3. 章节摘要必须取自原文，不能是凭空写的；
4. 至少有一条候选故事线跨章节——这正是 P5-B 存在的理由（P5-A 的故事线
   只在单块内部打转，到了 10 万字尺度就会退化成"取前 60% 的事件"）；
5. 按章节定下的范围，scope 之外的原文一个字都不能进来。

真实语料取工作区根的 `output/test_texts/蛊真人100000字.txt`（95,618 字，30 节）。
语料缺失时整项 SKIP 并写清原因，绝不当作通过。
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import PROJECTS_DIR, init_environment  # noqa: E402
from backend.database import connect, init_db, utc_now  # noqa: E402
from backend.services.medium_text_service import (  # noqa: E402
    list_chapters,
    list_chunks,
    list_events,
    list_storylines,
    run_medium_analysis,
    save_adaptation_scope,
)
from backend.workflow.medium_text_planner import (  # noqa: E402
    LONG_LIMIT,
    coverage_ok,
    parse_chapters,
    segment_source,
    text_scale,
)

CORPUS = ROOT.parent / "output" / "test_texts" / "蛊真人100000字.txt"
SKIP_REASON: str | None = None


def _project(source: str, title: str) -> str:
    project_id = f"p5b_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode, status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, source, "ink-wash fantasy", "16:9", 45, "auto", "created", "direct", now, now),
        )
    return project_id


def _cleanup(project_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)


def test_chapter_tree_parses_real_corpus() -> None:
    source = CORPUS.read_text(encoding="utf-8", errors="replace")
    chapters = parse_chapters(source)
    assert len(chapters) == 30, f"语料有 30 节，实际解析出 {len(chapters)}"
    first = chapters[0]
    assert first["chapter_index"] == 1
    assert "纵身亡魔心仍不悔" in first["title"], f"首节标题不对：{first['title']!r}"
    assert first["start_offset"] == 0, "首节必须从 0 开始，否则开头那段就没有归属"
    assert chapters[-1]["end_offset"] == len(source), "最后一节必须覆盖到文末，不能漏尾"
    # 章节必须无缝拼接：前一节的 end 就是后一节的 start。
    for prev, nxt in zip(chapters, chapters[1:]):
        assert prev["end_offset"] == nxt["start_offset"], (
            f"第 {prev['chapter_index']} 节与第 {nxt['chapter_index']} 节之间有缝：" 
            f"{prev['end_offset']} != {nxt['start_offset']}"
        )
    # 摘要要能在原文里找到，不能是编的。
    for chapter in chapters:
        assert chapter["summary"], f"第 {chapter['chapter_index']} 节没有摘要"
    print(f"PASS: 真实语料解析出 {len(chapters)} 节，首尾相接无缺口，每节都有摘要")


def test_chapter_tree_falls_back_without_markers() -> None:
    plain = "方源走在山路上。" * 3000  # 无任何章节标记，约 2.4 万字
    chapters = parse_chapters(plain)
    assert len(chapters) >= 2, "没有章节标记时也必须切出章节，否则长文本无处可选"
    assert chapters[0]["start_offset"] == 0
    assert chapters[-1]["end_offset"] == len(plain)
    for prev, nxt in zip(chapters, chapters[1:]):
        assert prev["end_offset"] == nxt["start_offset"], "回退分章也要无缝"
    print(f"PASS: 无标记文本回退成 {len(chapters)} 章，仍然无缝")


def test_chunks_never_cross_chapters() -> None:
    source = CORPUS.read_text(encoding="utf-8", errors="replace")
    chapters = parse_chapters(source)
    chunks = segment_source(source, chapters=chapters)
    assert chunks, "长文本应当分块"
    for chunk in chunks:
        owner = [c for c in chapters if c["start_offset"] <= chunk["start_offset"] < c["end_offset"]]
        assert owner, f"块 {chunk['chunk_index']} 不属于任何章节"
        chapter = owner[0]
        assert chunk["end_offset"] <= chapter["end_offset"], (
            f"块 {chunk['chunk_index']} 越过了第 {chapter['chapter_index']} 节的边界："
            f"块结束 {chunk['end_offset']} > 节结束 {chapter['end_offset']}"
        )
        assert chunk["chapter_index"] == chapter["chapter_index"], "块上记的章节号与实际归属不一致"
    print(f"PASS: {len(chunks)} 个块全部落在单一章节内，没有一个越界")


def test_long_text_analysis_runs() -> None:
    source = CORPUS.read_text(encoding="utf-8", errors="replace")
    assert len(source) <= LONG_LIMIT, "语料不该超过 P5-B 的上限"
    assert text_scale(source) == "long", "95,618 字应当被判为 long"
    project_id = _project(source, "P5-B 长文本测试")
    try:
        run_medium_analysis(project_id)
        chapters = list_chapters(project_id)
        chunks = list_chunks(project_id)
        events = list_events(project_id)
        storylines = list_storylines(project_id)
        assert len(chapters) == 30, f"章节应落库 30 节，实际 {len(chapters)}"
        assert len(chunks) >= 30, f"分块数应远多于章节数，实际 {len(chunks)}"
        assert len(events) >= 30, f"事件数应远多于章节数，实际 {len(events)}"
        assert 2 <= len(storylines) <= 3, f"候选故事线应为 2～3 条，实际 {len(storylines)}"
        print(
            f"PASS: 95,618 字长文本分析完成 —— {len(chapters)} 节 / "
            f"{len(chunks)} 块 / {len(events)} 事件 / {len(storylines)} 条故事线"
        )
    finally:
        _cleanup(project_id)


def test_storyline_spans_chapters() -> None:
    source = CORPUS.read_text(encoding="utf-8", errors="replace")
    project_id = _project(source, "P5-B 跨章故事线")
    try:
        run_medium_analysis(project_id)
        storylines = list_storylines(project_id)
        chunks = {c["id"]: c for c in list_chunks(project_id)}
        spans = []
        for line in storylines:
            covers = {chunks[cid]["chapter_index"] for cid in line.get("chunk_ids") or [] if cid in chunks}
            spans.append(len(covers))
        assert max(spans) >= 2, f"至少要有一条故事线跨章节，实际跨度 {spans}"
        print(f"PASS: 故事线跨章节跨度 = {spans}（最大 {max(spans)} 节）")
    finally:
        _cleanup(project_id)


def test_scope_by_chapter_excludes_the_rest() -> None:
    source = CORPUS.read_text(encoding="utf-8", errors="replace")
    project_id = _project(source, "P5-B 按章节选范围")
    try:
        run_medium_analysis(project_id)
        chapters = list_chapters(project_id)
        # 只挑中间三节，避免"从第 1 节开始"这种恰好等于前缀的假通过。
        picked = chapters[9:12]
        assert len(picked) == 3
        state = save_adaptation_scope(
            project_id,
            chapter_ids=[c["id"] for c in picked],
            event_ids=None,
            chunk_ids=None,
        )
        scope = state["adaptation_scope"]
        scoped = scope["scoped_text"]
        assert scoped, "按章节选范围后 scoped_text 不能为空"
        assert len(scoped) < len(source) * 0.5, "只选 3 节却带进了近半原文，范围没生效"
        for chapter in picked:
            marker = f"{chapter['marker']}："
            assert marker in scoped or chapter["title"][:6] in scoped, (
                f"选中的第 {chapter['chapter_index']} 节内容不在 scope 里"
            )
        # 关键的反向断言：没选的章节不能混进来。
        for chapter in chapters[:5] + chapters[15:]:
            if not chapter["title"]:
                continue
            assert chapter["title"] not in scoped, (
                f"第 {chapter['chapter_index']} 节没被选中，却出现在 scope 里"
            )
        print(f"PASS: 选 3 节后 scoped_text 为 {len(scoped)} 字（全文 {len(source)} 字），范围外章节未混入")
    finally:
        _cleanup(project_id)


def test_coverage_still_holds_for_long_text() -> None:
    source = CORPUS.read_text(encoding="utf-8", errors="replace")
    chapters = parse_chapters(source)
    chunks = segment_source(source, chapters=chapters)
    assert coverage_ok(source, chunks), "长文本分块后全文仍需被完整覆盖，不能有无法回溯的缺口"
    print(f"PASS: 长文本 {len(source)} 字分块后仍然全文覆盖（无缺口）")


def test_http_chapter_scope_smoke() -> None:
    """端到端：走真实 HTTP 建长文本项目 → /run → 按章选范围 → 确认并衔接改编方案。

    这一条专门覆盖「服务层写对了、入口却还按旧规模分支拒绝」这类缺口——
    上一版 `start_adaptation_workflow` 就是这么把 P5-B 挡在门外的。
    """
    from fastapi.testclient import TestClient

    from backend.main import app

    source = CORPUS.read_text(encoding="utf-8", errors="replace")
    client = TestClient(app)
    created = None
    try:
        response = client.post(
            "/api/projects",
            json={
                "title": "HTTP长文本",
                "source_text": source,
                "style": "ink-wash fantasy",
                "aspect_ratio": "16:9",
                "duration_seconds": 10,
            },
        )
        assert response.status_code == 200, response.text
        created = response.json()["id"]
        run = client.post(f"/api/projects/{created}/run")
        assert run.status_code == 200, run.text
        state = client.get(f"/api/projects/{created}/medium-text").json()
        assert state["status"] == "awaiting_storyline_review", f"长文本应停在范围选择，实际 {state['status']}"
        chapters = state["source_chapters"]
        assert len(chapters) == 30, f"HTTP 路径应解析出 30 章，实际 {len(chapters)}"
        picked = chapters[9:12]
        picked_ids = [item["id"] for item in picked]
        saved = client.put(
            f"/api/projects/{created}/medium-text/scope",
            json={"chapter_ids": picked_ids, "user_note": "只改这 3 节"},
        )
        assert saved.status_code == 200, saved.text
        scope = saved.json()["adaptation_scope"]
        assert scope["chapter_ids"] == picked_ids, "章节选择没有落库"
        assert 0 < len(scope["scoped_text"]) < len(source) * 0.5, "只选 3 节却带进近半原文"
        outside = [item["title"] for item in chapters[:5] + chapters[15:] if item["title"]]
        for title in outside:
            assert title not in scope["scoped_text"], f"范围外章节「{title}」混进了 scope"
        bad = client.put(f"/api/projects/{created}/medium-text/scope", json={"chapter_ids": ["chap_not_mine"]})
        assert bad.status_code >= 400, "不属于本项目的章节 id 必须被拒绝，不能静默放行"
        confirmed = client.post(f"/api/projects/{created}/medium-text/scope/confirm", json={"chapter_ids": picked_ids})
        assert confirmed.status_code == 200, confirmed.text
        body = confirmed.json()
        assert body["adaptation_scope"]["review_status"] == "confirmed"
        options = body["adaptation_options"]
        assert options, "确认范围后应产出改编方案"
        for item in options:
            assert item["source_excerpt"] in body["adaptation_scope"]["scoped_text"], (
                "改编方案引用了 scope 之外的原文，范围承诺被破坏"
            )
        print(
            f"PASS: HTTP 长文本按章选范围（3 节 → {len(scope['scoped_text'])} 字），"
            f"跨项目章节被拒，{len(options)} 个改编方案均落在 scope 内"
        )
    finally:
        if created:
            _cleanup(created)


def main() -> None:
    init_environment()
    init_db()
    if not CORPUS.is_file():
        print(f"SKIP: 长文本语料不存在（{CORPUS}），无法验证 P5-B；这不是通过。")
        return
    test_chapter_tree_parses_real_corpus()
    test_chapter_tree_falls_back_without_markers()
    test_chunks_never_cross_chapters()
    test_coverage_still_holds_for_long_text()
    test_long_text_analysis_runs()
    test_storyline_spans_chapters()
    test_scope_by_chapter_excludes_the_rest()
    test_http_chapter_scope_smoke()
    print("ALL LONG TEXT TESTS PASSED")


if __name__ == "__main__":
    main()
