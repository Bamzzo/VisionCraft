"""无费用浏览器验收：「未保存的章节勾选不跨重绘丢失」。

缺陷的成因在渲染路径上，只有真跑一遍浏览器才看得见：

- 勾选状态原先只活在被重绘覆盖的 DOM 里（`collectMediumScopePayload()` 直接读
  `[data-chapter-check]:checked`）；
- `renderAll()` 是整块替换 `stageWorkspace` 的 innerHTML，而后台任务事件会调用它
  ——一次长文本分析实测 13 次；
- 于是落在重绘窗口里的勾选会消失，用户接着点「保存范围」，提交上去的是空范围。
  **危害是保存了错误的范围，不只是"勾看起来没了"。**

这一条把「重绘真的发生过」也钉住了：重绘前在元素上挂内存标记，重绘后标记必须消失。
否则一个压根没重绘的环境也能让"勾还在"通过，用例就失去分辨力。

语料取工作区根的 `output/test_texts/蛊真人100000字.txt`；缺失时整项 SKIP，绝不当作通过。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import PROJECTS_DIR, init_environment  # noqa: E402
from backend.database import connect, init_db  # noqa: E402
from backend.services.project_service import delete_project  # noqa: E402

CORPUS = ROOT.parent / "output" / "test_texts" / "蛊真人100000字.txt"
PREFIX = "scopedrf_"
CREATED: list[str] = []
_BACKEND_LOG_HANDLE = None


def _health_ok(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/api/health", timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _post(base: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{base}{path}", data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def _backend_log_path(port: int) -> Path:
    data_dir = os.environ.get("VISIONCRAFT_DATA_DIR", "").strip()
    root = Path(data_dir) if data_dir else ROOT / "output" / "playwright" / "stageC"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"backend_scope_draft_{port}.log"


def _start_backend() -> tuple[subprocess.Popen, str]:
    global _BACKEND_LOG_HANDLE
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    exe = str(python if python.exists() else sys.executable)
    env = os.environ.copy()
    for key in ("VISIONCRAFT_ALLOW_LIVE_LLM", "VISIONCRAFT_ALLOW_LIVE_VISION", "VISIONCRAFT_ALLOW_LIVE_VIDEO"):
        env.pop(key, None)
    # 本组自带后端：用一个不与嵌套服务（8013-8018 / 8020-8025）和驱动器（8070+）争抢的区段。
    for port in range(8030, 8040):
        if _port_in_use(port):
            continue
        log_path = _backend_log_path(port)
        _BACKEND_LOG_HANDLE = log_path.open("w", encoding="utf-8")
        proc = subprocess.Popen(
            [exe, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(ROOT),
            env=env,
            stdout=_BACKEND_LOG_HANDLE,
            stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                _BACKEND_LOG_HANDLE.flush()
                err = log_path.read_text(encoding="utf-8", errors="replace")
                raise SystemExit(f"验收后端启动失败：{err[-800:]}")
            if _health_ok(base):
                print(f"INFO: 验收后端 {base}")
                print(f"INFO: 后端日志 {log_path}")
                return proc, base
            time.sleep(0.3)
        proc.terminate()
        proc.wait(timeout=5)
    raise SystemExit("浏览器验收失败：8030-8039 端口均不可用。")


def _seed_long(base: str, title: str, source: str) -> tuple[str, int]:
    created = _post(
        base,
        "/api/projects",
        {
            "title": title,
            "source_text": source,
            "style": "ink-wash fantasy",
            "aspect_ratio": "16:9",
            "duration_seconds": 10,
        },
    )
    project_id = created["id"]
    CREATED.append(project_id)
    _post(base, f"/api/projects/{project_id}/run")
    # POST /run 走 BackgroundTasks：响应先回、流程后跑，紧接着读会拿到 'running'。
    # 必须轮询到终态，否则种入的夹具还没准备好就开始跑浏览器，失败会记在错误的地方。
    state: dict = {}
    deadline = time.time() + 180
    while time.time() < deadline:
        with urllib.request.urlopen(f"{base}/api/projects/{project_id}/medium-text", timeout=60) as response:
            state = json.loads(response.read().decode("utf-8"))
        if state.get("status") == "awaiting_storyline_review":
            break
        if state.get("status") in {"failed", "error"}:
            raise SystemExit(f"长文本分析失败：{state.get('status')!r} {state.get('error')!r}")
        time.sleep(0.5)
    if state.get("status") != "awaiting_storyline_review":
        raise SystemExit(
            f"长文本项目 180s 内未停在范围选择，最后状态 {state.get('status')!r}"
        )
    chapters = state.get("source_chapters") or []
    if len(chapters) < 3:
        raise SystemExit(f"章节树太少（{len(chapters)} 节），用例挑不出中间一节")
    return project_id, len(chapters)


def _cleanup() -> None:
    init_environment()
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT id FROM projects WHERE id LIKE ?", (f"{PREFIX}%",)).fetchall()
    for row in rows:
        delete_project(row["id"])
        shutil.rmtree(PROJECTS_DIR / row["id"], ignore_errors=True)
        print(f"CLEANED: {row['id']}")
    for project_id in CREATED:
        delete_project(project_id)
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
        print(f"CLEANED: {project_id}")


def _ensure_playwright(harness: Path) -> None:
    module_dir = harness / "node_modules" / "playwright"
    harness.mkdir(parents=True, exist_ok=True)
    manifest = harness / "package.json"
    if not manifest.exists():
        manifest.write_text('{"name":"visioncraft-playwright","private":true}\n', encoding="utf-8")
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not module_dir.exists():
        subprocess.run([npm, "install", "playwright@1.55.1"], cwd=harness, check=True)
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    subprocess.run([npx, "playwright", "install", "chromium"], cwd=harness, check=True)


def main() -> None:
    init_environment()
    init_db()
    if not CORPUS.is_file():
        print(f"SKIP: 长文本语料不存在（{CORPUS}），无法验证勾选草稿；这不是通过。")
        return
    source = CORPUS.read_text(encoding="utf-8", errors="replace")
    # B 用一个错开起点的切片：章节边界与 A 不同，切换项目的断言才有分辨力。
    other_source = source[2000:70000]
    harness = ROOT / ".playwright-cli"
    _ensure_playwright(harness)
    server = None
    try:
        server, base = _start_backend()
        project_id, chapter_count = _seed_long(base, f"{PREFIX}勾选草稿A", source)
        other_id, _ = _seed_long(base, f"{PREFIX}勾选草稿B", other_source)
        print(f"INFO: 已种入两个长文本项目（A {chapter_count} 节 / B）")
        env = os.environ.copy()
        env["NODE_PATH"] = str(harness / "node_modules")
        env["VISIONCRAFT_BASE_URL"] = base
        env["SCOPE_DRAFT_PROJECT_ID"] = project_id
        env["SCOPE_DRAFT_OTHER_ID"] = other_id
        env["SCOPE_DRAFT_CHAPTER_COUNT"] = str(chapter_count)
        completed = subprocess.run(["node", str(ROOT / "tools" / "scope_draft_ui.cjs")], cwd=ROOT, env=env, check=False)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        print("PASS: 未保存勾选不跨重绘浏览器验收")
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
        _cleanup()


if __name__ == "__main__":
    main()
