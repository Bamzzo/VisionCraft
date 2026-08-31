"""本地 Mock 模式人工网页冒烟。禁止真实 API，费用 0 元。"""
from __future__ import annotations

import os
import shutil
import socket
import struct
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
    ensure_process_path,
    ffmpeg_available,
    make_color_clip,
    make_color_clip_with_sine,
    make_sine_wav,
)

JPEG_BYTES = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080808080808080808080808"
    "08080808080808080808080808080808080808080808080808080808080808080808080808"
    "08080808080808080808080808ffc0000b080001000101011100ffc4001410000000000000"
    "00000000000000000000ffc400141000000000000000000000000000000000ffda00080001"
    "0100003f00fb00d2ffd9"
)
PREFIX = "msmoke_"
CREATED: list[str] = []
OUT = ROOT / "output" / "playwright" / "mock-smoke"


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


def tiny_wav() -> bytes:
    frames = 1600
    data = b"\x00\x00" * frames
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(data),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        8000,
        16000,
        2,
        16,
        b"data",
        len(data),
    )
    return header + data


def _cleanup() -> None:
    init_environment()
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title FROM projects WHERE id LIKE ? OR title LIKE ?",
            (f"{PREFIX}%", "Mock冒烟%"),
        ).fetchall()
    ids = [row["id"] for row in rows] + list(CREATED)
    for project_id in dict.fromkeys(ids):
        title_ok = True
        with connect() as conn:
            row = conn.execute("SELECT title FROM projects WHERE id = ?", (project_id,)).fetchone()
            title = str(row["title"] if row else "")
        if str(project_id).startswith("project_") and not title.startswith("Mock冒烟"):
            continue
        delete_project(project_id)
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
        print(f"CLEANED: {project_id}")
    CREATED.clear()


def _seed_other(project_id: str) -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, output_resolution,
             shot_count_mode, status, routing_mode, assembly_stale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, "Mock冒烟隔离乙", "隔离样本", "cinematic clean realism", "16:9", 5, "1280x720",
             "auto", "created", "direct", 0, now, now),
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
             "ark", "local-fixture", "msmoke", now),
        )
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)""",
            (f"asset_{uuid.uuid4().hex[:10]}", project_id, f"镜头 {index}", "夹具", title,
             video_path, "provider:ark:local-fixture", now),
        )


def _seed_ready(project_id: str) -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, output_resolution,
             shot_count_mode, status, routing_mode, assembly_stale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, "Mock冒烟成片夹具", "夹具", "cinematic clean realism", "16:9", 5, "1280x720",
             "auto", "production_ready", "direct", 0, now, now),
        )
    CREATED.append(project_id)
    folder = PROJECTS_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)
    make_color_clip_with_sine(folder / "a.mp4", color="red", size="640x360", duration=1.0, frequency=440)
    make_color_clip(folder / "b.mp4", color="blue", size="1280x720", duration=1.0)
    _insert_shot(project_id, 1, "a.mp4", "有原声")
    _insert_shot(project_id, 2, "b.mp4", "无原声")
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


def _write_fixtures() -> dict[str, Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    jpeg = OUT / "first.jpg"
    jpeg.write_bytes(JPEG_BYTES)
    wav = OUT / "bg.wav"
    wav.write_bytes(tiny_wav())
    srt = OUT / "zh.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\n你好\n", encoding="utf-8")
    return {"jpeg": jpeg, "wav": wav, "srt": srt}


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
    ffmpeg_dir = ROOT.parent / ".tools" / "ffmpeg" / "bin"
    if ffmpeg_dir.is_dir():
        env["VISIONCRAFT_FFMPEG_DIR"] = str(ffmpeg_dir)
    for port in range(8032, 8038):
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
                raise SystemExit(f"冒烟后端启动失败：{err[-500:]}")
            if _health_ok(base):
                print(f"INFO: 冒烟后端 {base}")
                return proc, base
            time.sleep(0.3)
        proc.terminate()
        proc.wait(timeout=5)
    raise SystemExit("冒烟失败：8032-8037 端口均不可用。")


def main() -> None:
    for key in list(os.environ):
        if key.startswith("VISIONCRAFT_ALLOW_LIVE"):
            os.environ.pop(key, None)
    os.environ["VISIONCRAFT_ALLOW_LIVE_LLM"] = "0"
    os.environ["VISIONCRAFT_ALLOW_LIVE_VISION"] = "0"
    os.environ["VISIONCRAFT_ALLOW_LIVE_VIDEO"] = "0"
    ensure_process_path()
    init_environment()
    init_db()
    fixtures = _write_fixtures()
    other_id = f"{PREFIX}other"
    ready_id = f"{PREFIX}ready"
    delete_project(other_id)
    delete_project(ready_id)
    shutil.rmtree(PROJECTS_DIR / other_id, ignore_errors=True)
    shutil.rmtree(PROJECTS_DIR / ready_id, ignore_errors=True)
    _seed_other(other_id)
    has_ffmpeg = ffmpeg_available()
    if has_ffmpeg:
        _seed_ready(ready_id)
    else:
        print("SKIP: 无 FFmpeg，跳过夹具成片合成")
        _seed_other(ready_id)
    harness = ROOT / ".playwright-cli"
    _ensure_playwright(harness)
    server = None
    try:
        server, base = _start_backend()
        env = _strip_live(os.environ.copy())
        env["NODE_PATH"] = str(harness / "node_modules")
        env["VISIONCRAFT_BASE_URL"] = base
        env["MSMOKE_OTHER_ID"] = other_id
        env["MSMOKE_READY_ID"] = ready_id
        env["MSMOKE_HAS_FFMPEG"] = "1" if has_ffmpeg else "0"
        env["MSMOKE_JPEG"] = str(fixtures["jpeg"])
        env["MSMOKE_WAV"] = str(fixtures["wav"])
        env["MSMOKE_SRT"] = str(fixtures["srt"])
        script = ROOT / "tools" / "mock_web_smoke.cjs"
        print("RUN: node", script)
        print("INFO: live_network=否 cost_cny=0")
        completed = subprocess.run(["node", str(script)], cwd=ROOT, env=env, check=False)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        print("PASS: 本地 Mock 网页冒烟")
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
            print("CLEANED: 冒烟后端进程")
        _cleanup()
        for path in fixtures.values():
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
