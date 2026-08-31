"""P8-B 浏览器验收：项目内图片/音频/字幕上传。

自启 uvicorn（8026-8031），强制关闭 LIVE 开关，不调用付费 API。
截图仅写入 output/playwright/p8b-assets/，不得入库。
"""
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
from backend.services.asset_service import persist_uploaded_asset
from backend.services.project_service import delete_project

JPEG_BYTES = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080808080808080808080808"
    "08080808080808080808080808080808080808080808080808080808080808080808080808"
    "08080808080808080808080808ffc0000b080001000101011100ffc4001410000000000000"
    "00000000000000000000ffc400141000000000000000000000000000000000ffda00080001"
    "0100003f00fb00d2ffd9"
)
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)
PREFIX = "p8bui_"
CREATED: list[str] = []
OUT = ROOT / "output" / "playwright" / "p8b-assets"


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


def tiny_wav(duration: float = 0.2, rate: int = 8000) -> bytes:
    frames = int(rate * duration)
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
        rate,
        rate * 2,
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
        rows = conn.execute("SELECT id FROM projects WHERE id LIKE ?", (f"{PREFIX}%",)).fetchall()
    ids = [row["id"] for row in rows] + list(CREATED)
    for project_id in dict.fromkeys(ids):
        delete_project(project_id)
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
        print(f"CLEANED: {project_id}")
    CREATED.clear()


def _seed_project(title: str) -> str:
    init_environment()
    init_db()
    project_id = f"{PREFIX}{uuid.uuid4().hex[:10]}"
    now = utc_now()
    shot_id = f"shot_{uuid.uuid4().hex[:10]}"
    version_id = f"version_{uuid.uuid4().hex[:10]}"
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, output_resolution,
             shot_count_mode, status, routing_mode, assembly_stale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, "春秋蝉鸣少年归", "cinematic clean realism", "16:9", 5, "1280x720",
             "auto", "production_ready", "direct", 0, now, now),
        )
        conn.execute(
            """INSERT INTO shots
            (id, project_id, shot_index, title, description, characters, scene, camera_motion,
             visual_prompt, negative_prompt, audio_prompt, status, retry_count, current_version_id,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (shot_id, project_id, 1, "上传镜头", "d", "[]", "s", "固定", "p", "", "", "draft", 0, version_id, now, now),
        )
        conn.execute(
            """INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
             first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, shot_id, 1, "d", "p", "", "", None, None, None, "i2v", "minimax", "MiniMax-H3", "test", now),
        )
    persist_uploaded_asset(
        project_id,
        asset_type="final-video",
        asset_role="final_video",
        name="placeholder-final",
        content=b"ftypisomplaceholder",
        suffix=".mp4",
        mime_type="video/mp4",
    )
    CREATED.append(project_id)
    return project_id


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


def _start_backend() -> tuple[subprocess.Popen, str]:
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    exe = str(python if python.exists() else sys.executable)
    env = _strip_live(os.environ.copy())
    for port in range(8026, 8032):
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
    raise SystemExit("浏览器验收失败：8026-8031 端口均不可用。")


def _write_fixtures() -> dict[str, Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    jpeg_src = ROOT.parent / "gyfy.jpg"
    jpeg = OUT / "gyfy.jpg"
    if jpeg_src.is_file():
        shutil.copyfile(jpeg_src, jpeg)
    else:
        jpeg.write_bytes(JPEG_BYTES)
        print("SKIP: 未找到仓库旁 gyfy.jpg，改用 1×1 JPEG 夹具")
    png = OUT / "ref.png"
    png.write_bytes(PNG_BYTES)
    wav = OUT / "bg.wav"
    wav.write_bytes(tiny_wav())
    srt = OUT / "zh.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\n你好\n", encoding="utf-8")
    return {"jpeg": jpeg, "png": png, "wav": wav, "srt": srt}


def main() -> None:
    for key in list(os.environ):
        if key.startswith("VISIONCRAFT_ALLOW_LIVE"):
            os.environ.pop(key, None)
    os.environ["VISIONCRAFT_ALLOW_LIVE_LLM"] = "0"
    os.environ["VISIONCRAFT_ALLOW_LIVE_VISION"] = "0"
    os.environ["VISIONCRAFT_ALLOW_LIVE_VIDEO"] = "0"

    init_environment()
    init_db()
    fixtures = _write_fixtures()
    project_a = _seed_project("P8B上传甲")
    project_b = _seed_project("P8B上传乙")
    harness = ROOT / ".playwright-cli"
    _ensure_playwright(harness)
    server = None
    try:
        server, base = _start_backend()
        env = _strip_live(os.environ.copy())
        env["NODE_PATH"] = str(harness / "node_modules")
        env["VISIONCRAFT_BASE_URL"] = base
        env["P8B_PROJECT_A"] = project_a
        env["P8B_PROJECT_B"] = project_b
        env["P8B_JPEG"] = str(fixtures["jpeg"])
        env["P8B_PNG"] = str(fixtures["png"])
        env["P8B_WAV"] = str(fixtures["wav"])
        env["P8B_SRT"] = str(fixtures["srt"])
        script = ROOT / "tools" / "p8b_assets.cjs"
        print("RUN: node", script)
        print("INFO: live_network=否 cost_cny=0")
        completed = subprocess.run(["node", str(script)], cwd=ROOT, env=env, check=False)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        print("PASS: P8-B 浏览器素材上传验收")
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
            print("CLEANED: 验收后端进程")
        _cleanup()
        for path in fixtures.values():
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
