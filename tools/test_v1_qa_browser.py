"""V1-QA 浏览器验收：界面质量、状态一致性与智能体工作流展示。

自启 uvicorn（8013-8018），强制关闭 LIVE 开关，不调用付费 API。
截图仅写入 output/playwright/v1-qa-*.png，不得入库。
"""
from __future__ import annotations

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


def _strip_live(env: dict[str, str]) -> dict[str, str]:
    cleaned = dict(env)
    for key in list(cleaned):
        if key.startswith("VISIONCRAFT_ALLOW_LIVE"):
            cleaned.pop(key, None)
    cleaned["VISIONCRAFT_ALLOW_LIVE_LLM"] = "0"
    cleaned["VISIONCRAFT_ALLOW_LIVE_VISION"] = "0"
    cleaned["VISIONCRAFT_ALLOW_LIVE_VIDEO"] = "0"
    return cleaned


def _health_ok(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/api/health", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


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


_BACKEND_LOG_HANDLE = None


def _backend_log_path(port: int) -> Path:
    # The backend used to write into subprocess.PIPE and nothing ever read that
    # pipe. Whenever a step failed, the reason the *backend* gave -- the HTTP
    # status, the traceback -- was unreachable, so the failure could only be
    # described from the browser side ("state did not change"), which conflates
    # "the backend refused" with "the backend was never called". A pipe nobody
    # drains can also fill up and block the server outright. The isolated data
    # directory is the natural home: it already collects this run's other logs.
    data_dir = os.environ.get("VISIONCRAFT_DATA_DIR", "").strip()
    root = Path(data_dir) if data_dir else ROOT / "output" / "playwright" / "stageC"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"backend_{port}.log"


def _start_backend() -> tuple[subprocess.Popen, str]:
    global _BACKEND_LOG_HANDLE
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    exe = str(python if python.exists() else sys.executable)
    env = _strip_live(os.environ.copy())
    for port in range(8013, 8019):
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
        deadline = time.time() + 20
        while time.time() < deadline:
            if proc.poll() is not None:
                _BACKEND_LOG_HANDLE.flush()
                err = log_path.read_text(encoding="utf-8", errors="replace")
                raise SystemExit(f"验收后端启动失败：{err[-500:]}")
            if _health_ok(base):
                print(f"INFO: 验收后端 {base}")
                print(f"INFO: 后端日志 {log_path}")
                return proc, base
            time.sleep(0.3)
        proc.terminate()
        proc.wait(timeout=5)
    raise SystemExit("浏览器验收失败：8013-8018 端口均不可用。")


def main() -> None:
    for key in list(os.environ):
        if key.startswith("VISIONCRAFT_ALLOW_LIVE"):
            os.environ.pop(key, None)
    os.environ["VISIONCRAFT_ALLOW_LIVE_LLM"] = "0"
    os.environ["VISIONCRAFT_ALLOW_LIVE_VISION"] = "0"
    os.environ["VISIONCRAFT_ALLOW_LIVE_VIDEO"] = "0"

    harness = ROOT / ".playwright-cli"
    _ensure_playwright(harness)
    server = None
    try:
        server, base = _start_backend()
        env = _strip_live(os.environ.copy())
        env["NODE_PATH"] = str(harness / "node_modules")
        env["VISIONCRAFT_BASE_URL"] = base
        script = ROOT / "tools" / "v1_qa.cjs"
        print("RUN: node", script)
        print("INFO: live_network=否 cost_cny=0")
        completed = subprocess.run(["node", str(script)], cwd=ROOT, env=env, check=False)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        print("PASS: V1-QA 界面质量与工作流展示验收")
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
            print("CLEANED: 验收后端进程")


if __name__ == "__main__":
    main()
