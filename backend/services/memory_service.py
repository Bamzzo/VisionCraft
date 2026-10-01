import hashlib
import math
from typing import Iterable

import chromadb

from ..config import CHROMA_DIR
from ..database import connect


COLLECTION_NAME = "visioncraft_memory"
EMBEDDING_DIM = 384
FTS_TABLE = "source_chunk_fts"
# trigram 分词器的最小匹配长度：短于 3 个字符它**静默**返回 0 条（已实测），
# 所以这类查询不能进 MATCH，必须退回字面重合打分，否则"能查到"会变成"查不到"。
FTS_MIN_QUERY_CHARS = 3
# 字面命中加成：有上限的固定值。FTS 的职责是"找得到"，不是"排得更好"——
# 排序仍由同一套分数决定，跨语料的 bm25 量级不稳定，不适合直接当分数用。
FTS_HIT_BONUS = 0.05


class HashEmbeddingFunction:
    def name(self) -> str:
        return "visioncraft_hash_embedding"

    def __call__(self, input: list[str]) -> list[list[float]]:  # Chroma expects the parameter name `input`.
        return [_embed_text(text) for text in input]

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self(input)

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return self(input)


def get_collection():
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=HashEmbeddingFunction(),
        metadata={"hnsw:space": "cosine"},
    )


def reset_project_memory(project_id: str) -> None:
    collection = get_collection()
    existing = collection.get(where={"project_id": project_id}, include=[])
    ids = existing.get("ids") or []
    if ids:
        collection.delete(ids=ids)


def index_project_memory(project_id: str) -> int:
    reset_project_memory(project_id)
    with connect() as conn:
        project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        bible = conn.execute("SELECT * FROM story_bibles WHERE project_id = ?", (project_id,)).fetchone()
        characters = conn.execute("SELECT * FROM characters WHERE project_id = ?", (project_id,)).fetchall()
        scenes = conn.execute("SELECT * FROM scenes WHERE project_id = ?", (project_id,)).fetchall()
        shots = conn.execute("SELECT * FROM shots WHERE project_id = ? ORDER BY shot_index", (project_id,)).fetchall()
        assets = conn.execute("SELECT * FROM assets WHERE project_id = ?", (project_id,)).fetchall()
    if not project:
        return 0

    documents: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []

    # 记忆索引同时保存原文和生成资产，后续镜头可检索剧情事实和视觉锚点。
    for index, chunk in enumerate(_source_text_units(project_id, project["source_text"])):
        ids.append(f"{project_id}:source:{chunk['key']}")
        documents.append(chunk["text"])
        metadata = {"project_id": project_id, "kind": "source_text", "label": project["title"]}
        if chunk.get("chapter_index") is not None:
            # 长文本的检索单位必须是**章节内的块**。硬切 900/120 会跨章节边界，
            # 检索回来的证据落在用户没选的章节里，和"按章节圈定范围"直接打架。
            metadata["chapter_index"] = int(chunk["chapter_index"])
        if chunk.get("chapter_title"):
            metadata["chapter_title"] = chunk["chapter_title"]
        if chunk.get("chunk_id"):
            metadata["chunk_id"] = chunk["chunk_id"]
        metadatas.append(metadata)

    if bible:
        ids.append(f"{project_id}:story_bible")
        documents.append(f"{bible['summary']}\n{bible['worldview']}")
        metadatas.append({"project_id": project_id, "kind": "story_bible", "label": "故事圣经"})

    for row in characters:
        ids.append(f"{project_id}:character:{row['id']}")
        documents.append(f"{row['name']} {row['role']} {row['description']} {row['visual_prompt']}")
        metadatas.append({"project_id": project_id, "kind": "character", "label": row["name"]})

    for row in scenes:
        ids.append(f"{project_id}:scene:{row['id']}")
        documents.append(f"{row['name']} {row['description']} {row['visual_prompt']}")
        metadatas.append({"project_id": project_id, "kind": "scene", "label": row["name"]})

    for row in shots:
        ids.append(f"{project_id}:shot:{row['id']}")
        documents.append(f"{row['title']} {row['description']} {row['visual_prompt']} {row['audio_prompt']}")
        metadatas.append({"project_id": project_id, "kind": "shot", "label": row["title"]})

    for row in assets:
        ids.append(f"{project_id}:asset:{row['id']}")
        documents.append(f"{row['name']} {row['type']} {row['description']} {row['prompt']}")
        metadatas.append({"project_id": project_id, "kind": f"asset:{row['type']}", "label": row["name"], "file_path": row["file_path"]})

    collection = get_collection()
    if ids:
        collection.add(ids=ids, documents=documents, metadatas=metadatas)
    # 全文索引在这里一并重建：向量索引与全文索引同源，是这套检索唯一站得住的保证。
    # 返回值仍是写入向量的条数（端点的既有契约），全量条数只做记录。
    index_source_chunk_fts(project_id)
    return len(ids)


def index_source_chunk_fts(project_id: str) -> int:
    """按 `source_chunks` 重建本项目的全文索引行，返回行数。

    为什么必须重建而不是增量补：重跑分析会整批 `DELETE FROM source_chunks`
    再插入新 id，索引里于是留下指向已删块的悬空行。重建 + 检索侧 JOIN 内容表
    两道一起上，才谈得上"索引与内容同源"。
    """
    with connect() as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (FTS_TABLE,)
        ).fetchone():
            return 0
        conn.execute(f"DELETE FROM {FTS_TABLE} WHERE project_id = ?", (project_id,))
        conn.execute(
            f"""
            INSERT INTO {FTS_TABLE}(project_id, chunk_id, text)
            SELECT project_id, id, text FROM source_chunks WHERE project_id = ?
            """,
            (project_id,),
        )
        return int(
            conn.execute(
                f"SELECT COUNT(*) AS n FROM {FTS_TABLE} WHERE project_id = ?", (project_id,)
            ).fetchone()["n"]
        )


def _source_text_units(project_id: str, source_text: str) -> list[dict]:
    """原文的检索单位。

    有 `source_chunks` 就用它：这些块按章节切好、不跨章，检索到的证据能明确归属到
    某一章，不会出现"用户只选了第 10 节，证据却来自第 11 节"。
    没有（短文本、或还没跑过分析）就退回等长硬切——短文本本无章节结构，硬切够用。
    """
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, text, chunk_index, chapter_index FROM source_chunks
            WHERE project_id = ? ORDER BY chunk_index
            """,
            (project_id,),
        ).fetchall()
        titles = {
            int(chapter["chapter_index"]): f"{chapter['marker'] or ''}{chapter['title'] or ''}".strip()
            for chapter in conn.execute(
                "SELECT chapter_index, marker, title FROM source_chapters WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        }
    if rows:
        units = []
        for row in rows:
            text = str(row["text"] or "")
            if not text.strip():
                continue
            index = row["chapter_index"]
            units.append(
                {
                    "key": row["id"],
                    "chunk_id": row["id"],
                    "text": text,
                    "chapter_index": index,
                    "chapter_title": titles.get(int(index), "") if index is not None else "",
                }
            )
        return units
    return [{"key": str(index), "text": chunk} for index, chunk in enumerate(_chunk_text(source_text))]


def _fts_phrase(text: str) -> str:
    """把一段用户输入整体当字面短语交给 MATCH（引号双写转义）。

    这不是洁癖，是实测：裸引号（`x"y`）会让 FTS5 抛 `unterminated string`，
    而 `方源*`、`方源 OR 春秋` 在 trigram 下既不是通配也不是布尔（都返回 0 条）。
    统一短语化之后，用户输什么就查什么——不崩，也不会被悄悄解析成另一种查询。
    """
    return '"' + text.replace('"', '""') + '"'


def _fts_match_expression(query: str) -> str | None:
    """把查询切成空格分隔的短语，用 OR 串起来；没有够长的片段就返回 None。

    短语必须 ≥3 个字符：trigram 对更短的串静默返回 0 条。返回 None 表示
    "全文索引答不了这个问题"，由调用方退回字面重合打分，**不是**"没有匹配"。
    """
    parts = [part for part in str(query).split() if len(part) >= FTS_MIN_QUERY_CHARS]
    if not parts:
        return None
    return " OR ".join(_fts_phrase(part) for part in parts)


def _fts_candidates(project_id: str, query: str, limit: int) -> list[dict]:
    """全文索引命中的块，按 bm25（越小越相关）排序。

    JOIN `source_chunks` 是刻意的第二道守卫：即使某次忘了重建索引，
    悬空行也会在这里被挡掉，"检索到已经不存在的块"不会泄漏给调用方。
    """
    expression = _fts_match_expression(query)
    if not expression:
        return []
    with connect() as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (FTS_TABLE,)
        ).fetchone():
            return []
        rows = conn.execute(
            f"""
            SELECT c.id AS chunk_id, c.text AS text, c.chunk_index AS chunk_index,
                   c.chapter_index AS chapter_index, bm25({FTS_TABLE}) AS rank
            FROM {FTS_TABLE}
            JOIN source_chunks c ON c.id = {FTS_TABLE}.chunk_id
            WHERE {FTS_TABLE} MATCH ? AND {FTS_TABLE}.project_id = ?
            ORDER BY rank
            LIMIT ?
            """,
            (expression, project_id, max(1, int(limit))),
        ).fetchall()
    return [dict(row) for row in rows]


def search_project_memory(project_id: str, query: str, limit: int = 6) -> list[dict]:
    collection = get_collection()
    result = collection.query(
        query_texts=[query],
        n_results=max(1, min(limit * 3, 50)),
        where={"project_id": project_id},
        include=["documents", "metadatas", "distances"],
    )
    candidates: dict[str, dict] = {}
    ids = result.get("ids", [[]])[0]
    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    for item_id, document, metadata, distance in zip(ids, documents, metadatas, distances):
        candidates[item_id] = {
            "id": item_id,
            "document": document,
            "metadata": metadata or {},
            "vector_score": max(0.0, 1 - float(distance or 0)),
            "fts_hit": False,
        }
    # 全文索引补精确召回。本地 hash embedding 对中文短查询太弱，只靠它 + 字面重合
    # 加权时，一个就写在原文里的名字也可能进不了候选；这里把字面命中的块并进候选集，
    # 让它至少有机会被排序。
    chunk_ids = {item["metadata"].get("chunk_id") for item in candidates.values()}
    for row in _fts_candidates(project_id, query, limit * 3):
        chunk_id = row["chunk_id"]
        if chunk_id in chunk_ids:
            for item in candidates.values():
                if item["metadata"].get("chunk_id") == chunk_id:
                    item["fts_hit"] = True
                    break
            continue
        item_id = f"{project_id}:source:{chunk_id}"
        metadata = {"project_id": project_id, "kind": "source_text", "label": "", "chunk_id": chunk_id}
        if row.get("chapter_index") is not None:
            metadata["chapter_index"] = int(row["chapter_index"])
        candidates[item_id] = {
            "id": item_id,
            "document": row["text"],
            "metadata": metadata,
            "vector_score": 0.0,
            "fts_hit": True,
        }
    items = []
    for item in candidates.values():
        # 本地 hash embedding 较轻量，中文短查询需要提高字面重合权重。
        lexical = _lexical_score(query, item["document"])
        score = (lexical * 0.8) + (item["vector_score"] * 0.2) + (FTS_HIT_BONUS if item["fts_hit"] else 0.0)
        items.append(
            {
                "id": item["id"],
                "document": item["document"],
                "metadata": item["metadata"],
                "score": round(score, 4),
                "fts_hit": item["fts_hit"],
            }
        )
    return sorted(items, key=lambda item: item["score"], reverse=True)[:limit]


def build_shot_evidence(project_id: str, title: str, description: str, limit: int = 2) -> list[dict]:
    query = f"{title} {description}".strip()
    if not query:
        return []
    items = search_project_memory(project_id, query, max(limit * 2, 4))
    preferred = []
    for item in items:
        kind = (item.get("metadata") or {}).get("kind", "")
        # 前端展示的证据优先选择文本、故事圣经、角色和场景，少展示视频原始资产。
        if kind in {"source_text", "story_bible", "scene", "character"}:
            preferred.append(
                {
                    "kind": kind,
                    "label": (item.get("metadata") or {}).get("label", kind),
                    "score": item.get("score", 0),
                    "excerpt": _compact_excerpt(item.get("document", "")),
                }
            )
        if len(preferred) >= limit:
            break
    return preferred


def _chunk_text(text: str, size: int = 900, overlap: int = 120) -> Iterable[str]:
    compact = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if not compact:
        return []
    chunks = []
    step = max(1, size - overlap)
    for start in range(0, len(compact), step):
        chunk = compact[start : start + size]
        if chunk:
            chunks.append(chunk)
    return chunks


def _embed_text(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    normalized = text.lower()
    grams = [normalized[i : i + 2] for i in range(max(1, len(normalized) - 1))]
    for gram in grams:
        digest = hashlib.blake2b(gram.encode("utf-8", errors="ignore"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "little") % EMBEDDING_DIM
        sign = 1 if digest[4] % 2 == 0 else -1
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _lexical_score(query: str, document: str) -> float:
    query_chars = {char for char in query.lower() if not char.isspace()}
    if not query_chars:
        return 0.0
    doc = document.lower()
    hits = sum(1 for char in query_chars if char in doc)
    return hits / len(query_chars)


def _compact_excerpt(text: str, limit: int = 140) -> str:
    compact = " ".join(str(text).split())
    return compact[:limit]
