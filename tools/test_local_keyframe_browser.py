"""Browser test for local JPEG first-frame registration. No paid API calls."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db, utc_now
from backend.services.project_service import delete_project

JPEG_BYTES = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080808080808080808080808"
    "08080808080808080808080808080808080808080808080808080808080808080808080808"
    "08080808080808080808080808ffc0000b080001000101011100ffc4001410000000000000"
    "00000000000000000000ffc400141000000000000000000000000000000000ffda00080001"
    "0100003f00fb00d2ffd9"
)
E2E_PREFIX = "p7kf_"
CREATED: list[str] = []


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


def _cleanup(jpeg_path: Path | None = None) -> None:
    init_environment()
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title FROM projects WHERE id LIKE ? OR title LIKE ?",
            (f"{E2E_PREFIX}%", "护栏E2E%"),
        ).fetchall()
    for row in rows:
        delete_project(row["id"])
        shutil.rmtree(PROJECTS_DIR / row["id"], ignore_errors=True)
        print(f"CLEANED: {row['id']}")
    for project_id in CREATED:
        delete_project(project_id)
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
        print(f"CLEANED: {project_id}")
    if jpeg_path:
        jpeg_path.unlink(missing_ok=True)


def _seed_other(project_id: str) -> None:
    now = utc_now()
    shot_id = f"shot_{uuid.uuid4().hex[:10]}"
    version_id = f"version_{uuid.uuid4().hex[:10]}"
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, output_resolution,
             shot_count_mode, status, routing_mode, assembly_stale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, "护栏E2E 隔离项目", "春秋蝉鸣少年归", "cinematic clean realism", "16:9", 5, "1280x720",
             "auto", "production_ready", "direct", 0, now, now),
        )
        conn.execute(
            """INSERT INTO shots
            (id, project_id, shot_index, title, description, characters, scene, camera_motion,
             visual_prompt, negative_prompt, audio_prompt, status, retry_count, current_version_id,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (shot_id, project_id, 1, "隔离镜头", "d", "[]", "s", "固定", "p", "", "", "draft", 0, version_id, now, now),
        )
        conn.execute(
            """INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
             first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, shot_id, 1, "d", "p", "", "", None, None, None, "i2v", "minimax", "MiniMax-H3", "test", now),
        )
    CREATED.append(project_id)


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


def _start_backend() -> tuple[subprocess.Popen, str]:
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    exe = str(python if python.exists() else sys.executable)
    env = os.environ.copy()
    env.pop("VISIONCRAFT_ALLOW_LIVE_LLM", None)
    env.pop("VISIONCRAFT_ALLOW_LIVE_VISION", None)
    env.pop("VISIONCRAFT_ALLOW_LIVE_VIDEO", None)
    for port in range(8020, 8026):
        if _port_in_use(port):
            continue
        proc = subprocess.Popen(
            [exe, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 20
        while time.time() < deadline:
            if proc.poll() is not None:
                err = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
                raise SystemExit(f"验收后端启动失败：{err[-500:]}")
            if _health_ok(base):
                print(f"INFO: 验收后端 {base}")
                return proc, base
            time.sleep(0.3)
        proc.terminate()
        proc.wait(timeout=5)
    raise SystemExit("浏览器验收失败：8020-8025 端口均不可用。")


def main() -> None:
    init_environment()
    init_db()
    other_id = f"{E2E_PREFIX}other"
    delete_project(other_id)
    shutil.rmtree(PROJECTS_DIR / other_id, ignore_errors=True)
    _seed_other(other_id)
    jpeg_path = ROOT / "output" / "playwright" / "_tmp_local_keyframe.jpg"
    jpeg_path.parent.mkdir(parents=True, exist_ok=True)
    jpeg_path.write_bytes(JPEG_BYTES)
    harness = ROOT / ".playwright-cli"
    _ensure_playwright(harness)
    server = None
    try:
        server, base = _start_backend()
        env = os.environ.copy()
        env["NODE_PATH"] = str(harness / "node_modules")
        env["VISIONCRAFT_BASE_URL"] = base
        env["V1_OTHER_ID"] = other_id
        env["LOCAL_JPEG_PATH"] = str(jpeg_path)
        completed = subprocess.run(["node", str(ROOT / "tools" / "local_keyframe_ui.cjs")], cwd=ROOT, env=env, check=False)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        print("PASS: 本地首帧登记浏览器验收")
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
        _cleanup(jpeg_path)


if __name__ == "__main__":
    main()
