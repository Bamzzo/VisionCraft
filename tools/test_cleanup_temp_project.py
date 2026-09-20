"""Read-only tests for tools/cleanup_temp_project.py.

Every case must be refused or be a dry run: this test must never delete anything.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import init_environment  # noqa: E402
from backend.database import connect, init_db  # noqa: E402

TOOL = ROOT / "tools" / "cleanup_temp_project.py"
PROTECTED = "v1demo_main"
# Mirrors cleanup_temp_project.PROTECTED. The queries below must exclude these,
# otherwise they can select a protected project: the refusal case would then pass
# for the wrong reason, and the dry-run case would fail because the tool refuses
# protected ids before it ever looks at emptiness.
TOOL_PROTECTED = ("v1demo_main", "project_5fdac03f50")


def run_tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), *args], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def project_exists(pid: str) -> bool:
    with connect() as conn:
        return bool(conn.execute("SELECT COUNT(*) AS n FROM projects WHERE id = ?", (pid,)).fetchone()["n"])


def pick_non_protected_project_with_shots() -> str:
    """First project that has shots and is not protected by the cleanup tool."""
    placeholders = ", ".join("?" for _ in TOOL_PROTECTED)
    with connect() as conn:
        row = conn.execute(
            f"SELECT p.id FROM projects p JOIN shots s ON s.project_id = p.id "
            f"WHERE p.id NOT IN ({placeholders}) GROUP BY p.id LIMIT 1",
            TOOL_PROTECTED,
        ).fetchone()
    return row["id"] if row else ""


def test_protected_project_refused() -> None:
    result = run_tool(f"--project-id={PROTECTED}", "--expect-title-prefix=LIVE2SHOT", "--apply")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "受保护项目" in result.stdout
    assert project_exists(PROTECTED), "受保护项目必须仍然存在"
    print("PASS: 受保护项目被拒绝且仍然存在")


def test_missing_project_is_noop() -> None:
    result = run_tool("--project-id=project_does_not_exist", "--expect-title-prefix=LIVE2SHOT", "--apply")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "不存在" in result.stdout
    print("PASS: 不存在的项目直接跳过，无副作用")


def test_title_prefix_mismatch_refused() -> None:
    result = run_tool(f"--project-id={PROTECTED}", "--expect-title-prefix=NOT-A-REAL-PREFIX", "--apply")
    assert result.returncode == 1
    assert project_exists(PROTECTED)
    print("PASS: 前缀不匹配时拒绝删除")


def test_non_empty_project_refused() -> None:
    pid = pick_non_protected_project_with_shots()
    if not pid:
        print("SKIP: 没有不受保护且含镜头的项目可供断言")
        return
    result = run_tool(f"--project-id={pid}", "--expect-title-prefix=", "--apply")
    assert result.returncode == 1, result.stdout + result.stderr
    assert project_exists(pid), "有镜头的项目不得被删除"
    print(f"PASS: 有镜头的项目 {pid} 被拒绝删除且仍然存在")


def test_allow_non_empty_still_requires_apply() -> None:
    pid = pick_non_protected_project_with_shots()
    if not pid:
        print("SKIP: 没有不受保护且含镜头的项目可供断言")
        return
    result = run_tool(f"--project-id={pid}", "--expect-title-prefix=", "--allow-non-empty")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DRY-RUN" in result.stdout
    assert project_exists(pid)
    print("PASS: 非空项目即使显式放行，缺 --apply 时仍只 dry-run")


def main() -> None:
    init_environment()
    init_db()
    test_protected_project_refused()
    test_missing_project_is_noop()
    test_title_prefix_mismatch_refused()
    test_non_empty_project_refused()
    test_allow_non_empty_still_requires_apply()
    print("PASS: cleanup temp project guards (read-only)")


if __name__ == "__main__":
    main()
