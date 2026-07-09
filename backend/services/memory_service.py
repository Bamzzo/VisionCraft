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


def get_collection(provider: EmbeddingProvider | None = None):
    provider = provider or get_embedding_provider()
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_or_create_collection(
        name=collection_name_for_provider(provider),
        metadata={"hnsw:space": "cosine", "embedding_name": provider.name, "embedding_dimension": provider.dimension},
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
    for index, chunk in enumerate(_chunk_text(project["source_text"])):
        ids.append(f"{project_id}:source:{index}")
        documents.append(chunk)
        metadatas.append({"project_id": project_id, "kind": "source_text", "label": project["title"]})

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
    provider = get_embedding_provider()
    provider, embeddings = embed_texts_with_fallback(provider, documents)
    collection = get_collection(provider)
    reset_project_memory_for_provider(project_id, provider)
    collection.add(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
    return len(ids)


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
    if not (result.get("ids", [[]])[0]):
        _warn_if_other_collection_has_project(project_id, collection_name_for_provider(provider))
    items = []
    ids = result.get("ids", [[]])[0]
    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    lexical_weight, vector_weight = _hybrid_weights(provider)
    for item_id, document, metadata, distance in zip(ids, documents, metadatas, distances):
        vector_score = max(0.0, 1 - float(distance or 0))
        lexical_score = _lexical_score(query, document)
        # Hash embedding 较轻量，中文短查询需要提高字面重合权重；语义模型可通过配置提高向量权重。
        items.append(
            {
                "id": item_id,
                "document": document,
                "metadata": metadata,
                "score": round((lexical_score * lexical_weight) + (vector_score * vector_weight), 4),
            }
        )
    return sorted(items, key=lambda item: item["score"], reverse=True)[:limit]


def reset_project_memory_for_provider(project_id: str, provider: EmbeddingProvider) -> None:
    collection = get_collection(provider)
    existing = collection.get(where={"project_id": project_id}, include=[])
    ids = existing.get("ids") or []
    if ids:
        collection.delete(ids=ids)


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
    lexical_default, vector_default = (0.3, 0.7) if provider.name.startswith("siliconflow:") else (0.8, 0.2)
    lexical = _float_env("HYBRID_LEXICAL_WEIGHT", lexical_default)
    vector = _float_env("HYBRID_VECTOR_WEIGHT", vector_default)
    total = lexical + vector
    if total <= 0:
        return lexical_default, vector_default
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
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    for name in dict.fromkeys(known_collection_names()):
        if name == current_collection_name:
            continue
        try:
            collection = client.get_collection(name)
            existing = collection.get(where={"project_id": project_id}, include=[])
        except Exception:
            continue
        if existing.get("ids"):
            logger.warning(
                "Memory collection %s has data for project %s, but active collection %s is empty. Rebuild the index.",
                name,
                project_id,
                current_collection_name,
            )
            return
