"""检索评测：给「语义 embedding 到底值不值」一个可复现的数字。

为什么需要它：产品里 `build_shot_evidence(shot)` 走 `search_project_memory(..., limit=2)`
—— 也就是 **k=2** 的小区间。小 k 下差一点点就整条掉出结果，所以这里按 k=1/2/5 分档报，
而不是只报一个好看的 recall@10。

三种模式，用来把「增益来自哪一项」拆开，而不是笼统说"更好了"：

- `hybrid`       产品实际口径：向量候选 ∪ FTS 字面候选，按权重合并。
- `vector_only`  只看向量近邻。换 embedding provider 时，这一列才是"语义"本身的贡献。
- `lexical_only` 只看字符集合重合分（向量与 FTS 都关掉）。这是本轮之前的**事实基线**：
                 现有打分里 lexical 占 0.8，所以它很接近"什么都没做"时的表现。

评测前先答一个问题：**这条用例答得出来吗**。若没有任何块包含 `must_contain`，记
`UNANSWERABLE` 并排除出召回统计 —— 把"标注失效 / 被切分切断"和"检索没召回"混在一起，
指标就没有意义了。

用法：
    python tools/eval_retrieval.py --provider hash
    python tools/eval_retrieval.py --provider dashscope --mode vector_only --k 2
结果追加到 `tools/eval_results.md`。
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

# 评测要建项目、跑分析、重建索引、再删项目 —— 全都写数据库。**必须与真实数据目录隔离**：
# 第一版直接跑在 `backend/data` 下，7 轮评测就在真库里留下 2675 行指向已删项目的全文索引
# 行（检索侧有 JOIN 挡着，功能无碍，但污染开发机，还会让"索引表里只有当前项目"这类断言
# 在隔离环境之外失效）。这里改用临时目录，跑完删掉；`config` 在 import 时读这个变量，
# 所以必须在任何 `backend.*` import **之前**设好。
EVAL_DATA_DIR = Path(tempfile.mkdtemp(prefix="visioncraft-eval-"))
os.environ["VISIONCRAFT_DATA_DIR"] = str(EVAL_DATA_DIR)
os.environ.setdefault("CODEBUDDY_SAFE_DELETE_ENABLED", "0")
# 走 atexit 而不是只在 finally 里删：main() 有几条提前 return（语料缺失/参数非法），
# 那些路径不会经过 finally，临时目录会留在 %TEMP% 里。
atexit.register(shutil.rmtree, EVAL_DATA_DIR, ignore_errors=True)

CORPUS = ROOT.parent / "output" / "test_texts" / "蛊真人100000字.txt"
EVAL_SET = ROOT / "tools" / "eval_retrieval_set.json"
RESULTS = ROOT / "tools" / "eval_results.md"

MODES = ("hybrid", "vector_only", "lexical_only")


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _make_project(source: str, title: str) -> str:
    from backend.database import connect, utc_now

    project_id = f"eval_{uuid.uuid4().hex[:10]}"
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
    from backend.config import PROJECTS_DIR
    from backend.services.project_service import delete_project

    delete_project(project_id)
    shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)


def _chunks(project_id: str) -> list[dict]:
    from backend.database import connect

    with connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id, chunk_index, text, chapter_index FROM source_chunks "
                "WHERE project_id = ? ORDER BY chunk_index",
                (project_id,),
            ).fetchall()
        ]


def _rank_vector(project_id: str, query: str, k: int) -> list[str]:
    from backend.providers.embedding_provider import get_embedding_provider
    from backend.services import memory_service

    provider = get_embedding_provider()
    result = memory_service.get_collection(provider).query(
        query_embeddings=provider.embed_texts([query]),
        n_results=max(1, k),
        where={"project_id": project_id},
        include=["documents"],
    )
    return list(result.get("documents", [[]])[0])


def _rank_lexical(chunks: list[dict], query: str, k: int) -> list[str]:
    from backend.services import memory_service

    scored = [(memory_service._lexical_score(query, row["text"]), row["text"]) for row in chunks]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [text for _, text in scored[:k]]


def _rank_hybrid(project_id: str, query: str, k: int) -> list[str]:
    from backend.services import memory_service

    return [item["document"] for item in memory_service.search_project_memory(project_id, query, k)]


def _rank(mode: str, project_id: str, chunks: list[dict], query: str, k: int) -> list[str]:
    if mode == "vector_only":
        return _rank_vector(project_id, query, k)
    if mode == "lexical_only":
        return _rank_lexical(chunks, query, k)
    return _rank_hybrid(project_id, query, k)


def _evaluate(mode: str, project_id: str, chunks: list[dict], cases: list[dict], k: int) -> dict:
    """返回 recall@k / MRR 与逐类分项。"""
    per_case = []
    for case in cases:
        docs = _rank(mode, project_id, chunks, case["query"], k)
        rank = 0
        for index, doc in enumerate(docs, start=1):
            if case["must_contain"] in doc:
                rank = index
                break
        per_case.append({"category": case["category"], "rank": rank, "query": case["query"]})

    answerable = len(cases)
    hits = [item for item in per_case if item["rank"]]
    recall = len(hits) / answerable if answerable else 0.0
    mrr = sum(1.0 / item["rank"] for item in hits) / answerable if answerable else 0.0

    by_category: dict[str, dict] = {}
    for item in per_case:
        bucket = by_category.setdefault(item["category"], {"n": 0, "hit": 0, "rr": 0.0})
        bucket["n"] += 1
        if item["rank"]:
            bucket["hit"] += 1
            bucket["rr"] += 1.0 / item["rank"]
    for bucket in by_category.values():
        bucket["recall"] = bucket["hit"] / bucket["n"] if bucket["n"] else 0.0
        bucket["mrr"] = bucket["rr"] / bucket["n"] if bucket["n"] else 0.0
    return {"recall": recall, "mrr": mrr, "by_category": by_category, "per_case": per_case}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="hash", help="hash | dashscope")
    parser.add_argument("--mode", default="all", help="hybrid | vector_only | lexical_only | all")
    parser.add_argument("--k", default="2", help="逗号分隔可一次测多个，如 2,5（产品走 limit=2）")
    parser.add_argument("--no-record", action="store_true", help="不追加结果到 eval_results.md")
    parser.add_argument(
        "--note",
        default="",
        help="写进结果表 note 列，用来区分 A/B（如「候选池修复前 / 后」）。"
        "没有它，改了召回逻辑前后的数字会混在一张表里看不出来。",
    )
    args = parser.parse_args()

    # 必须在 init_environment() 之前设：load_dotenv 默认不覆盖已存在的环境变量，
    # 所以这里设的值才是最终生效的（`.env` 里配了什么都不会盖掉它）。
    os.environ["EMBEDDING_PROVIDER"] = args.provider

    from backend.config import init_environment
    from backend.database import init_db
    from backend.providers.embedding_provider import get_embedding_provider, HashEmbeddingProvider
    from backend.services import memory_service
    from backend.services.medium_text_service import run_medium_analysis

    init_environment()
    init_db()
    print(f"数据目录（隔离）: {EVAL_DATA_DIR}")

    if not CORPUS.is_file():
        print(f"SKIP: 语料不存在 {CORPUS}")
        return 2
    payload = json.loads(EVAL_SET.read_text(encoding="utf-8"))
    cases = payload["cases"]
    modes = list(MODES) if args.mode == "all" else [args.mode]
    for mode in modes:
        if mode not in MODES:
            print(f"未知 mode: {mode}（可选 {MODES}）")
            return 2
    # 一次索引、多 k 连测：索引才是花钱的那一步（远端要真发请求），
    # 查询侧几乎免费。所以 k 用逗号列表而不是跑多轮。
    try:
        ks = [int(part) for part in str(args.k).replace(",", " ").split()]
    except ValueError:
        ks = []
    if not ks:
        print(f"非法 --k: {args.k!r}")
        return 2

    provider = get_embedding_provider()
    requested = args.provider
    active = provider.name
    if requested != "hash" and isinstance(provider, HashEmbeddingProvider):
        print(f"⚠️ 请求 provider={requested}，实际解析为 hash（缺 key 或本进程已降级）。下面这组数字是 hash 的。")

    source = CORPUS.read_text(encoding="utf-8", errors="replace")
    project_id = _make_project(source, f"检索评测 {args.provider}")
    print(f"provider={requested} active={active} k={ks} modes={modes}")
    print(f"语料 {len(source)} 字 / 用例 {len(cases)} 条")
    try:
        started = time.time()
        run_medium_analysis(project_id)
        indexed = memory_service.index_project_memory(project_id)
        chunks = _chunks(project_id)
        print(f"分析 + 索引完成：{indexed} 条向量 / {len(chunks)} 个章节块 / {time.time() - started:.1f}s")

        # 先定界：没有块包含标注 → 这条用例对本次切分不可答，排除出召回统计并明说。
        unanswerable = []
        for case in cases:
            if not any(case["must_contain"] in row["text"] for row in chunks):
                unanswerable.append(case)
        answerable = [case for case in cases if case not in unanswerable]
        if unanswerable:
            print(f"⚠️ {len(unanswerable)} 条用例没有块包含标注（切分切断了？），已排除出召回统计：")
            for case in unanswerable:
                print(f"   [{case['category']}] {case['query']} → {case['must_contain']}")
        else:
            print(f"全部 {len(answerable)} 条用例均可答（都有块包含标注）")

        rows = []
        for k_value in ks:
            for mode in modes:
                result = _evaluate(mode, project_id, chunks, answerable, k_value)
                rows.append((mode, k_value, result))
                print(f"\n=== mode={mode} k={k_value} ===")
                print(f"整体  recall@{k_value}={result['recall']:.4f}  MRR={result['mrr']:.4f}")
                for category, bucket in sorted(result["by_category"].items()):
                    print(f"  {category:11s} n={bucket['n']:2d}  recall={bucket['recall']:.4f}  MRR={bucket['mrr']:.4f}")
                misses = [item for item in result["per_case"] if not item["rank"]]
                if misses:
                    print(f"  未命中 {len(misses)} 条：" + "；".join(item["query"] for item in misses[:4]))

        if not args.no_record:
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            commit = _git_commit()
            lines = []
            if not RESULTS.exists():
                lines.append("# VisionCraft 检索评测结果\n")
                lines.append(
                    "| timestamp | commit | provider | active_provider | mode | k | cases | recall@k | MRR | note |"
                )
                lines.append("|---|---|---|---|---|---:|---:|---:|---:|---|")
            for mode, k, result in rows:
                lines.append(
                    f"| {stamp} | {commit} | {requested} | {active} | {mode} | {k} | {len(answerable)} | "
                    f"{result['recall']:.4f} | {result['mrr']:.4f} | {args.note} |"
                )
            with RESULTS.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            print(f"\n已追加 {len(rows)} 行到 {RESULTS.relative_to(ROOT)}")
        return 0
    finally:
        _cleanup(project_id)
        shutil.rmtree(EVAL_DATA_DIR, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
