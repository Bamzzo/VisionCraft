"""无费用浏览器验收：UI-0～UI-1 四区工作台信息架构与交互原型。

覆盖：新建/创建项目分离、未保存守卫、阶段查看与执行分离、素材视图切换、
脏状态重做门控、上游重做下游失效、任务进度免刷新、切换项目不污染任务事件、
长标题 2 行省略。主验收截图为 output/playwright/ui-0*.png；
旧 workbench-*.png / short-*.png 仅作历史产物保留。使用 mock 后端，不接入付费 API。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

BASE = os.environ.get("VISIONCRAFT_BASE_URL", "http://127.0.0.1:8000")


def _health_ok() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _npm() -> str:
    found = shutil.which("npm.cmd") or shutil.which("npm")
    if not found:
        raise SystemExit("真实浏览器验收失败：未找到 npm。请安装 Node.js 后再运行本脚本。")
    return found


def _ensure_playwright(harness: Path) -> None:
    module_dir = harness / "node_modules" / "playwright"
    harness.mkdir(parents=True, exist_ok=True)
    manifest = harness / "package.json"
    if not manifest.exists():
        manifest.write_text('{"name":"visioncraft-playwright","private":true}\n', encoding="utf-8")
    npm = _npm()
    if not module_dir.exists():
        subprocess.run([npm, "install", "playwright@1.55.1"], cwd=harness, check=True)
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    subprocess.run([npx, "playwright", "install", "chromium"], cwd=harness, check=True)


def main() -> None:
    if not _health_ok():
        raise SystemExit(
            f"真实浏览器验收失败：{BASE} 未响应。请先在本机启动 "
            "`python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`"
        )
    harness = ROOT / ".playwright-cli"
    _ensure_playwright(harness)
    script = ROOT / "tools" / "ui_workbench.cjs"
    env = os.environ.copy()
    env["NODE_PATH"] = str(harness / "node_modules")
    print("RUN: node", script)
    completed = subprocess.run(["node", str(script)], cwd=ROOT, env=env, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    print("PASS: UI-0～UI-1 工作台交互原型验收")


if __name__ == "__main__":
    main()
