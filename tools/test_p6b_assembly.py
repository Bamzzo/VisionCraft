"""P6-B 浏览器验收包装：准备本地测试视频项目，再调用 Playwright。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
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

BASE = os.environ.get("VISIONCRAFT_BASE_URL", "http://127.0.0.1:8000")
FFMPEG_AVAILABLE = bool(shutil.which("ffmpeg"))
CREATED: list[str] = []


def _health_ok() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _seed_project(title: str, *, stale: int = 0) -> str:
    project_id = f"p6b_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode,
             status, routing_mode, assembly_stale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, "浏览器合成验收文本", "test", "16:9", 5, "auto", "production_ready", "direct", stale, now, now),
        )
    CREATED.append(project_id)
    return project_id


def _seed_shots(project_id: str, count: int = 3) -> None:
    now = utc_now()
    for index in range(1, count + 1):
        shot_id = f"shot_{uuid.uuid4().hex[:10]}"
        version_id = f"version_{uuid.uuid4().hex[:10]}"
        filename = f"{shot_id}.mp4"
        first_name = f"{shot_id}_first.svg"
        last_name = f"{shot_id}_last.svg"
        video_path = public_asset_path(project_id, filename)
        first_path = public_asset_path(project_id, first_name)
        last_path = public_asset_path(project_id, last_name)
        with connect() as conn:
            conn.execute(
                """INSERT INTO shots
                (id, project_id, shot_index, title, description, characters, scene, camera_motion,
                 visual_prompt, negative_prompt, audio_prompt, status, retry_count, current_version_id,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (shot_id, project_id, index, f"夜路镜头 {index:02d}", "林晚停在旧书店门口。", "[]", "雨夜", "固定",
                 "cinematic", "", "", "video_ready", 0, version_id, now, now),
            )
            conn.execute(
                """INSERT INTO shot_versions
                (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
                 first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (version_id, shot_id, 1, "描述", "cinematic", "", "", first_path, last_path, video_path, "t2v", "ark", "seedance", "test", now),
            )
            conn.execute(
                """INSERT INTO assets
                (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)""",
                (f"asset_{uuid.uuid4().hex[:10]}", project_id, f"镜头 {index:02d} 视频", "本地测试视频", "test",
                 video_path, "provider:ark:seedance", now),
            )
        folder = PROJECTS_DIR / project_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_bytes(b"local-test-video")
        marker = f"<svg xmlns='http://www.w3.org/2000/svg' width='64' height='36'><rect width='64' height='36' fill='#111'/></svg>"
        (folder / first_name).write_text(marker, encoding="utf-8")
        (folder / last_name).write_text(marker, encoding="utf-8")


def _seed_final(project_id: str, label: str) -> None:
    filename = f"final_{uuid.uuid4().hex[:8]}.mp4"
    path = PROJECTS_DIR / project_id / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"local-final-cut")
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, 'final-video', ?, ?, ?, ?, ?, ?)""",
            (f"asset_{uuid.uuid4().hex[:10]}", project_id, f"{label} Final Cut",
             "Assembled final cut from 3 shot videos.", "FFmpeg sequence assembly (video only)",
             public_asset_path(project_id, filename), "provider:ffmpeg:sequence-assembly", now),
        )


def _cleanup() -> None:
    for project_id in CREATED:
        if not project_id.startswith("p6b_"):
            continue
        with connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
        print(f"CLEANED: {project_id}")


def _ensure_playwright(harness: Path) -> None:
    module_dir = harness / "node_modules" / "playwright"
    harness.mkdir(parents=True, exist_ok=True)
    manifest = harness / "package.json"
    if not manifest.exists():
        manifest.write_text('{"name":"visioncraft-playwright","private":true}\n', encoding="utf-8")
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise SystemExit("真实浏览器验收失败：未找到 npm。")
    if not module_dir.exists():
        subprocess.run([npm, "install", "playwright@1.55.1"], cwd=harness, check=True)
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    subprocess.run([npx, "playwright", "install", "chromium"], cwd=harness, check=True)


def main() -> None:
    if not _health_ok():
        raise SystemExit(
            f"真实浏览器验收失败：{BASE} 未响应。请先启动 "
            "`.venv\\Scripts\\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`"
        )
    init_environment()
    init_db()
    ready_id = _seed_project("P6B 就绪项目")
    other_id = _seed_project("P6B 隔离项目")
    complete_id = _seed_project("P6B 成片预览")
    stale_id = _seed_project("P6B 过期成片", stale=1)
    _seed_shots(ready_id)
    _seed_shots(complete_id)
    _seed_shots(stale_id)
    _seed_final(complete_id, "完成夹具")
    _seed_final(stale_id, "过期夹具")
    harness = ROOT / ".playwright-cli"
    _ensure_playwright(harness)
    env = os.environ.copy()
    env["NODE_PATH"] = str(harness / "node_modules")
    env["P6B_READY_ID"] = ready_id
    env["P6B_OTHER_ID"] = other_id
    env["P6B_COMPLETE_ID"] = complete_id
    env["P6B_STALE_ID"] = stale_id
    env["P6B_FFMPEG"] = "1" if FFMPEG_AVAILABLE else "0"
    script = ROOT / "tools" / "p6b_assembly.cjs"
    print("RUN: node", script)
    if not FFMPEG_AVAILABLE:
        print("SKIP: 本机没有 FFmpeg，真实 concat/重新合成不报告为通过")
    try:
        completed = subprocess.run(["node", str(script)], cwd=ROOT, env=env, check=False)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        print("PASS: P6-B 成片工作台浏览器验收")
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
