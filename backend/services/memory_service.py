import hashlib
import math
from typing import Iterable

import chromadb

from ..config import CHROMA_DIR
from ..database import connect


COLLECTION_NAME = "visioncraft_memory"
EMBEDDING_DIM = 384


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
    collection = get_collection()
    collection.add(ids=ids, documents=documents, metadatas=metadatas)
    return len(ids)


def search_project_memory(project_id: str, query: str, limit: int = 6) -> list[dict]:
    collection = get_collection()
    result = collection.query(
        query_texts=[query],
        n_results=max(1, min(limit * 3, 50)),
        where={"project_id": project_id},
        include=["documents", "metadatas", "distances"],
    )
    items = []
    ids = result.get("ids", [[]])[0]
    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    for item_id, document, metadata, distance in zip(ids, documents, metadatas, distances):
        vector_score = max(0.0, 1 - float(distance or 0))
        lexical_score = _lexical_score(query, document)
        # 本地 hash embedding 较轻量，中文短查询需要提高字面重合权重。
        items.append(
            {
                "id": item_id,
                "document": document,
                "metadata": metadata,
                "score": round((lexical_score * 0.8) + (vector_score * 0.2), 4),
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
