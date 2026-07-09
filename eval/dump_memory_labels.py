from __future__ import annotations

from pathlib import Path

from eval_support import PROJECT_ID, ensure_eval_project, load_memory_rows


OUTPUT_PATH = Path(__file__).resolve().parent / "memory_labels_dump.md"


def main() -> None:
    ensure_eval_project(PROJECT_ID)
    rows = sorted(load_memory_rows(PROJECT_ID), key=lambda row: (row["kind"], row["label"], row["id"]))
    lines = [
        "# VisionCraft Retrieval Eval Memory Labels",
        "",
        f"project_id: `{PROJECT_ID}`",
        "",
        "| label | kind | excerpt |",
        "|---|---|---|",
    ]
    for row in rows:
        excerpt = " ".join(row["document"].split())[:50]
        lines.append(f"| {row['label']} | {row['kind']} | {excerpt} |")
    OUTPUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}")
    print(f"memory_rows {len(rows)}")


if __name__ == "__main__":
    main()
