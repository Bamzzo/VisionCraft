"""Mock 浏览器回归：waiting/running 不依赖 #videoModeSelect，完成后才合成。费用 0。"""
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

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db, utc_now
from backend.services.asset_service import public_asset_path
from backend.services.job_service import create_job, update_job
from backend.services.project_service import delete_project

JPEG_BYTES = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080808080808080808080808"
    "08080808080808080808080808080808080808080808080808080808080808080808080808"
    "08080808080808080808080808ffc0000b080001000101011100ffc4001410000000000000"
    "00000000000000000000ffc400141000000000000000000000000000000000ffda00080001"
    "0100003f00fb00d2ffd9"
)
FAKE_MP4 = b"\x00\x00\x00\x1cftypmp42" + b"\x00" * 64
PREFIX = "mvref_"
SAMPLE = "春秋蝉鸣少年归。"
OUT = ROOT / "output" / "playwright" / "mock-video-refresh"


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


def _write_bytes(project_id: str, filename: str, data: bytes) -> str:
    folder = PROJECTS_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_bytes(data)
    return public_asset_path(project_id, filename)


def _insert_project(project_id: str, title: str, status: str) -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, output_resolution,
             shot_count_mode, requested_shot_count, status, routing_mode, assembly_stale,
             generation_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                title,
                SAMPLE,
                "cinematic clean realism",
                "16:9",
                5,
                "1280x720",
                "manual",
                2,
                status,
                "direct",
                0,
                "mock",
                now,
                now,
            ),
        )


def _insert_two_shots(project_id: str, *, ready: bool) -> list[str]:
    now = utc_now()
    frame = _write_bytes(project_id, "first.jpg", JPEG_BYTES)
    shot_ids = []
    with connect() as conn:
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, 'first-frame', ?, ?, ?, ?, ?, ?)""",
            (f"asset_{project_id[-6:]}_ff", project_id, "首帧", "mock jpeg", "local", frame, "provider:local:jpeg", now),
        )
        for index in range(1, 3):
            shot_id = f"shot_{project_id[-6:]}_{index}"
            version_id = f"version_{project_id[-6:]}_{index}"
            has_video = ready
            video_path = _write_bytes(project_id, f"clip_{index}.mp4", FAKE_MP4) if has_video else None
            if not ready and index == 1:
                status = "video_waiting_remote"
            elif has_video:
                status = "video_ready"
            else:
                status = "production_ready"
            conn.execute(
                """INSERT INTO shots
                (id, project_id, shot_index, title, description, characters, scene, camera_motion,
                 visual_prompt, negative_prompt, audio_prompt, status, retry_count, current_version_id,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    shot_id,
                    project_id,
                    index,
                    f"镜头 {index}",
                    "归乡",
                    "[]",
                    "青茅山",
                    "缓推",
                    "cinematic",
                    "",
                    "",
                    status,
                    0,
                    version_id,
                    now,
                    now,
                ),
            )
            conn.execute(
                """INSERT INTO shot_versions
                (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
                 first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    version_id,
                    shot_id,
                    1,
                    f"镜头 {index}",
                    "cinematic",
                    "",
                    "",
                    frame,
                    None,
                    video_path,
                    "i2v",
                    "minimax",
                    "MiniMax-H3",
                    "mvref-fixture",
                    now,
                ),
            )
            if has_video:
                conn.execute(
                    """INSERT INTO assets
                    (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                    VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)""",
                    (
                        f"asset_{project_id[-6:]}_v{index}",
                        project_id,
                        f"镜头 {index} 视频",
                        "mock fixture",
                        "shot",
                        video_path,
                        "provider:mock:local-fixture",
                        now,
                    ),
                )
            shot_ids.append(shot_id)
    return shot_ids


def _insert_running_task(project_id: str, shot_id: str) -> None:
    now = utc_now()
    with connect() as conn:
        version_id = conn.execute("SELECT current_version_id FROM shots WHERE id = ?", (shot_id,)).fetchone()["current_version_id"]
        conn.execute(
            """INSERT INTO video_tasks
            (id, project_id, shot_id, version_id, job_id, provider, model, remote_task_id,
             status, cloud_status, prompt, submit_payload, status_payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"vt_{project_id[-6:]}",
                project_id,
                shot_id,
                version_id,
                None,
                "minimax",
                "MiniMax-H3",
                "mock_remote_wait_0001",
                "running",
                "running",
                "prompt",
                "{}",
                "{}",
                now,
                now,
            ),
        )


def _insert_final(project_id: str) -> None:
    path = _write_bytes(project_id, "final.mp4", FAKE_MP4)
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, 'final-video', ?, ?, ?, ?, ?, ?)""",
            (f"asset_{project_id[-6:]}_final", project_id, "成片夹具", "mock", "final", path, "provider:ffmpeg:local-fixture", now),
        )
        conn.execute(
            "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
            ("completed", now, project_id),
        )


def _cleanup_prefix() -> None:
    init_environment()
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT id FROM projects WHERE id LIKE ?", (f"{PREFIX}%",)).fetchall()
    for row in rows:
        delete_project(row["id"])
        shutil.rmtree(PROJECTS_DIR / row["id"], ignore_errors=True)


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
    env = _strip_live(os.environ.copy())
    for port in range(8050, 8057):
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
                raise SystemExit(f"Mock 回查后端启动失败：{err[-500:]}")
            if _health_ok(base):
                print(f"INFO: mock video-refresh backend {base}")
                return proc, base
            time.sleep(0.3)
        proc.terminate()
        proc.wait(timeout=5)
    raise SystemExit("Mock 回查失败：8050-8056 端口均不可用")


def _seed() -> dict[str, str]:
    waiting = f"{PREFIX}wait"
    ready = f"{PREFIX}ready"
    for project_id in (waiting, ready):
        delete_project(project_id)
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
    _insert_project(waiting, "MVREF 等待云端", "production_ready")
    shots = _insert_two_shots(waiting, ready=False)
    _insert_running_task(waiting, shots[0])
    job_id = create_job(waiting, "video_generation", "等待云端夹具", shot_id=shots[0], stage="waiting_remote")
    update_job(job_id, "waiting_remote", 40, "等待远端返回（本地夹具，未发真实请求）", stage="waiting_remote")
    _insert_project(ready, "MVREF 两镜完成", "completed")
    _insert_two_shots(ready, ready=True)
    _insert_final(ready)
    return {"waiting": waiting, "ready": ready}


def main() -> int:
    for key in list(os.environ):
        if key.startswith("VISIONCRAFT_ALLOW_LIVE"):
            os.environ.pop(key, None)
    os.environ["VISIONCRAFT_ALLOW_LIVE_LLM"] = "0"
    os.environ["VISIONCRAFT_ALLOW_LIVE_VISION"] = "0"
    os.environ["VISIONCRAFT_ALLOW_LIVE_VIDEO"] = "0"
    init_environment()
    init_db()
    _cleanup_prefix()
    ids = _seed()
    harness = ROOT / ".playwright-cli"
    _ensure_playwright(harness)
    OUT.mkdir(parents=True, exist_ok=True)
    server = None
    try:
        server, base = _start_backend()
        env = _strip_live(os.environ.copy())
        env["NODE_PATH"] = str(harness / "node_modules")
        env["VISIONCRAFT_BASE_URL"] = base
        env["MVREF_IDS"] = json.dumps(ids, ensure_ascii=False)
        completed = subprocess.run(["node", str(ROOT / "tools" / "mock_video_refresh.cjs")], cwd=ROOT, env=env, check=False)
        return completed.returncode
    finally:
        if server is not None:
            if os.name == "nt" and server.poll() is None:
                subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"], capture_output=True, check=False)
            else:
                server.terminate()
                try:
                    server.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    server.kill()
        _cleanup_prefix()


if __name__ == "__main__":
    raise SystemExit(main())
