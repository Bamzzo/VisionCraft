"""Embedding provider 抽象：把「向量从哪来」从检索逻辑里摘出来。

为什么需要这一层（两条约束，缺一条都不能上线）：

1. **维度不同不能混存。** 本地 hash 是 384 维，`text-embedding-v4` 默认 1024 维。
   把两种维度的向量写进同一个 Chroma collection，检索结果没有任何意义 —— 而且它是
   **静默**的：不报错，只是排序变得不可解释。所以 collection 按 provider 命名，
   各存各的。副作用正好是想要的：切回 hash 不需要清理任何东西，切回来也不需要。

2. **embedding 失败不能打断工作流。** 索引发生在 LangGraph 工作流内部
   （`langgraph_workflow` 里两次 `index_project_memory`）。远端 embedding 一挂就整条
   流程失败，代价远大于"这一轮召回差一点"。所以远端失败时**进程内降级**到 hash，
   记 warning；并且**写入与查询都用降级后那一个 provider 的 collection**，
   维度不会串。降级是粘性的（本进程内不再重试远端），免得每个镜头都白等一次超时。

默认 provider 是 `hash`：零费用、零外部依赖、离线可用。
要真语义（同义/改写/指代召回）时设 `EMBEDDING_PROVIDER=dashscope`。
两边的实测对比见 `tools/eval_retrieval.py`。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol


logger = logging.getLogger(__name__)

HASH_EMBEDDING_DIM = 384
# text-embedding-v4 的批次上限是 10（官方规格表：批次大小 10，单条 ≤ 8192 token）。
# 写 32 会直接被服务端拒，所以这个数不是调优，是硬约束。
DASHSCOPE_BATCH_SIZE = 10
DASHSCOPE_TIMEOUT_SECONDS = 30

# 远端不可用的原因。一旦记下就不再重试远端 —— 见模块开头第 2 条。
_remote_disabled_reason: str | None = None


class EmbeddingProvider(Protocol):
    name: str
    dimension: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...


@dataclass(frozen=True)
class HashEmbeddingProvider:
    """零依赖本地向量：字符二元组哈希 + blake2b → 384 维有符号计数，L2 归一。

    它**不是语义模型**。同义、改写、指代一概召不回，只是让"字面有重合"的文本在
    向量空间里有点相似度。作为兜底与离线默认值够用，别拿它当语义检索宣传。
    """

    name: str = "hash"
    dimension: int = HASH_EMBEDDING_DIM

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_embed_hash(text) for text in texts]


@dataclass
class DashScopeEmbeddingProvider:
    """阿里云百炼 text-embedding-v4（OpenAI 兼容 `/embeddings`）。

    host 走配置而不是写死：本机的 `DASHSCOPE_API_HOST` 指向一个**专属 MaaS 实例**
    （`llm-*.cn-beijing.maas.aliyuncs.com`），不是公共 `dashscope.aliyuncs.com`。
    路径也可能随实例不同，所以 host 与 path 都可配。
    """

    api_key: str
    base_url: str
    model: str = "text-embedding-v4"
    dimension: int = 1024
    path: str = "/compatible-mode/v1/embeddings"
    name: str = ""
    # 最后一次调用的用量，供评测脚本报实际消耗（token 数由服务端给，不自己估）。
    last_usage: dict = field(default_factory=dict)
    # 累计用量：一次 `embed_texts` 会切成多批，`last_usage` 只留最后一批，
    # 拿它当"本轮花了多少"会低估。记账要的是累计值。
    total_usage: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            self.name = f"dashscope:{self.model}"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), DASHSCOPE_BATCH_SIZE):
            batch = texts[start : start + DASHSCOPE_BATCH_SIZE]
            vectors.extend(self._embed_batch(batch))
        return vectors

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": self.model,
            "input": texts,
            "dimensions": self.dimension,
            "encoding_format": "float",
        }
        url = self.base_url.rstrip("/") + self.path
        last_error: Exception | None = None
        for attempt in range(2):
            request = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=DASHSCOPE_TIMEOUT_SECONDS) as response:
                    body = json.loads(response.read().decode("utf-8"))
                rows = sorted(body.get("data") or [], key=lambda item: item.get("index", 0))
                vectors = [row.get("embedding") for row in rows]
                if len(vectors) != len(texts) or any(not isinstance(v, list) for v in vectors):
                    raise RuntimeError(
                        f"embedding 返回形状不对：要 {len(texts)} 条，拿到 {len(vectors)} 条"
                    )
                for index, vector in enumerate(vectors):
                    if len(vector) != self.dimension:
                        raise RuntimeError(
                            f"第 {index} 条向量维度是 {len(vector)}，与声明的 {self.dimension} 不符"
                        )
                self.last_usage = body.get("usage") or {}
                for key, value in self.last_usage.items():
                    if isinstance(value, int):
                        self.total_usage[key] = self.total_usage.get(key, 0) + value
                return vectors
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_error = RuntimeError(f"embedding HTTP {exc.code}: {detail[:300]}")
            except Exception as exc:  # noqa: BLE001 - 网络侧什么都可能抛，统一重试一次
                last_error = exc
            if attempt == 0:
                time.sleep(0.8)
        raise RuntimeError(str(last_error or "embedding 调用失败"))


def get_embedding_provider() -> EmbeddingProvider:
    """按 `EMBEDDING_PROVIDER` 解析当前生效的 provider；任何异常都退回 hash。"""
    requested = os.getenv("EMBEDDING_PROVIDER", "hash").strip().lower() or "hash"
    if requested in {"hash", "local", "none"}:
        return HashEmbeddingProvider()
    if requested != "dashscope":
        logger.warning("未知的 EMBEDDING_PROVIDER=%r，退回 hash", requested)
        return HashEmbeddingProvider()
    if _remote_disabled_reason:
        logger.warning("本进程已停用远端 embedding：%s", _remote_disabled_reason)
        return HashEmbeddingProvider()
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        disable_remote_embedding("DASHSCOPE_API_KEY 未配置")
        return HashEmbeddingProvider()
    model = os.getenv("EMBEDDING_MODEL", "text-embedding-v4").strip() or "text-embedding-v4"
    try:
        dimension = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
    except ValueError:
        dimension = 1024
    return DashScopeEmbeddingProvider(
        api_key=api_key,
        base_url=os.getenv("DASHSCOPE_EMBEDDING_HOST")
        or os.getenv("DASHSCOPE_API_HOST")
        or "https://dashscope.aliyuncs.com",
        model=model,
        dimension=dimension,
        path=os.getenv("DASHSCOPE_EMBEDDING_PATH", "/compatible-mode/v1/embeddings"),
    )


def embed_texts_with_fallback(
    provider: EmbeddingProvider, texts: list[str]
) -> tuple[EmbeddingProvider, list[list[float]]]:
    """嵌入一批文本；远端失败就整批换成 hash，并返回**实际使用的** provider。

    返回值里那个 provider 必须被调用方用来选 collection —— 否则降级后的 384 维向量
    会写进 1024 维的 collection 里，而那是一种不报错的错。
    """
    if not texts:
        return provider, []
    try:
        return provider, provider.embed_texts(texts)
    except Exception as exc:  # noqa: BLE001
        if isinstance(provider, DashScopeEmbeddingProvider):
            disable_remote_embedding(str(exc)[:200])
            fallback = HashEmbeddingProvider()
            return fallback, fallback.embed_texts(texts)
        raise


def disable_remote_embedding(reason: str) -> None:
    global _remote_disabled_reason
    if not _remote_disabled_reason:
        _remote_disabled_reason = reason
        logger.warning("远端 embedding 已停用，降级 hash：%s", reason)


def _collection_suffix(name: str) -> str:
    """把 provider 名压成合法的 Chroma collection 后缀（Chroma 名字有长度与字符限制）。"""
    raw = name.split(":", 1)[-1].lower()
    return re.sub(r"[^a-z0-9-]+", "-", raw).strip("-") or "model"


def collection_name_for_provider(provider: EmbeddingProvider) -> str:
    """provider → collection 名。hash 沿用历史名字 `visioncraft_memory`，不制造迁移。"""
    if isinstance(provider, HashEmbeddingProvider):
        return "visioncraft_memory"
    return f"visioncraft_memory_sem_{_collection_suffix(provider.name)}"[:63].strip("-_")


def known_collection_names() -> list[str]:
    """所有可能存有本项目向量的 collection —— 用于"切了 provider 却没重建索引"的告警。"""
    names = ["visioncraft_memory"]
    model = os.getenv("EMBEDDING_MODEL", "text-embedding-v4").strip() or "text-embedding-v4"
    names.append(f"visioncraft_memory_sem_{_collection_suffix('dashscope:' + model)}"[:63].strip("-_"))
    return list(dict.fromkeys(names))


def _embed_hash(text: str) -> list[float]:
    """与原先 `memory_service._embed_text` 逐位一致 —— 换层不换结果。"""
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
