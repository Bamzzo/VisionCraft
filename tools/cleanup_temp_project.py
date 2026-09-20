"""Delete one leftover temporary project, with guards that refuse anything valuable.

Written after the live 2-shot harness adopted a pre-existing project and then deleted it: the
guards below make an accidental deletion of a real project impossible from this entry point.

Refuses unless ALL hold:
- project id is not in PROTECTED
- title starts with the expected prefix
- no inflight video tasks and no inflight shots
- the project has no shots, no video tasks and no assets (unless --allow-non-empty)

Usage:
    python tools/cleanup_temp_project.py --project-id=project_x --expect-title-prefix=LIVE2SHOT          # dry run
    python tools/cleanup_temp_project.py --project-id=project_x --expect-title-prefix=LIVE2SHOT --apply
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import DB_PATH, PROJECTS_DIR, init_environment  # noqa: E402
from backend.database import connect, init_db  # noqa: E402
from backend.services.project_service import delete_project  # noqa: E402

OUT = ROOT / "output" / "playwright" / "live-2shot"
PROTECTED = {"v1demo_main", "project_5fdac03f50"}
INFLIGHT_TASKS = ("submitted", "running", "pending_remote", "waiting_remote")
INFLIGHT_SHOTS = ("video_running", "video_waiting_remote")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--expect-title-prefix", required=True)
    parser.add_argument("--allow-non-empty", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    init_environment()
    init_db()

    pid = args.project_id
    if pid in PROTECTED:
        print(f"FAIL: {pid} 属于受保护项目，拒绝删除")
        return 1

    with connect() as conn:
        project = conn.execute("SELECT id, title FROM projects WHERE id = ?", (pid,)).fetchone()
        if not project:
            print(f"SKIP: 项目 {pid} 不存在")
            return 0
        shots = conn.execute("SELECT COUNT(*) AS n FROM shots WHERE project_id = ?", (pid,)).fetchone()["n"]
        tasks = conn.execute("SELECT COUNT(*) AS n FROM video_tasks WHERE project_id = ?", (pid,)).fetchone()["n"]
        assets = conn.execute("SELECT COUNT(*) AS n FROM assets WHERE project_id = ?", (pid,)).fetchone()["n"]
        inflight_tasks = conn.execute(
            f"SELECT COUNT(*) AS n FROM video_tasks WHERE project_id = ? AND status IN {INFLIGHT_TASKS}", (pid,)
        ).fetchone()["n"]
        inflight_shots = conn.execute(
            f"SELECT COUNT(*) AS n FROM shots WHERE project_id = ? AND status IN {INFLIGHT_SHOTS}", (pid,)
        ).fetchone()["n"]

    title = str(project["title"])
    print(f"目标：{pid} / {title}")
    print(f"shots={shots} video_tasks={tasks} assets={assets} inflight_tasks={inflight_tasks} inflight_shots={inflight_shots}")

    if not title.startswith(args.expect_title_prefix):
        print(f"FAIL: 标题不以 {args.expect_title_prefix} 开头，拒绝删除")
        return 1
    if inflight_tasks or inflight_shots:
        print("FAIL: 存在进行中的任务或镜头，拒绝删除")
        return 1
    if (shots or tasks or assets) and not args.allow_non_empty:
        print("FAIL: 项目非空（有镜头/任务/资产），需 --allow-non-empty 明确同意")
        return 1

    if not args.apply:
        print("DRY-RUN: 将删除数据库记录与项目目录，未执行")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    backup = OUT / f"db-pre-cleanup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    shutil.copy2(DB_PATH, backup)
    print(f"当前库已备份：{backup}")

    delete_project(pid)
    shutil.rmtree(PROJECTS_DIR / pid, ignore_errors=True)

    with connect() as conn:
        still_there = conn.execute("SELECT COUNT(*) AS n FROM projects WHERE id = ?", (pid,)).fetchone()["n"]
    dir_gone = not (PROJECTS_DIR / pid).exists()
    print(f"删除后：数据库行={still_there} 目录存在={not dir_gone}")
    report = {
        "schema": "visioncraft.cleanup_temp_project.v1",
        "cleaned_at": utc_now(),
        "project_id": pid,
        "title": title,
        "guard": "title_prefix+empty+no_inflight",
        "counts_before": {"shots": shots, "video_tasks": tasks, "assets": assets},
        "db_backup": str(backup),
        "row_removed": still_there == 0,
        "dir_removed": dir_gone,
    }
    (OUT / "cleanup_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"清理记录：{OUT / 'cleanup_report.json'}")
    return 0 if still_there == 0 and dir_gone else 1


if __name__ == "__main__":
    raise SystemExit(main())
