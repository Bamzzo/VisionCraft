"""V1 全链路浏览器验收。不调用付费 API。无 FFmpeg 时成片步骤 SKIP。"""
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
from backend.services.asset_service import public_asset_path
from backend.services.project_service import delete_project
from tools.p6c_ffmpeg import (
    INSTALL_HINT,
    ensure_process_path,
    ffmpeg_available,
    make_color_clip,
    make_color_clip_with_sine,
    make_sine_wav,
)

CREATED: list[str] = []
E2E_PREFIX = "v1e2e_"


def pass_(msg: str) -> None:
    print(f"PASS: {msg}")


def skip(msg: str) -> None:
    print(f"SKIP: {msg}")


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
        with urllib.request.urlopen(f"{base}/api/health", timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _cleanup() -> None:
    init_environment()
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title FROM projects WHERE id LIKE ? OR title LIKE ?",
            (f"{E2E_PREFIX}%", "V1E2E%"),
        ).fetchall()
    for row in rows:
        project_id = row["id"]
        if not (str(project_id).startswith(E2E_PREFIX) or str(project_id).startswith("project_")):
            continue
        if str(project_id).startswith("project_") and not str(row["title"] or "").startswith("V1E2E"):
            continue
        delete_project(project_id)
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
        print(f"CLEANED: {project_id}")
    for project_id in CREATED:
        if project_id.startswith(E2E_PREFIX):
            delete_project(project_id)
            shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
            print(f"CLEANED: {project_id}")


def _seed_project(project_id: str, title: str) -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, output_resolution,
             shot_count_mode, status, routing_mode, assembly_stale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, "V1E2E 隔离样本", "cinematic clean realism", "16:9", 5, "1280x720",
             "auto", "production_ready", "direct", 0, now, now),
        )
    CREATED.append(project_id)


def _insert_shot(project_id: str, index: int, filename: str, title: str) -> None:
    now = utc_now()
    shot_id = f"shot_{uuid.uuid4().hex[:10]}"
    version_id = f"version_{uuid.uuid4().hex[:10]}"
    video_path = public_asset_path(project_id, filename)
    with connect() as conn:
        conn.execute(
            """INSERT INTO shots
            (id, project_id, shot_index, title, description, characters, scene, camera_motion,
             visual_prompt, negative_prompt, audio_prompt, status, retry_count, current_version_id,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (shot_id, project_id, index, title, title, "[]", "色块", "固定", "color", "", "",
             "video_ready", 0, version_id, now, now),
        )
        conn.execute(
            """INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
             first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, shot_id, 1, title, "color", "", "", None, None, video_path, "t2v",
             "ark", "local-fixture", "v1e2e", now),
        )
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)""",
            (f"asset_{uuid.uuid4().hex[:10]}", project_id, f"镜头 {index}", "夹具", title,
             video_path, "provider:ark:local-fixture", now),
        )


def _seed_ready(project_id: str) -> None:
    folder = PROJECTS_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)
    make_color_clip_with_sine(folder / "a.mp4", color="red", size="640x360", duration=1.2, frequency=440)
    make_color_clip_with_sine(folder / "b.mp4", color="blue", size="1280x720", duration=1.4, frequency=880)
    make_color_clip(folder / "c.mp4", color="green", size="640x360", duration=1.0)
    make_color_clip_with_sine(folder / "d.mp4", color="yellow", size="1280x720", duration=1.2, frequency=520)
    _insert_shot(project_id, 1, "a.mp4", "440Hz")
    _insert_shot(project_id, 2, "b.mp4", "880Hz")
    _insert_shot(project_id, 3, "c.mp4", "无音轨")
    _insert_shot(project_id, 4, "d.mp4", "520Hz")
    make_sine_wav(folder / "bg.wav", duration=1.0, frequency=220)
    audio_path = public_asset_path(project_id, "bg.wav")
    with connect() as conn:
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, 'audio', ?, ?, ?, ?, ?, ?)""",
            (f"asset_{uuid.uuid4().hex[:10]}", project_id, "背景正弦音", "sine", "sine", audio_path,
             "provider:ffmpeg:local-audio", utc_now()),
        )


def _ensure_playwright(harness: Path) -> None:
    module_dir = harness / "node_modules" / "playwright"
    harness.mkdir(parents=True, exist_ok=True)
    manifest = harness / "package.json"
    if not manifest.exists():
        manifest.write_text('{"name":"visioncraft-playwright","private":true}\n', encoding="utf-8")
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise SystemExit("浏览器验收失败：未找到 npm。")
    if not module_dir.exists():
        subprocess.run([npm, "install", "playwright@1.55.1"], cwd=harness, check=True)
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    subprocess.run([npx, "playwright", "install", "chromium"], cwd=harness, check=True)


def _start_backend() -> tuple[subprocess.Popen, str]:
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    exe = str(python if python.exists() else sys.executable)
    env = _strip_live(os.environ.copy())
    for port in range(8013, 8019):
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
    raise SystemExit("浏览器验收失败：8013-8018 端口均不可用。")


def main() -> None:
    ensure_process_path()
    has_ffmpeg = ffmpeg_available()
    init_environment()
    init_db()
    ready_id = f"{E2E_PREFIX}ready"
    other_id = f"{E2E_PREFIX}other"
    delete_project(ready_id)
    delete_project(other_id)
    shutil.rmtree(PROJECTS_DIR / ready_id, ignore_errors=True)
    shutil.rmtree(PROJECTS_DIR / other_id, ignore_errors=True)
    _seed_project(other_id, "V1E2E 隔离项目")
    if has_ffmpeg:
        _seed_project(ready_id, "V1E2E 成片夹具")
        _seed_ready(ready_id)
    else:
        skip("成片预览、下载、真实合成与过期重合成（无 FFmpeg）")
        skip("未生成 output/playwright/v1-assembly-*.png")
        print(INSTALL_HINT)
        _seed_project(ready_id, "V1E2E 成片夹具（无视频）")
    harness = ROOT / ".playwright-cli"
    _ensure_playwright(harness)
    server = None
    try:
        server, base = _start_backend()
        env = _strip_live(os.environ.copy())
        env["NODE_PATH"] = str(harness / "node_modules")
        env["VISIONCRAFT_BASE_URL"] = base
        env["V1_READY_ID"] = ready_id
        env["V1_OTHER_ID"] = other_id
        env["V1_HAS_FFMPEG"] = "1" if has_ffmpeg else "0"
        script = ROOT / "tools" / "v1_demo.cjs"
        print("RUN: node", script)
        print("INFO: live_network=否 cost_cny=0")
        completed = subprocess.run(["node", str(script)], cwd=ROOT, env=env, check=False)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        print("PASS: V1 全链路浏览器验收")
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
            print("CLEANED: 验收后端进程")
        _cleanup()


if __name__ == "__main__":
    main()
