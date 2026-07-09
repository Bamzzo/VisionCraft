import hashlib
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol


logger = logging.getLogger(__name__)

HASH_EMBEDDING_DIM = 384
SILICONFLOW_BATCH_SIZE = 32
SILICONFLOW_TIMEOUT = 30

_remote_disabled_reason: str | None = None


class EmbeddingProvider(Protocol):
    name: str
    dimension: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...


@dataclass(frozen=True)
class HashEmbeddingProvider:
    name: str = "hash"
    dimension: int = HASH_EMBEDDING_DIM

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_embed_hash(text) for text in texts]


@dataclass(frozen=True)
class SiliconFlowEmbeddingProvider:
    api_key: str
    base_url: str
    model: str
    name: str
    dimension: int = 1024

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), SILICONFLOW_BATCH_SIZE):
            batch = texts[start : start + SILICONFLOW_BATCH_SIZE]
            embeddings.extend(self._embed_batch(batch))
        return embeddings

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload = {"model": self.model, "input": texts}
        last_error: Exception | None = None
        for attempt in range(2):
            request = urllib.request.Request(
                self.base_url.rstrip("/") + "/embeddings",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=SILICONFLOW_TIMEOUT) as response:
                    body = json.loads(response.read().decode("utf-8"))
                rows = sorted(body.get("data", []), key=lambda item: item.get("index", 0))
                vectors = [row.get("embedding") for row in rows]
                if len(vectors) != len(texts) or any(not isinstance(vector, list) for vector in vectors):
                    raise RuntimeError("SiliconFlow embedding response shape is invalid")
                return vectors
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_error = RuntimeError(f"SiliconFlow embedding HTTP {exc.code}: {detail[:300]}")
            except Exception as exc:  # pragma: no cover - network dependent
                last_error = exc
            if attempt == 0:
                time.sleep(0.8)
        raise RuntimeError(str(last_error or "SiliconFlow embedding failed"))


def get_embedding_provider() -> EmbeddingProvider:
    requested = os.getenv("EMBEDDING_PROVIDER", "hash").strip().lower()
    if requested != "siliconflow":
        return HashEmbeddingProvider()
    if _remote_disabled_reason:
        logger.warning("SiliconFlow embedding disabled for this process: %s", _remote_disabled_reason)
        return HashEmbeddingProvider()
    api_key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        disable_remote_embedding("SILICONFLOW_API_KEY is missing")
        return HashEmbeddingProvider()
    model = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3").strip() or "BAAI/bge-m3"
    return SiliconFlowEmbeddingProvider(
        api_key=api_key,
        base_url=os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"),
        model=model,
        name=f"siliconflow:{model}",
    )


def embed_texts_with_fallback(provider: EmbeddingProvider, texts: list[str]) -> tuple[EmbeddingProvider, list[list[float]]]:
    if not texts:
        return provider, []
    try:
        return provider, provider.embed_texts(texts)
    except Exception as exc:
        if isinstance(provider, SiliconFlowEmbeddingProvider):
            disable_remote_embedding(str(exc))
            fallback = HashEmbeddingProvider()
            return fallback, fallback.embed_texts(texts)
        raise


def disable_remote_embedding(reason: str) -> None:
    global _remote_disabled_reason
    if not _remote_disabled_reason:
        _remote_disabled_reason = reason
        logger.warning("Falling back to hash embedding provider: %s", reason)


def collection_name_for_provider(provider: EmbeddingProvider) -> str:
    if isinstance(provider, SiliconFlowEmbeddingProvider):
        model_name = provider.model.rsplit("/", 1)[-1].lower()
        suffix = re.sub(r"[^a-z0-9-]+", "-", model_name).strip("-") or "model"
        return f"visioncraft_memory_sf_{suffix}"[:63].strip("-_")
    return "visioncraft_memory_hash"


def known_collection_names() -> list[str]:
    model = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3").strip() or "BAAI/bge-m3"
    model_name = model.rsplit("/", 1)[-1].lower()
    suffix = re.sub(r"[^a-z0-9-]+", "-", model_name).strip("-") or "model"
    return ["visioncraft_memory", "visioncraft_memory_hash", f"visioncraft_memory_sf_{suffix}"[:63].strip("-_")]


def _embed_hash(text: str) -> list[float]:
    vector = [0.0] * HASH_EMBEDDING_DIM
    normalized = text.lower()
    grams = [normalized[i : i + 2] for i in range(max(1, len(normalized) - 1))]
    for gram in grams:
        digest = hashlib.blake2b(gram.encode("utf-8", errors="ignore"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "little") % HASH_EMBEDDING_DIM
        sign = 1 if digest[4] % 2 == 0 else -1
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]
