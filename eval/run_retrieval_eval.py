from __future__ import annotations

import argparse
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from eval_support import PROJECT_ID, ensure_eval_project, load_eval_set, load_memory_rows
from backend.providers.embedding_provider import get_embedding_provider
from backend.services.memory_service import search_project_memory


EVAL_DIR = Path(__file__).resolve().parent
EVAL_SET_PATH = EVAL_DIR / "retrieval_eval_set.json"
RESULTS_PATH = EVAL_DIR / "results.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run VisionCraft memory retrieval evaluation.")
    parser.add_argument("--provider", choices=["hash", "siliconflow"], default="hash")
    parser.add_argument("--mode", choices=["vector_only", "lexical_only", "hybrid"], default="hybrid")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--breakdown", action="store_true", help="Also append metrics grouped by case category.")
    return parser.parse_args()


def configure_environment(provider: str, mode: str) -> None:
    os.environ["EMBEDDING_PROVIDER"] = provider
    if mode == "vector_only":
        os.environ["HYBRID_LEXICAL_WEIGHT"] = "0"
        os.environ["HYBRID_VECTOR_WEIGHT"] = "1"
    elif mode == "lexical_only":
        os.environ["HYBRID_LEXICAL_WEIGHT"] = "1"
        os.environ["HYBRID_VECTOR_WEIGHT"] = "0"
    else:
        if provider == "siliconflow":
            os.environ["HYBRID_LEXICAL_WEIGHT"] = os.getenv("HYBRID_LEXICAL_WEIGHT", "0.3")
            os.environ["HYBRID_VECTOR_WEIGHT"] = os.getenv("HYBRID_VECTOR_WEIGHT", "0.7")
        else:
            os.environ["HYBRID_LEXICAL_WEIGHT"] = os.getenv("HYBRID_LEXICAL_WEIGHT", "0.8")
            os.environ["HYBRID_VECTOR_WEIGHT"] = os.getenv("HYBRID_VECTOR_WEIGHT", "0.2")


def validate_expected_labels(cases: list[dict], memory_rows: list[dict]) -> None:
    known_labels = {row["label"] for row in memory_rows}
    missing: list[tuple[str, str]] = []
    for case in cases:
        for label in case.get("expected_labels", []):
            if label not in known_labels:
                missing.append((case.get("query", ""), label))
    if missing:
        details = "\n".join(f"- query={query!r}, missing_label={label!r}" for query, label in missing)
        raise SystemExit(f"expected_labels not found in memory labels:\n{details}")


def evaluate_cases(project_id: str, cases: list[dict], k: int) -> dict:
    recall_values: list[float] = []
    reciprocal_ranks: list[float] = []
    hit_values: list[float] = []
    expected_count = 0
    for case in cases:
        expected = set(case.get("expected_labels") or [])
        results = search_project_memory(project_id, case["query"], limit=k)
        labels = [(item.get("metadata") or {}).get("label", "") for item in results]
        hit_count = sum(1 for label in expected if label in labels)
        expected_count += len(expected)
        recall_values.append(hit_count / max(1, len(expected)))
        hit_values.append(1.0 if hit_count > 0 else 0.0)
        first_rank = 0
        for rank, label in enumerate(labels, start=1):
            if label in expected:
                first_rank = rank
                break
        reciprocal_ranks.append(1 / first_rank if first_rank else 0.0)
    recall = sum(recall_values) / max(1, len(recall_values))
    mrr = sum(reciprocal_ranks) / max(1, len(reciprocal_ranks))
    hit_rate = sum(hit_values) / max(1, len(hit_values))
    return {
        "cases": len(cases),
        "expected_labels": expected_count,
        "recall": recall,
        "mrr": mrr,
        "hit_rate": hit_rate,
    }


def group_cases(cases: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {"direct_match": [], "semantic_rewrite": [], "cross_shot": []}
    for case in cases:
        group = case_group(case)
        grouped.setdefault(group, []).append(case)
    return {key: value for key, value in grouped.items() if value}


def case_group(case: dict) -> str:
    note = str(case.get("note") or "")
    if note.startswith("直接"):
        return "direct_match"
    if note.startswith("同义"):
        return "semantic_rewrite"
    if note.startswith("跨镜头"):
        return "cross_shot"
    return "uncategorized"


def git_commit_hash() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def append_result(row: dict) -> None:
    header = (
        "# VisionCraft Retrieval Evaluation Results\n\n"
        "| timestamp | commit | provider | active_provider | mode | k | cases | recall@k | MRR | status |\n"
        "|---|---|---|---|---|---:|---:|---:|---:|---|\n"
    )
    if not RESULTS_PATH.exists():
        RESULTS_PATH.write_text(header, encoding="utf-8")
    line = (
        f"| {row['timestamp']} | {row['commit']} | {row['provider']} | {row['active_provider']} | "
        f"{row['mode']} | {row['k']} | {row['cases']} | {row['recall']:.4f} | {row['mrr']:.4f} | {row['status']} |\n"
    )
    with RESULTS_PATH.open("a", encoding="utf-8") as file:
        file.write(line)


def append_breakdown(rows: list[dict]) -> None:
    if not rows:
        return
    section = (
        "\n## Breakdown By Case Category\n\n"
        "| timestamp | commit | provider | active_provider | mode | k | group | cases | expected_labels | recall@k | MRR | hit_rate@k | status |\n"
        "|---|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---|\n"
    )
    existing = RESULTS_PATH.read_text(encoding="utf-8") if RESULTS_PATH.exists() else ""
    with RESULTS_PATH.open("a", encoding="utf-8") as file:
        if "## Breakdown By Case Category" not in existing:
            file.write(section)
        for row in rows:
            file.write(
                f"| {row['timestamp']} | {row['commit']} | {row['provider']} | {row['active_provider']} | "
                f"{row['mode']} | {row['k']} | {row['group']} | {row['cases']} | {row['expected_labels']} | "
                f"{row['recall']:.4f} | {row['mrr']:.4f} | {row['hit_rate']:.4f} | {row['status']} |\n"
            )


def main() -> None:
    args = parse_args()
    configure_environment(args.provider, args.mode)
    print(f"rebuilding synthetic eval project {PROJECT_ID}; run this script serially")
    ensure_eval_project(PROJECT_ID)
    provider = get_embedding_provider()
    eval_set = load_eval_set(EVAL_SET_PATH)
    memory_rows = load_memory_rows(PROJECT_ID)
    cases = eval_set["cases"]
    validate_expected_labels(cases, memory_rows)
    metrics = evaluate_cases(eval_set["project_id"], cases, max(1, args.k))
    active_provider = provider.name
    status = "OK"
    if args.provider == "siliconflow" and not active_provider.startswith("siliconflow:"):
        status = "PENDING_LIVE_KEY"
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    commit = git_commit_hash()
    row = {
        "timestamp": timestamp,
        "commit": commit,
        "provider": args.provider,
        "active_provider": active_provider,
        "mode": args.mode,
        "k": max(1, args.k),
        "cases": metrics["cases"],
        "recall": metrics["recall"],
        "mrr": metrics["mrr"],
        "status": status,
    }
    append_result(row)
    print(
        f"provider={args.provider} active_provider={active_provider} mode={args.mode} "
        f"k={row['k']} cases={metrics['cases']} recall@k={metrics['recall']:.4f} "
        f"mrr={metrics['mrr']:.4f} hit_rate@k={metrics['hit_rate']:.4f} status={status}"
    )
    if args.breakdown:
        breakdown_rows = []
        for group, group_items in group_cases(cases).items():
            group_metrics = evaluate_cases(eval_set["project_id"], group_items, max(1, args.k))
            breakdown_rows.append(
                {
                    "timestamp": timestamp,
                    "commit": commit,
                    "provider": args.provider,
                    "active_provider": active_provider,
                    "mode": args.mode,
                    "k": max(1, args.k),
                    "group": group,
                    "status": status,
                    **group_metrics,
                }
            )
            print(
                f"  {group}: cases={group_metrics['cases']} expected_labels={group_metrics['expected_labels']} "
                f"recall@k={group_metrics['recall']:.4f} mrr={group_metrics['mrr']:.4f} "
                f"hit_rate@k={group_metrics['hit_rate']:.4f}"
            )
        append_breakdown(breakdown_rows)
    print(f"appended {RESULTS_PATH}")


if __name__ == "__main__":
    main()
