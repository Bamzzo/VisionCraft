import logging
import os
from typing import Iterable

import chromadb

from ..config import CHROMA_DIR
from ..database import connect
from ..providers.embedding_provider import (
    EmbeddingProvider,
    collection_name_for_provider,
    embed_texts_with_fallback,
    get_embedding_provider,
    known_collection_names,
)


logger = logging.getLogger(__name__)


FTS_TABLE = "source_chunk_fts"
# trigram 分词器的最小匹配长度：短于 3 个字符它**静默**返回 0 条（已实测），
# 所以这类查询不能进 MATCH，必须退回字面重合打分，否则"能查到"会变成"查不到"。
FTS_MIN_QUERY_CHARS = 3
# 字面命中加成：有上限的固定值。FTS 的职责是"找得到"，不是"排得更好"——
# 排序仍由同一套分数决定，跨语料的 bm25 量级不稳定，不适合直接当分数用。
FTS_HIT_BONUS = 0.05
# 字面候选池的规模。字面打分是纯本地 O(块数) 计算，但**进池不只是为了打分**——它要
# 参与最终排序，所以扩进来的必须真是"字面更像"的那些，不能把整个语料倒进来。
LEXICAL_CANDIDATE_MULTIPLIER = 6
LEXICAL_CANDIDATE_MIN = 12


def get_collection(provider: EmbeddingProvider | None = None):
    """取当前 provider 的 collection。

    collection 按 provider 隔离（见 `embedding_provider` 模块开头）：hash 是 384 维、
    语义模型是 1024 维，混存不报错但排序不可解释。这里**不注册 embedding_function**，
    写入与查询都显式传向量 —— 否则 Chroma 会拿它自己的默认模型去算，既不可控又多一次
    模型下载。
    """
    provider = provider or get_embedding_provider()
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_or_create_collection(
        name=collection_name_for_provider(provider),
        metadata={
            "hnsw:space": "cosine",
            "embedding_name": provider.name,
            "embedding_dimension": provider.dimension,
        },
    )


def reset_project_memory(project_id: str, provider: EmbeddingProvider | None = None) -> None:
    collection = get_collection(provider)
    existing = collection.get(where={"project_id": project_id}, include=[])
    ids = existing.get("ids") or []
    if ids:
        collection.delete(ids=ids)


def index_project_memory(project_id: str) -> int:
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

    if not ids:
        return 0
    # 先嵌入、再按**实际使用的** provider 选 collection 与清旧行。顺序不能反：
    # 远端失败会整批降级成 hash，这时如果先清了语义 collection，就会把上一轮语义索引
    # 白删一遍，而新数据其实写进了 hash collection。
    provider, embeddings = embed_texts_with_fallback(get_embedding_provider(), documents)
    collection = get_collection(provider)
    reset_project_memory(project_id, provider)
    collection.add(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
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


def _lexical_candidates(project_id: str, query: str, limit: int) -> list[dict]:
    """对**本项目的全部块**做字面重合打分，取前 `limit` 条（只保留得分 > 0 的）。

    为什么必须全扫 —— 这是一个实测出来的缺陷，不是调优：

    最终分数是 `lexical*0.8 + vector*0.2 + fts_hit*0.05`，字面项占大头；但候选池原先只由
    「向量近邻 `limit*3` 条」+「FTS 命中 `limit*3` 条」拼成，产品走 `limit=2` 时池子只有
    **十几条**（语料有 131 块）。于是**占 0.8 权重的那一项只能在这十几条里挑**，而这几条是
    弱向量随机挑的、加上对无空格中文短语常常一条都不返回的 trigram FTS。

    实测（`tools/eval_retrieval.py`，k=2，16 例）：hybrid 的改写类召回 **0/8**，而同一条
    `_lexical_score` 在全部块上打分能答对 **6/8**（`lexical_only` 口径）。差距不在打分函数，
    **在候选池**——料没进来，分再准也排不出来。

    代价有上界：原文在分析阶段就按章节切块，且本项目明确拒绝 >10 万字，块数是百量级；
    这是一次字符包含统计，不涉及网络与磁盘随机读。

    边界：只覆盖 `source_chunks`（与 FTS 同源）。故事圣经/角色/场景/镜头那些非原文记忆
    暂不做全扫——它们条数少且都在向量池里，留待确有需要时再加。
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT id AS chunk_id, text, chapter_index FROM source_chunks "
            "WHERE project_id = ? ORDER BY chunk_index",
            (project_id,),
        ).fetchall()
    if not rows:
        return []
    scored = sorted(
        ((_lexical_score(query, str(row["text"] or "")), row) for row in rows),
        key=lambda item: item[0],
        reverse=True,
    )
    return [
        {
            "chunk_id": row["chunk_id"],
            "text": str(row["text"] or ""),
            "chapter_index": row["chapter_index"],
        }
        for score, row in scored[: max(1, int(limit))]
        if score > 0
    ]


def search_project_memory(project_id: str, query: str, limit: int = 6) -> list[dict]:
    provider = get_embedding_provider()
    provider, query_embeddings = embed_texts_with_fallback(provider, [query])
    collection = get_collection(provider)
    result = collection.query(
        query_embeddings=query_embeddings,
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
    if not candidates:
        # 当前 collection 空、别的 collection 却有这个项目的行 —— 几乎总是"换了 provider
        # 但没重建索引"。静默返回空会让人以为内容没了，这里必须留一条能查到的告警。
        _warn_if_other_collection_has_project(project_id, collection_name_for_provider(provider))
    # 全文索引补精确召回。向量侧再强也不该放弃这条路：它答的是"字面就在原文里"，
    # 而那类问题不该由近邻搜索去赌。hash provider 下尤其重要——它连字面命中都可能漏。
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
    # 第三条候选来源：字面全扫。上面两条都答不了"同义改写"——弱向量抓不住语义，trigram
    # FTS 对无空格中文短语又常常一条都不返；而同一套字面打分在全部块上本来就答得出
    # （见 `_lexical_candidates` 的实测 6/8 vs 0/8），只是原先没让它看到那些块。
    # 池子不配得上 0.8 的权重，字面这一路就等于没接上。
    chunk_ids = {item["metadata"].get("chunk_id") for item in candidates.values()}
    for row in _lexical_candidates(
        project_id, query, max(limit * LEXICAL_CANDIDATE_MULTIPLIER, LEXICAL_CANDIDATE_MIN)
    ):
        chunk_id = row["chunk_id"]
        if not chunk_id or chunk_id in chunk_ids:
            continue
        metadata = {"project_id": project_id, "kind": "source_text", "label": "", "chunk_id": chunk_id}
        if row.get("chapter_index") is not None:
            metadata["chapter_index"] = int(row["chapter_index"])
        item_id = f"{project_id}:source:{chunk_id}"
        candidates[item_id] = {
            "id": item_id,
            "document": row["text"],
            "metadata": metadata,
            "vector_score": 0.0,
            "fts_hit": False,
        }
        chunk_ids.add(chunk_id)
    lexical_weight, vector_weight = _hybrid_weights(provider)
    items = []
    for item in candidates.values():
        # 权重按 provider 取：hash 只能靠字面重合，语义模型可以让向量说话。
        lexical = _lexical_score(query, item["document"])
        score = (lexical * lexical_weight) + (item["vector_score"] * vector_weight) + (FTS_HIT_BONUS if item["fts_hit"] else 0.0)
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


def _hybrid_weights(provider: EmbeddingProvider) -> tuple[float, float]:
    """字面分与向量分的权重。**两个 provider 用同一套**：0.8 / 0.2。

    这里原本按 provider 分档（hash 0.8/0.2，语义模型 0.3/0.7），理由是"向量强了就该让
    向量说话"。实测把这个直觉否掉了（`tmp/_fusion_lab.py`，同一索引、同一 16 例、k=2）：

    | 字面/向量 | hash | dashscope:text-embedding-v4 |
    |---|---|---|
    | **0.8 / 0.2** | recall 0.8750 / MRR 0.8438 | **recall 0.9375 / MRR 0.8750** |
    | 0.7 / 0.3 | 0.8750 / 0.8438 | 0.9375 / 0.8438 |
    | 0.3 / 0.7 | 0.8125 / 0.8125 | 0.8125 / 0.7812 |

    换成 0.3/0.7 之后反而掉一档：**向量一旦占主导，会把字面完全命中的块挤出 top-2**，
    而后者的相关度是真高。语义该补的是"字面答不出的那些"（改写类 0.75 → 0.88），不是
    去推翻字面已经答对的。

    也试过 RRF（名次融合，不配权重）：dashscope 下很好（0.9375 / MRR 0.9375），
    但 hash 下崩到 0.5000 —— 名次融合给噪声表和有效表**同等权重**，而 hash 是必须
    可用的离线兜底。所以不采用。

    仍可用 `HYBRID_LEXICAL_WEIGHT` / `HYBRID_VECTOR_WEIGHT` 覆盖（调参入口留着）。
    `provider` 参数保留，是为了让"哪天实测出需要分档"时能就地改，不必再改调用点。
    """
    lexical = _float_env("HYBRID_LEXICAL_WEIGHT", 0.8)
    vector = _float_env("HYBRID_VECTOR_WEIGHT", 0.2)
    total = lexical + vector
    if total <= 0:
        return 0.8, 0.2
    return lexical / total, vector / total


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _warn_if_other_collection_has_project(project_id: str, current_collection_name: str) -> None:
    """当前 collection 里查不到这个项目，却在别的 collection 里找到了 —— 记一条 warning。

    这是"换了 embedding provider 但没重建索引"的唯一可见症状。没有它，用户看到的是
    "检索突然全空"，而这看起来像数据丢了。
    """
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    for name in known_collection_names():
        if name == current_collection_name:
            continue
        try:
            collection = client.get_collection(name)
            existing = collection.get(where={"project_id": project_id}, include=[])
        except Exception:  # noqa: BLE001 - collection 不存在是正常情况
            continue
        if existing.get("ids"):
            logger.warning(
                "collection %s 里还有项目 %s 的向量，但当前 collection %s 是空的；"
                "换了 embedding provider 之后需要重建索引。",
                name,
                project_id,
                current_collection_name,
            )
            return
