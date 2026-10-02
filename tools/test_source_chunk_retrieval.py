"""无费用测试：章节感知索引 + FTS5 全文索引的混合召回（P5-B-2 的第一层）。

P5-B-2 要补的是**精确召回**，不是语义检索。这一点必须先说清楚，因为它决定了
下面的断言只能证明什么：

- 本地 hash embedding 是字符二元组哈希，对中文短查询很弱。此前候选集完全由它给出，
  再用 `_lexical_score` 按"字符出现在文档里的比例"重新加权——注意那是**字符集合**，
  不看顺序。于是"字面就写在原文里"这种最容易答对的问题，反而可能整片召不回来。
- 这一层加的是 SQLite FTS5 全文索引（trigram 分词），把字面命中的块并进候选集。
  它**不改变**排序口径，只让本来就该被找到的块至少进入排序。

语料取工作区根的 `output/test_texts/蛊真人100000字.txt`（95,618 字 / 30 节）。缺失时整项 SKIP。

下面几条断言都刻意做成"关掉这一层就会失败"的形状：

1. 选定的字面片段（11 字，语料中**恰好**出现在第 15、16 两节各一次）；
2. 这两个块都**不在**向量 top-18 里——即没有 FTS 时，这条查询召回的会是一批
   根本不包含该片段的块（实测如此，不是推论）；
3. 混合后，这两块都进了结果且 `fts_hit=True`，并把"确实包含该片段"写进断言；
4. FTS 的精度是**连续子串**：打乱顺序的同一批字符不得命中（已用最小例子核实），
   因此每个候选文档都必须真的包含该片段，不能只用字面重合打分糊过去；
5. trigram 的 3 字下限不许退化成"查不到"：2 字查询在 FTS 侧返回空是正常的，
   但合并检索必须仍有结果（由向量路径兜住）。
"""
from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import PROJECTS_DIR, init_environment  # noqa: E402
from backend.database import connect, init_db, utc_now  # noqa: E402
from backend.services import memory_service  # noqa: E402
from backend.services.medium_text_service import run_medium_analysis  # noqa: E402
from backend.services.project_service import delete_project  # noqa: E402

CORPUS = ROOT.parent / "output" / "test_texts" / "蛊真人100000字.txt"
# 语料中恰好出现两次（第 15、16 节各一次），跨章节，且长到不会被向量路径顺带召回。
LITERAL = "你已经中了我的独门毒蛊"
VECTOR_TOP_N = 18  # 就是 search_project_memory 里 limit*3（limit=6）的候选宽度


def _project(source: str, title: str) -> str:
    project_id = f"p5b2_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode,
             status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, source, "ink-wash fantasy", "16:9", 45, "auto", "created", "direct", now, now),
        )
    return project_id


def _cleanup(project_id: str) -> None:
    delete_project(project_id)
    shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)


def _chunks(project_id: str) -> list[dict]:
    with connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """SELECT id, chunk_index, text, start_offset, end_offset, chapter_index
                FROM source_chunks WHERE project_id = ? ORDER BY chunk_index""",
                (project_id,),
            ).fetchall()
        ]


def _vector_top(project_id: str, query: str, n: int = VECTOR_TOP_N) -> list[str]:
    """**绕过合并逻辑**直取向量候选，用来证明"没有 FTS 会漏召"。

    向量必须显式算：collection 现在按 provider 隔离、**不注册 embedding_function**
    （见 `backend/providers/embedding_provider`）。继续用 `query_texts=` 会让 Chroma
    去要它自己的默认模型，那既不可控、也不是这条断言想测的东西。
    """
    from backend.providers.embedding_provider import get_embedding_provider

    provider = get_embedding_provider()
    result = memory_service.get_collection(provider).query(
        query_embeddings=provider.embed_texts([query]),
        n_results=n,
        where={"project_id": project_id},
        include=["metadatas"],
    )
    return [meta.get("chunk_id") for meta in result.get("metadatas", [[]])[0]]


def test_fts_table_exists() -> None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'source_chunk_fts'"
        ).fetchone()
    assert row, "初始化后应存在 source_chunk_fts 全文索引表"
    assert "trigram" in (row["sql"] or ""), f"索引必须用 trigram 分词（中文没有词间空格）：{row['sql']}"
    print("PASS: source_chunk_fts 已建立，分词器为 trigram")


def test_index_is_chapter_aware_and_scoped() -> None:
    source = CORPUS.read_text(encoding="utf-8", errors="replace")
    # B 只取前 40,000 字：LITERAL 的两次出现都在 45,798 之后，所以 B 里根本没有它。
    # 这样"跨项目隔离"才有一个**有内容的**判据，而不是"两边都查不到"的空断言。
    other_source = source[:40000]
    assert LITERAL not in other_source, "隔离用例的前提不成立：B 里不该出现该片段"
    project_id = _project(source, "P5-B-2 章节感知索引")
    other_id = _project(other_source, "P5-B-2 旁项目")
    try:
        run_medium_analysis(project_id)
        chunks = _chunks(project_id)
        assert len(chunks) >= 30, f"长文本应有大量章节块，实际 {len(chunks)}"

        # 分析一结束索引就该是新鲜的：不靠外面再点一次"建索引"。
        fresh = memory_service._fts_candidates(project_id, LITERAL, 10)
        assert fresh, "分析完成后全文索引就应可用，不该等额外的一次建索引调用"

        indexed = memory_service.index_project_memory(project_id)
        assert indexed >= len(chunks), f"原文块都该进向量索引：索引 {indexed} < 块 {len(chunks)}"
        with connect() as conn:
            own = conn.execute(
                "SELECT COUNT(*) AS n FROM source_chunk_fts WHERE project_id = ?", (project_id,)
            ).fetchone()["n"]
            chunk_rows = conn.execute(
                "SELECT COUNT(*) AS n FROM source_chunk_fts", ()
            ).fetchone()["n"]
        assert own == len(chunks), f"全文索引行数应等于本项目块数：{own} != {len(chunks)}"
        assert chunk_rows == own, f"此时只有这一个项目建过索引，全表 {chunk_rows} 行却该只有 {own} 行"

        run_medium_analysis(other_id)
        other_chunks = _chunks(other_id)
        assert other_chunks, "B 也应有自己的章节块"
        with connect() as conn:
            other_rows = conn.execute(
                "SELECT COUNT(*) AS n FROM source_chunk_fts WHERE project_id = ?", (other_id,)
            ).fetchone()["n"]
        assert other_rows == len(other_chunks), "每个项目的索引行应与它自己的块数相等"
        # 真正的隔离判据：A 里查得到的片段，在 B 里必须查不到。
        assert memory_service._fts_candidates(project_id, LITERAL, 10), "A 里应当查得到"
        assert memory_service._fts_candidates(other_id, LITERAL, 10) == [], "B 里没有该片段，却查出了东西"
        print(
            f"PASS: 全文索引 {own} 行 = 本项目 {len(chunks)} 块（分析后即新鲜）；"
            f"旁项目 {other_rows} 行各归其位，A 的片段在 B 里查不到"
        )
    finally:
        _cleanup(project_id)
        _cleanup(other_id)


def test_literal_recall_is_precise_and_attributed() -> None:
    source = CORPUS.read_text(encoding="utf-8", errors="replace")
    assert source.count(LITERAL) == 2, f"语料里这个片段应恰好出现 2 次，实际 {source.count(LITERAL)}"
    project_id = _project(source, "P5-B-2 字面召回")
    try:
        run_medium_analysis(project_id)
        memory_service.index_project_memory(project_id)
        chunks = _chunks(project_id)
        owners = [chunk for chunk in chunks if LITERAL in chunk["text"]]
        assert len(owners) == 2, f"应有两块含该片段，实际 {len(owners)}"

        candidates = memory_service._fts_candidates(project_id, LITERAL, 50)
        found = {row["chunk_id"] for row in candidates}
        for owner in owners:
            assert owner["id"] in found, f"字面索引没有召回含该片段的块 {owner['id']}"
        # 精度：FTS 是连续子串匹配，因此每个候选都必须真的包含该片段。
        for row in candidates:
            assert LITERAL in row["text"], f"候选 {row['chunk_id']} 并不包含该片段，精度不成立"

        # 章节归属：块上记的章节号必须与"哪个章节覆盖了这段偏移"一致。
        with connect() as conn:
            chapters = [
                dict(item)
                for item in conn.execute(
                    "SELECT chapter_index, start_offset, end_offset FROM source_chapters WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
            ]
        by_offset = {chunk["id"]: chunk for chunk in chunks}
        for row in candidates:
            chunk = by_offset[row["chunk_id"]]
            owner_chapter = [
                chapter
                for chapter in chapters
                if chapter["start_offset"] <= chunk["start_offset"] < chapter["end_offset"]
            ]
            assert owner_chapter, f"块 {chunk['id']} 不属于任何章节"
            assert int(row["chapter_index"]) == int(owner_chapter[0]["chapter_index"]), (
                f"块 {chunk['id']} 的章节归属与偏移不符："
                f"记为 {row['chapter_index']}，偏移落在 {owner_chapter[0]['chapter_index']}"
            )
        chapters_hit = sorted({int(row["chapter_index"]) for row in candidates})
        print(f"PASS: 字面索引精确召回 {len(candidates)} 块，全部真的包含该片段，章节归属为 {chapters_hit}")
    finally:
        _cleanup(project_id)


def test_fts_recovers_what_the_vector_path_misses() -> None:
    """核心一条：没有这一层就会漏召，且漏掉的是唯一正确答案。

    ⚠️ 这条断言的**前提**是"向量侧是弱的 hash"。一旦 `EMBEDDING_PROVIDER` 换成真语义
    模型，向量路径多半自己就召回了 —— 下面那句 `raise` 就是为这种情形准备的：
    断言会失去分辨力，与其静默通过不如炸掉。所以这里**显式钉在 hash 上**，
    测的是"FTS 相对弱向量的增益"，而不是"当前配置下碰巧怎样"。
    """
    os.environ["EMBEDDING_PROVIDER"] = "hash"
    source = CORPUS.read_text(encoding="utf-8", errors="replace")
    project_id = _project(source, "P5-B-2 召回增益")
    try:
        run_medium_analysis(project_id)
        memory_service.index_project_memory(project_id)
        owners = [chunk for chunk in _chunks(project_id) if LITERAL in chunk["text"]]
        owner_ids = {chunk["id"] for chunk in owners}
        assert len(owner_ids) == 2

        vector_ids = _vector_top(project_id, LITERAL)
        still_there = owner_ids & set(vector_ids)
        if still_there:
            raise AssertionError(
                f"向量路径这次已经能召回 {sorted(still_there)}——本条断言失去分辨力。"
                f"请换一个更长/更生僻的字面片段，别把这条留成永远通过的空壳。"
            )

        merged = memory_service.search_project_memory(project_id, LITERAL, 6)
        merged_ids = {item["metadata"].get("chunk_id") for item in merged}
        for owner_id in owner_ids:
            assert owner_id in merged_ids, f"合并检索仍没召回 {owner_id}"
        top2 = merged[:2]
        for item in top2:
            assert item["fts_hit"] is True, f"排在前面的字面命中没有标记 fts_hit：{item['id']}"
            assert LITERAL in item["document"], "排在前面的结果并不包含查询片段"
        # 反向对照：合并结果里也有 fts=False 的项，说明这不是"把所有块都返回"了事。
        assert any(item["fts_hit"] is False for item in merged), "合并检索把全部候选都标成字面命中了"
        print(
            f"PASS: 向量 top-{len(vector_ids)} 完全没有召回含该片段的块，"
            f"FTS 补齐后这两块排到第 1、2 位且含该片段"
        )
    finally:
        _cleanup(project_id)


def test_short_query_floor_does_not_silently_empty_results() -> None:
    source = CORPUS.read_text(encoding="utf-8", errors="replace")
    project_id = _project(source, "P5-B-2 短查询下限")
    try:
        run_medium_analysis(project_id)
        memory_service.index_project_memory(project_id)
        short = "方源"
        assert len(short) < memory_service.FTS_MIN_QUERY_CHARS
        assert memory_service._fts_candidates(project_id, short, 10) == [], (
            "trigram 对短于 3 字的查询静默返回 0 条，这里必须是空——这正是要防的那个坑"
        )
        merged = memory_service.search_project_memory(project_id, short, 6)
        assert merged, "短查询在 FTS 侧无解，但合并检索必须仍有结果（由向量路径兜住），不能变成查不到"
        print(f"PASS: 2 字查询在全文索引侧为空（trigram 下限），合并检索仍有 {len(merged)} 条结果")
    finally:
        _cleanup(project_id)


def test_adversarial_queries_do_not_raise() -> None:
    """查询串是用户输入，必须整体当字面量，不能被当成 FTS5 语法。"""
    source = CORPUS.read_text(encoding="utf-8", errors="replace")
    project_id = _project(source, "P5-B-2 敌意输入")
    try:
        run_medium_analysis(project_id)
        memory_service.index_project_memory(project_id)
        # `x"y` 会让裸 MATCH 抛 unterminated string；`方源*` / `方源 OR 春秋` 在
        # trigram 下既不解析成通配也不解析成布尔（实测都返回 0 条）。短语化后三者都安全。
        for bad in ['x"y', '方源*', "方源 OR 春秋", "NEAR(方源 山)", "***", '"']:
            items = memory_service.search_project_memory(project_id, bad, 3)
            assert isinstance(items, list), f"{bad!r} 的返回不是列表"
        print("PASS: 引号/通配/布尔/NEAR 等输入都不抛异常，作为字面短语处理")
    finally:
        _cleanup(project_id)


def test_index_never_returns_deleted_chunks() -> None:
    """索引与内容不同源是这类功能的经典缺陷：重跑分析会整批删块，索引会留悬空行。"""
    source = CORPUS.read_text(encoding="utf-8", errors="replace")
    project_id = _project(source, "P5-B-2 悬空行守卫")
    try:
        run_medium_analysis(project_id)
        memory_service.index_project_memory(project_id)
        owners = [chunk for chunk in _chunks(project_id) if LITERAL in chunk["text"]]
        victim = owners[0]
        assert memory_service._fts_candidates(project_id, LITERAL, 50)
        with connect() as conn:
            conn.execute("DELETE FROM source_chunks WHERE id = ?", (victim["id"],))
        after = {row["chunk_id"] for row in memory_service._fts_candidates(project_id, LITERAL, 50)}
        assert victim["id"] not in after, "块已被删除，检索却还能返回它——索引漂移泄漏了"
        print("PASS: 块被删除后，检索不再返回它（悬空行被联表守卫挡掉）")
    finally:
        _cleanup(project_id)


def _vector_count(project_id: str) -> int:
    """项目在**所有** collection 里的向量总数 —— 换过 provider 就会有多个 collection。"""
    import chromadb

    from backend.config import CHROMA_DIR

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    total = 0
    for collection in client.list_collections():
        try:
            got = collection.get(where={"project_id": project_id}, include=[])
        except Exception:  # noqa: BLE001 - 某个 collection 坏了不该让计数失败
            continue
        total += len(got.get("ids") or [])
    return total


def test_delete_project_purges_fts_rows_and_vectors() -> None:
    """删项目必须把**派生的索引**一起收掉。两条泄漏都是"查询侧兜得住"型的：

    - `source_chunk_fts` 是 FTS5 **虚表**，不参与外键级联 —— 删 `projects` 行不会带走它，
      而 `_fts_candidates` 的 JOIN 会把悬空行挡掉，所以功能上一直看不出来；
    - 向量在 Chroma 里，检索靠 `where={"project_id": ...}` 过滤，别的项目也看不见它。

    实测代价（开发机）：`source_chunks` 只剩 6 行时 `source_chunk_fts` 积了 2675 行悬空行，
    Chroma 里 2621 条向量**全部**来自已删项目（52 MB 只有垃圾）。
    """
    project_id = _project(CORPUS.read_text(encoding="utf-8", errors="replace"), "P5-B-2 删除清理")
    try:
        run_medium_analysis(project_id)
        memory_service.index_project_memory(project_id)
        with connect() as conn:
            fts_rows = conn.execute(
                "SELECT COUNT(*) AS n FROM source_chunk_fts WHERE project_id = ?", (project_id,)
            ).fetchone()["n"]
        vectors = _vector_count(project_id)
        assert fts_rows > 0, f"建完索引却没有全文索引行（{fts_rows}），这条用例就证明不了删除清理"
        assert vectors > 0, f"建完索引却没有向量（{vectors} 条），这条用例就证明不了删除清理"

        delete_project(project_id)
        with connect() as conn:
            fts_after = conn.execute(
                "SELECT COUNT(*) AS n FROM source_chunk_fts WHERE project_id = ?", (project_id,)
            ).fetchone()["n"]
        vectors_after = _vector_count(project_id)
        assert fts_after == 0, f"删项目后全文索引仍留 {fts_after} 行悬空行"
        assert vectors_after == 0, f"删项目后 Chroma 仍留 {vectors_after} 条孤儿向量"
        print(f"PASS: 删项目同时收掉派生索引（FTS {fts_rows}→0 行、向量 {vectors}→0 条）")
    finally:
        _cleanup(project_id)


def test_short_text_projects_are_unaffected() -> None:
    """短文本没有章节块：全文索引应空着，检索走原路，行为不变。"""
    project_id = _project("春秋蝉鸣少年归，白凝冰在雪原上。方源走在山路上。", "P5-B-2 短文本")
    try:
        indexed = memory_service.index_project_memory(project_id)
        assert indexed > 0, "短文本仍应有原文索引（退回等长硬切）"
        with connect() as conn:
            fts_rows = conn.execute(
                "SELECT COUNT(*) AS n FROM source_chunk_fts WHERE project_id = ?", (project_id,)
            ).fetchone()["n"]
            chunk_rows = conn.execute(
                "SELECT COUNT(*) AS n FROM source_chunks WHERE project_id = ?", (project_id,)
            ).fetchone()["n"]
        assert chunk_rows == 0, "这个项目不该有章节块"
        assert fts_rows == 0, f"没有章节块就没有全文索引行，实际 {fts_rows} 行"
        items = memory_service.search_project_memory(project_id, "白凝冰", 3)
        assert items, "短文本检索必须照常工作"
        print(f"PASS: 短文本项目索引 {indexed} 条、全文索引为空，检索行为不变")
    finally:
        _cleanup(project_id)


def test_http_memory_endpoints_smoke() -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    source = CORPUS.read_text(encoding="utf-8", errors="replace")
    client = TestClient(app)
    created = None
    try:
        response = client.post(
            "/api/projects",
            json={
                "title": "HTTP全文检索",
                "source_text": source,
                "style": "ink-wash fantasy",
                "aspect_ratio": "16:9",
                "duration_seconds": 10,
            },
        )
        assert response.status_code == 200, response.text
        created = response.json()["id"]
        assert client.post(f"/api/projects/{created}/run").status_code == 200
        indexed = client.post(f"/api/projects/{created}/memory/index")
        assert indexed.status_code == 200, indexed.text
        assert indexed.json()["indexed"] > 0

        found = client.get(f"/api/projects/{created}/memory/search", params={"q": LITERAL, "limit": 5})
        assert found.status_code == 200, found.text
        items = found.json()["items"]
        assert items, "按字面片段检索应至少命中一块"
        assert LITERAL in items[0]["document"], "排第一的结果应当真的包含该片段"

        for bad in ['x"y', "方源*", "方源 OR 春秋"]:
            resp = client.get(f"/api/projects/{created}/memory/search", params={"q": bad})
            assert resp.status_code == 200, f"{bad!r} 让检索端点返回了 {resp.status_code}"
        print(f"PASS: HTTP 记忆索引/检索端点工作正常（命中 {len(items)} 条，敌意输入不 5xx）")
    finally:
        if created:
            _cleanup(created)


def main() -> None:
    init_environment()
    init_db()
    if not CORPUS.is_file():
        print(f"SKIP: 长文本语料不存在（{CORPUS}），无法验证第四章索引；这不是通过。")
        return
    test_fts_table_exists()
    test_index_is_chapter_aware_and_scoped()
    test_literal_recall_is_precise_and_attributed()
    test_fts_recovers_what_the_vector_path_misses()
    test_short_query_floor_does_not_silently_empty_results()
    test_adversarial_queries_do_not_raise()
    test_index_never_returns_deleted_chunks()
    test_delete_project_purges_fts_rows_and_vectors()
    test_short_text_projects_are_unaffected()
    test_http_memory_endpoints_smoke()
    print("ALL SOURCE CHUNK RETRIEVAL TESTS PASSED")


if __name__ == "__main__":
    main()
