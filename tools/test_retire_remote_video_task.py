"""Read-only tests for tools/retire_remote_video_task.py.

Only exercises dry-run and refusal paths: the real database must never be mutated by a test.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import init_environment  # noqa: E402
from backend.database import connect, init_db  # noqa: E402

INFLIGHT = ("submitted", "running", "pending_remote", "waiting_remote")
TOOL = ROOT / "tools" / "retire_remote_video_task.py"


def run_tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), *args], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def snapshot(task_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM video_tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else {}


def test_unknown_task_refused() -> None:
    result = run_tool("--task-id=vt_does_not_exist", "--reason=test")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "未找到任务" in result.stdout
    print("PASS: 未知任务被拒绝，退出码 1")


def test_non_inflight_task_untouched() -> None:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, status FROM video_tasks WHERE status NOT IN (?,?,?,?) ORDER BY updated_at DESC LIMIT 1",
            INFLIGHT,
        ).fetchall()
    if not rows:
        print("SKIP: 数据库中没有非活动任务可供断言")
        return
    task_id = rows[0]["id"]
    before = snapshot(task_id)
    result = run_tool(f"--task-id={task_id}", "--reason=test")
    after = snapshot(task_id)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SKIP" in result.stdout, result.stdout
    assert before == after, "非活动任务不得被改写"
    print(f"PASS: 非活动任务 {task_id}（{before['status']}）被拒绝改写且记录零变化")


def test_dry_run_never_writes() -> None:
    with connect() as conn:
        rows = conn.execute("SELECT id FROM video_tasks ORDER BY updated_at DESC LIMIT 1").fetchall()
    if not rows:
        print("SKIP: 数据库中没有 video_tasks 记录")
        return
    task_id = rows[0]["id"]
    before = snapshot(task_id)
    result = run_tool(f"--task-id={task_id}", "--reason=test")
    after = snapshot(task_id)
    assert before == after, "dry-run 不得写入"
    if "DRY-RUN" in result.stdout:
        assert "活动任务数（收尾前）" in result.stdout
        print(f"PASS: dry-run 报告将执行的动作且零写入（任务 {task_id}）")
    else:
        assert "SKIP" in result.stdout, result.stdout
        print(f"PASS: 非活动任务走拒绝分支且零写入（任务 {task_id}）")


def test_reason_is_required() -> None:
    result = run_tool("--task-id=vt_x")
    assert result.returncode != 0, "缺少 --reason 必须失败"
    print("PASS: 缺少 --reason 时参数校验失败")


def test_no_secret_in_retire_report() -> None:
    report = ROOT / "output" / "playwright" / "live-2shot" / "retire_report.json"
    if not report.is_file():
        print("SKIP: 尚无 retire_report.json")
        return
    text = report.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["row_kept"] is True
    assert payload["remote_task_id"], "收尾必须保留 remote_task_id 作为证据"
    assert payload["downloaded"] is False
    assert "sk-" not in text and "Authorization" not in text
    print("PASS: 收尾报告保留 remote_task_id、不声称下载、无密钥痕迹")


def main() -> None:
    init_environment()
    init_db()
    test_unknown_task_refused()
    test_non_inflight_task_untouched()
    test_dry_run_never_writes()
    test_reason_is_required()
    test_no_secret_in_retire_report()
    print("PASS: retire remote video task tool (read-only)")


if __name__ == "__main__":
    main()
