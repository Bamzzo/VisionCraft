"""P6-C 真实 FFmpeg 浏览器闭环。无 FFmpeg 时 SKIP，且不写 p6c-real-*.png。"""
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
from tools.p6c_ffmpeg import INSTALL_HINT, ensure_process_path, ffmpeg_available, make_color_clip
from tools.test_p6c_real_assembly import CLIP_SPECS

BASE = os.environ.get("VISIONCRAFT_BASE_URL", "http://127.0.0.1:8000")
CREATED: list[str] = []


def _health_ok() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _cleanup() -> None:
    for project_id in CREATED:
        if not str(project_id).startswith("p6c_"):
            continue
        with connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
        print(f"CLEANED: {project_id}")


def _seed_project(title: str) -> str:
    project_id = f"p6c_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode,
             status, routing_mode, assembly_stale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, "P6-C 浏览器真实合成", "cinematic clean realism", "16:9", 5, "auto",
             "production_ready", "direct", 0, now, now),
        )
    CREATED.append(project_id)
    return project_id


def _seed_real_shots(project_id: str, extra_version_on: int | None = 2) -> dict:
    folder = PROJECTS_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    alt = {}
    for spec in CLIP_SPECS:
        shot_id = f"shot_{uuid.uuid4().hex[:10]}"
        version_id = f"version_{uuid.uuid4().hex[:10]}"
        filename = f"{shot_id}.mp4"
        local = folder / filename
        make_color_clip(local, color=spec["color"], size=spec["size"], duration=spec["duration"])
        video_path = public_asset_path(project_id, filename)
        first = public_asset_path(project_id, f"{shot_id}_first.svg")
        last = public_asset_path(project_id, f"{shot_id}_last.svg")
        (folder / f"{shot_id}_first.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
        (folder / f"{shot_id}_last.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
        with connect() as conn:
            conn.execute(
                """INSERT INTO shots
                (id, project_id, shot_index, title, description, characters, scene, camera_motion,
                 visual_prompt, negative_prompt, audio_prompt, status, retry_count, current_version_id,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (shot_id, project_id, spec["index"], f"色块镜头 {spec['index']:02d}", spec["color"], "[]", "色块",
                 "固定", "color", "", "", "video_ready", 0, version_id, now, now),
            )
            conn.execute(
                """INSERT INTO shot_versions
                (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
                 first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (version_id, shot_id, 1, spec["color"], "color", "", "", first, last, video_path, "t2v",
                 "ark", "local-fixture", "p6c", now),
            )
            conn.execute(
                """INSERT INTO assets
                (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)""",
                (f"asset_{uuid.uuid4().hex[:10]}", project_id, f"镜头 {spec['index']:02d}", "真实夹具",
                 spec["color"], video_path, "provider:ark:local-fixture", now),
            )
        if extra_version_on == spec["index"]:
            alt_name = f"{shot_id}_v2.mp4"
            make_color_clip(folder / alt_name, color="white", size="960x540", duration=1.3)
            alt_path = public_asset_path(project_id, alt_name)
            alt_id = f"version_{uuid.uuid4().hex[:10]}"
            with connect() as conn:
                conn.execute(
                    """INSERT INTO shot_versions
                    (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
                     first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (alt_id, shot_id, 2, "white", "color", "", "", first, last, alt_path, "t2v",
                     "ark", "local-fixture", "p6c", now),
                )
                conn.execute(
                    """INSERT INTO assets
                    (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                    VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)""",
                    (f"asset_{uuid.uuid4().hex[:10]}", project_id, "替换镜头", "白色", "white", alt_path,
                     "provider:ark:local-fixture", now),
                )
            alt = {"shot_id": shot_id, "alt_version_id": alt_id}
    return alt


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
    ensure_process_path()
    if not ffmpeg_available():
        print("SKIP: 浏览器真实 FFmpeg 合成/预览/下载")
        print("SKIP: 未生成 output/playwright/p6c-real-*.png（禁止用夹具伪装真实成片）")
        print(INSTALL_HINT)
        return
    if not _health_ok():
        raise SystemExit(
            f"真实浏览器验收失败：{BASE} 未响应。请先启动 "
            "`.venv\\Scripts\\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`"
        )
    init_environment()
    init_db()
    ready_id = _seed_project("P6C 真实合成")
    other_id = _seed_project("P6C 隔离项目")
    alt = _seed_real_shots(ready_id)
    harness = ROOT / ".playwright-cli"
    _ensure_playwright(harness)
    env = os.environ.copy()
    env["NODE_PATH"] = str(harness / "node_modules")
    env["P6C_READY_ID"] = ready_id
    env["P6C_OTHER_ID"] = other_id
    env["P6C_SHOT_ID"] = alt.get("shot_id", "")
    env["P6C_ALT_VERSION"] = alt.get("alt_version_id", "")
    script = ROOT / "tools" / "p6c_real_assembly.cjs"
    print("RUN: node", script)
    try:
        completed = subprocess.run(["node", str(script)], cwd=ROOT, env=env, check=False)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        print("PASS: P6-C 真实 FFmpeg 浏览器闭环")
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
