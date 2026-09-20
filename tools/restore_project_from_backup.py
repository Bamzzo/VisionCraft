"""Restore a project's rows and asset files from a database backup.

Used after project_a43afde7c5 was destroyed by the live 2-shot harness: the backup taken just
before the retire step still holds every row of that project.

Safety:
- refuses if the project already exists in the live database
- backs up the live database before writing anything
- inserts rows verbatim, keeping ids, so cross-table references stay valid
- rebuilds asset files only when the recorded sha256 matches a local source file

Usage:
    python tools/restore_project_from_backup.py --backup=<path> --project-id=project_x        # dry run
    python tools/restore_project_from_backup.py --backup=<path> --project-id=project_x --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import DB_PATH, PROJECTS_DIR, init_environment  # noqa: E402
from backend.database import connect, init_db  # noqa: E402

OUT = ROOT / "output" / "playwright" / "live-2shot"
JOB_EVENT_TABLE = "job_events"
# Parent rows first; shots <-> shot_versions form a cycle (shots.current_version_id), so foreign
# key checks are deferred to COMMIT and still fail if the final state is inconsistent.
TABLE_ORDER = [
    "projects",
    "shots",
    "shot_versions",
    "story_bibles",
    "characters",
    "scenes",
    "adaptation_scopes",
    "adaptation_options",
    "shot_drafts",
    "storyboard_drafts",
    "review_records",
    "vision_reviews",
    "workflow_checkpoints",
    "jobs",
    "video_tasks",
    "assets",
]


def ordered_tables(plan: dict[str, list[dict]]) -> list[str]:
    known = [name for name in TABLE_ORDER if name in plan]
    unknown = sorted(name for name in plan if name not in TABLE_ORDER)
    return known + unknown


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tables_with_project_id(backup: sqlite3.Connection) -> list[str]:
    names = [
        row[0]
        for row in backup.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    result = []
    for name in names:
        cols = [col[1] for col in backup.execute(f"PRAGMA table_info({name})")]
        if "project_id" in cols and name != JOB_EVENT_TABLE:
            result.append(name)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument(
        "--asset-source",
        default=str(ROOT.parent / "gyfy.jpg"),
        help="用于重建资产文件的本机来源文件（默认工作区旁 gyfy.jpg）",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    init_environment()
    init_db()
    backup_path = Path(args.backup)
    if not backup_path.is_file():
        print(f"FAIL: 备份不存在 {backup_path}")
        return 1

    with connect() as live:
        exists = live.execute("SELECT COUNT(*) AS n FROM projects WHERE id = ?", (args.project_id,)).fetchone()["n"]
    if exists:
        print(f"SKIP: 项目中已存在 {args.project_id}，拒绝覆盖")
        return 0

    bak = sqlite3.connect(f"file:{backup_path.as_posix()}?mode=ro", uri=True)
    bak.row_factory = sqlite3.Row
    plan: dict[str, list[dict]] = {}
    project_rows = [dict(row) for row in bak.execute("SELECT * FROM projects WHERE id = ?", (args.project_id,))]
    if project_rows:
        plan["projects"] = project_rows
    for table in tables_with_project_id(bak):
        rows = [dict(row) for row in bak.execute(f"SELECT * FROM {table} WHERE project_id = ?", (args.project_id,))]
        if rows:
            plan[table] = rows
    shot_ids = [row["id"] for row in plan.get("shots", [])]
    if shot_ids:
        placeholders = ",".join("?" for _ in shot_ids)
        plan["shot_versions"] = [
            dict(row)
            for row in bak.execute(f"SELECT * FROM shot_versions WHERE shot_id IN ({placeholders})", shot_ids)
        ]
    events = [dict(row) for row in bak.execute(f"SELECT * FROM {JOB_EVENT_TABLE} WHERE project_id = ? ORDER BY id", (args.project_id,))]

    print(f"备份 {backup_path.name} 中可恢复：")
    for table, rows in plan.items():
        print(f"  {table}: {len(rows)} 行")
    print(f"  {JOB_EVENT_TABLE}: {len(events)} 行（按原顺序重建，不保留旧整型 id）")

    assets = plan.get("assets", [])
    source = Path(args.asset_source)
    asset_actions = []
    if assets:
        if not source.is_file():
            print(f"FAIL: 资产来源文件不存在 {source}")
            return 1
        source_sha = sha256_of(source)
        by_name: dict[str, dict] = {}
        for row in assets:
            file_path = str(row.get("file_path") or "")
            name = file_path.rsplit("/", 1)[-1]
            if not name:
                print("FAIL: 存在没有 file_path 的资产行，拒绝恢复")
                return 1
            entry = by_name.setdefault(name, {"expected": set(), "rows_without_sha": 0})
            expected = str(row.get("sha256") or "")
            if expected:
                entry["expected"].add(expected)
            else:
                entry["rows_without_sha"] += 1
        print(f"资产重建：来源 {source.name} sha256={source_sha[:12]}…")
        for name, entry in sorted(by_name.items()):
            mismatched = {sha for sha in entry["expected"] if sha != source_sha}
            match = (not entry["expected"] or not mismatched) and not mismatched
            asset_actions.append(
                {
                    "name": name,
                    "expected_sha256": sorted(entry["expected"]),
                    "match": match,
                    "rows_without_sha": entry["rows_without_sha"],
                }
            )
            note = "" if entry["expected"] else "（该文件所有资产行均无 sha256，按同路径校验）"
            print(f"  {name}: {'MATCH' if match else 'MISMATCH'} {note}")
        if any(not item["match"] for item in asset_actions):
            print("FAIL: 存在 sha256 不匹配的资产，拒绝重建（避免写入错内容）")
            return 1

    if not args.apply:
        print("DRY-RUN: 未写入任何数据")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    live_backup = OUT / f"db-pre-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    shutil.copy2(DB_PATH, live_backup)
    print(f"当前库已备份：{live_backup}")

    restored: dict[str, int] = {}
    with connect() as live:
        # shots.current_version_id <-> shot_versions.shot_id is a genuine cycle, and the project row
        # references adaptation_options. Foreign keys are therefore checked explicitly after the
        # inserts instead of per statement.
        live.execute("PRAGMA foreign_keys = OFF")
        for table in ordered_tables(plan):
            rows = plan[table]
            cols = list(rows[0].keys())
            sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})"
            try:
                for row in rows:
                    live.execute(sql, [row[c] for c in cols])
            except sqlite3.IntegrityError as exc:
                print(f"FAIL: 写入 {table} 时约束失败：{exc}")
                raise
            restored[table] = len(rows)
        if events:
            cols = [c for c in events[0].keys() if c != "id"]
            sql = f"INSERT INTO {JOB_EVENT_TABLE} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})"
            for row in events:
                live.execute(sql, [row[c] for c in cols])
            restored[JOB_EVENT_TABLE] = len(events)
        live.commit()
        live.execute("PRAGMA foreign_keys = ON")
        violations = live.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            print(f"FAIL: 恢复后外键校验发现 {len(violations)} 处不一致")
            for item in violations[:10]:
                print("  ", tuple(item))
            return 1
        print("外键校验：通过（0 处不一致）")

    project_dir = PROJECTS_DIR / args.project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    for item in asset_actions:
        shutil.copyfile(source, project_dir / item["name"])

    with connect() as live:
        counts = {
            "projects": live.execute("SELECT COUNT(*) AS n FROM projects WHERE id = ?", (args.project_id,)).fetchone()["n"],
            "shots": live.execute("SELECT COUNT(*) AS n FROM shots WHERE project_id = ?", (args.project_id,)).fetchone()["n"],
            "assets": live.execute("SELECT COUNT(*) AS n FROM assets WHERE project_id = ?", (args.project_id,)).fetchone()["n"],
            "video_tasks": live.execute("SELECT COUNT(*) AS n FROM video_tasks WHERE project_id = ?", (args.project_id,)).fetchone()["n"],
        }
    print("恢复后核对：" + json.dumps(counts, ensure_ascii=False))
    report = {
        "schema": "visioncraft.restore_project_from_backup.v1",
        "restored_at": utc_now(),
        "project_id": args.project_id,
        "backup": str(backup_path),
        "live_db_backup": str(live_backup),
        "restored_rows": restored,
        "asset_source": str(source),
        "assets_rebuilt": [item["name"] for item in asset_actions],
        "assets_without_recorded_sha256": [
            item["name"] for item in asset_actions if item["rows_without_sha"]
        ],
        "counts_after": counts,
        "note": "从事故前备份恢复；资产文件按记录 sha256 校验后重建。",
    }
    report_path = OUT / "restore_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"恢复记录：{report_path}")
    if counts["projects"] != 1:
        print("FAIL: 项目行未恢复")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
