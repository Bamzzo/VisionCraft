"""Retire a dead remote video task so it stops blocking preflight and refresh loops.

A remote task whose provider reports an invalid/expired task id can never complete, but the
refresh path only updates `shots` and `jobs`, leaving `video_tasks.status` at running. That stale
row keeps the project "inflight" forever, blocks `run_live_2shot.py` preflight, and makes every
later refresh re-query the same dead id.

This tool closes such a row honestly: it keeps the row and the remote_task_id (evidence), records
the provider error, and only flips the status. It backs up the database before writing.

Usage:
    python tools/retire_remote_video_task.py --task-id=vt_x --reason="..."          # dry run
    python tools/retire_remote_video_task.py --task-id=vt_x --reason="..." --apply
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

from backend.config import DB_PATH, init_environment  # noqa: E402
from backend.database import connect, init_db  # noqa: E402

INFLIGHT = ("submitted", "running", "pending_remote", "waiting_remote")
OUT = ROOT / "output" / "playwright" / "live-2shot"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def inflight_count() -> int:
    with connect() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM video_tasks WHERE status IN (?,?,?,?)", INFLIGHT
            ).fetchone()["n"]
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--reason", required=True, help="脱敏后的失败原因，不得包含密钥")
    parser.add_argument("--error-code", default="TASK_ID_INVALID")
    parser.add_argument("--cloud-status", default="invalid_task_id")
    parser.add_argument("--apply", action="store_true", help="真正写入；缺省为 dry-run")
    args = parser.parse_args()

    init_environment()
    init_db()

    with connect() as conn:
        row = conn.execute("SELECT * FROM video_tasks WHERE id = ?", (args.task_id,)).fetchone()
    if not row:
        print(f"FAIL: 未找到任务 {args.task_id}")
        return 1
    task = dict(row)
    print("任务现状：")
    print(
        json.dumps(
            {
                "id": task["id"],
                "project_id": task["project_id"],
                "shot_id": task["shot_id"],
                "status": task["status"],
                "cloud_status": task["cloud_status"],
                "remote_task_id": task["remote_task_id"],
                "result_path": task.get("result_path"),
            },
            ensure_ascii=False,
        )
    )

    if task["status"] not in INFLIGHT:
        print(f"SKIP: 任务状态 {task['status']} 不属于活动集合，拒绝改写")
        return 0

    active_before = inflight_count()
    print(f"活动任务数（收尾前）= {active_before}")

    if not args.apply:
        print("DRY-RUN: 将把 status 置为 failed，并写入 error_code/error_message；不改 remote_task_id")
        print(f"DRY-RUN: 备份路径将为 {OUT / ('db-backup-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.db')}")
        print(f"DRY-RUN: 收尾后活动任务数预计 = {active_before - 1}")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    backup = OUT / f"db-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    shutil.copy2(DB_PATH, backup)
    if not backup.is_file() or backup.stat().st_size == 0:
        print("FAIL: 数据库备份失败，未做任何写入")
        return 1
    print(f"备份完成：{backup}（{backup.stat().st_size} 字节）")

    with connect() as conn:
        conn.execute(
            """
            UPDATE video_tasks
            SET status = ?, cloud_status = ?, error_code = ?, error_message = ?, updated_at = ?
            WHERE id = ?
            """,
            ("failed", args.cloud_status, args.error_code, args.reason, utc_now(), args.task_id),
        )

    with connect() as conn:
        after = dict(conn.execute("SELECT * FROM video_tasks WHERE id = ?", (args.task_id,)).fetchone())
    active_after = inflight_count()
    print(
        "收尾后："
        + json.dumps(
            {"status": after["status"], "cloud_status": after["cloud_status"], "remote_task_id": after["remote_task_id"]},
            ensure_ascii=False,
        )
    )
    print(f"活动任务数（收尾后）= {active_after}")

    report = {
        "schema": "visioncraft.retire_remote_video_task.v1",
        "retired_at": utc_now(),
        "task_id": task["id"],
        "project_id": task["project_id"],
        "shot_id": task["shot_id"],
        "remote_task_id": task["remote_task_id"],
        "status_before": "running",
        "status_after": after["status"],
        "error_code": args.error_code,
        "error_message": args.reason,
        "reason_source": "provider_query_http_400_invalid_task_id",
        "row_kept": True,
        "downloaded": False,
        "db_backup": str(backup),
        "inflight_before": active_before,
        "inflight_after": active_after,
    }
    report_path = OUT / "retire_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"收尾记录：{report_path}")
    if active_after != active_before - 1:
        print("WARN: 活动任务数变化与预期不一致，请复核")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
