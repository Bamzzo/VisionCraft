"""P7-C 无费用浏览器证据：阶段状态一致性与截图哈希。

自启 uvicorn（8013-8018），强制关闭 LIVE 开关，只使用 Mock / 本地夹具。
截图写入 output/playwright/p7c-ui-state/，不得入库。只清理 p7cui_ 前缀临时项目。
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
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db, to_json, utc_now
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

PREFIX = "p7cui_"
CREATED: list[str] = []
SAMPLE = (
    "方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。"
    "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。"
    "但是族中长老已经设下阻碍，他只能选择冒险一搏。"
    "最终他停在山门前，留下未说完的话。"
)
LONG_TITLE = "P7C超长中文标题用于检查换行省略与错位-影视创作工作台春秋蝉鸣少年归" * 2
LONG_MODEL = "MiniMax-Hailuo-02-超长模型名用于检查换行与遮挡-doubao-seedance-1-0-pro-fast"


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
            "SELECT id FROM projects WHERE id LIKE ?",
            (f"{PREFIX}%",),
        ).fetchall()
    for row in rows:
        project_id = row["id"]
        delete_project(project_id)
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
        print(f"CLEANED: {project_id}")
    for project_id in CREATED:
        if str(project_id).startswith(PREFIX):
            delete_project(project_id)
            shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)


def _write_jpeg(project_id: str, filename: str = "first.jpg") -> str:
    folder = PROJECTS_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_bytes(JPEG_BYTES)
    return public_asset_path(project_id, filename)


def _write_video(project_id: str, filename: str) -> str:
    folder = PROJECTS_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    try:
        from tools.p6c_ffmpeg import ffmpeg_available, make_color_clip

        if ffmpeg_available():
            make_color_clip(path, color="red", size="640x360", duration=0.4)
        else:
            path.write_bytes(b"p7c-local-fixture-mp4")
    except Exception:
        path.write_bytes(b"p7c-local-fixture-mp4")
    return public_asset_path(project_id, filename)


def _insert_project(project_id: str, title: str, status: str, **extra) -> None:
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
                extra.get("shot_count", 5),
                status,
                "direct",
                extra.get("assembly_stale", 0),
                "mock",
                now,
                now,
            ),
        )
    CREATED.append(project_id)


def _insert_option(project_id: str) -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO adaptation_options
            (id, project_id, option_index, title, rationale, protagonist_goal, conflict,
             ending_orientation, suggested_duration_seconds, suggested_shot_count, source_excerpt, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"opt_{uuid.uuid4().hex[:8]}",
                project_id,
                1,
                "春秋蝉归乡",
                "保留少年归乡与传承冲突。",
                "带回春秋蝉",
                "族中长老阻拦",
                "未说完的话",
                30,
                5,
                SAMPLE[:40],
                now,
            ),
        )


def _insert_bible(project_id: str, confirmed: bool = False) -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO story_bibles
            (project_id, summary, worldview, style_tags, themes, adaptation_summary,
             protagonist, visual_style, review_status, character_cards_json, scene_cards_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "青茅山夜路，少年要带回春秋蝉。",
                "蛊修世界",
                to_json(["cinematic", "night"]),
                to_json(["归乡", "传承"]),
                "方源走夜路，听见争夺呼喊。",
                "方源",
                "cinematic clean realism",
                "confirmed" if confirmed else "draft",
                to_json([{"name": "方源", "identity": "少年", "appearance": "青衫", "motivation": "取蝉", "invariant": "冷静"}]),
                to_json([{"name": "青茅山夜路", "environment": "山道", "time": "夜", "visuals": "冷月", "invariant": "雾"}]),
                now,
            ),
        )


def _insert_drafts(project_id: str, count: int = 5) -> None:
    now = utc_now()
    with connect() as conn:
        for index in range(1, count + 1):
            conn.execute(
                """INSERT INTO storyboard_drafts
                (id, project_id, shot_index, title, narrative_purpose, characters, scene, action_text,
                 camera_motion, duration_seconds, visual_prompt, source_excerpt, source_type, review_status,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"draft_{uuid.uuid4().hex[:8]}",
                    project_id,
                    index,
                    f"镜头 {index} · 夜路",
                    "推进归乡",
                    to_json(["方源"]),
                    "青茅山",
                    "少年停步听见呼喊",
                    "缓推",
                    5,
                    "night mountain path cinematic",
                    SAMPLE[:24],
                    "auto_draft",
                    "draft",
                    now,
                    now,
                ),
            )


def _insert_image_asset(project_id: str, path: str) -> str:
    asset_id = f"asset_{uuid.uuid4().hex[:10]}"
    with connect() as conn:
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, 'first-frame', ?, ?, ?, ?, ?, ?)""",
            (asset_id, project_id, "本地首帧夹具", "p7c jpeg", "local jpeg", path, "provider:local:jpeg", utc_now()),
        )
    return asset_id


def _insert_shots(
    project_id: str,
    count: int,
    *,
    ready: int = 0,
    first_frame: bool = True,
    waiting: bool = False,
    model: str = LONG_MODEL,
) -> list[str]:
    now = utc_now()
    frame_path = _write_jpeg(project_id) if first_frame else None
    if first_frame:
        _insert_image_asset(project_id, frame_path)
    shot_ids = []
    with connect() as conn:
        for index in range(1, count + 1):
            shot_id = f"shot_{uuid.uuid4().hex[:10]}"
            version_id = f"version_{uuid.uuid4().hex[:10]}"
            has_video = index <= ready
            video_path = _write_video(project_id, f"clip_{index}.mp4") if has_video else None
            if waiting and index == ready + 1:
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
                    f"镜头 {index} · 青茅山夜路",
                    f"镜头 {index} 归乡动作",
                    "[]",
                    "青茅山",
                    "缓推",
                    "cinematic night path",
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
                    "cinematic night path",
                    "",
                    "",
                    frame_path,
                    None,
                    video_path,
                    "i2v",
                    "ark" if has_video else "minimax",
                    model,
                    "p7c-fixture",
                    now,
                ),
            )
            if has_video:
                conn.execute(
                    """INSERT INTO assets
                    (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                    VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)""",
                    (
                        f"asset_{uuid.uuid4().hex[:10]}",
                        project_id,
                        f"镜头 {index} 视频",
                        "local fixture",
                        f"shot {index}",
                        video_path,
                        "provider:ark:local-fixture",
                        now,
                    ),
                )
            shot_ids.append(shot_id)
    return shot_ids


def _insert_final(project_id: str, stale: int = 0) -> None:
    path = _write_video(project_id, "final.mp4")
    with connect() as conn:
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, 'final-video', ?, ?, ?, ?, ?, ?)""",
            (
                f"asset_{uuid.uuid4().hex[:10]}",
                project_id,
                "成片夹具",
                "from 5 shots",
                "p7c final",
                path,
                "provider:ffmpeg:local-fixture",
                utc_now(),
            ),
        )
        conn.execute(
            "UPDATE projects SET assembly_stale = ?, status = ?, updated_at = ? WHERE id = ?",
            (stale, "completed" if not stale else "production_ready", utc_now(), project_id),
        )


def _insert_vision_review(project_id: str, asset_id: str) -> None:
    blob = to_json(
        {
            "result": {
                "description": "本地视觉检查占位：首帧（角色 first_frame）。未调用远程视觉模型。",
                "quality_notes": ["当前为本地模拟结果，等待人工确认后再发起真实视觉调用。"],
                "source": "mock_vision",
            },
            "lineage": {"provider": "mock", "model": "local-fixture", "source": "mock_vision"},
        }
    )
    with connect() as conn:
        conn.execute(
            """INSERT INTO vision_reviews
            (id, project_id, asset_id, asset_role, provider, model, transport_mode, mime_type,
             width, height, byte_size, request_id, result_json, used_local_fallback, generation_mode,
             source, config_source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"vr_{uuid.uuid4().hex[:10]}",
                project_id,
                asset_id,
                "first_frame",
                "mock",
                "local-fixture",
                "local",
                "image/jpeg",
                1,
                1,
                len(JPEG_BYTES),
                None,
                blob,
                0,
                "mock",
                "mock_vision",
                "fixture",
                utc_now(),
            ),
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


def _seed_all() -> dict[str, str]:
    ids = {
        "created": f"{PREFIX}created",
        "adaptation": f"{PREFIX}adapt",
        "story_bible": f"{PREFIX}bible",
        "storyboard": f"{PREFIX}board",
        "first_frame": f"{PREFIX}frame",
        "vision_review": f"{PREFIX}vision",
        "video_partial": f"{PREFIX}vpart",
        "video_complete": f"{PREFIX}vfull",
        "assembly_running": f"{PREFIX}asrun",
        "assembly_complete": f"{PREFIX}asdone",
        "download_ready": f"{PREFIX}asdone",
        "assembly_stale": f"{PREFIX}stale",
        "other": f"{PREFIX}other",
    }
    for project_id in dict.fromkeys(ids.values()):
        delete_project(project_id)
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)

    _insert_project(ids["created"], LONG_TITLE, "created", shot_count=5)
    _insert_project(ids["other"], "P7C隔离项目乙", "created", shot_count=5)

    _insert_project(ids["adaptation"], "P7C改编等待审核", "awaiting_scope_review")
    _insert_option(ids["adaptation"])

    _insert_project(ids["story_bible"], "P7C Story Bible 已确认", "awaiting_storyboard_review")
    _insert_option(ids["story_bible"])
    _insert_bible(ids["story_bible"], confirmed=True)
    _insert_drafts(ids["story_bible"], 5)

    _insert_project(ids["storyboard"], "P7C分镜已确认", "production_ready")
    _insert_bible(ids["storyboard"], confirmed=True)
    _insert_shots(ids["storyboard"], 5, ready=0, first_frame=False)

    _insert_project(ids["first_frame"], "P7C关键帧首帧", "production_ready")
    _insert_bible(ids["first_frame"], confirmed=True)
    _insert_shots(ids["first_frame"], 5, ready=0, first_frame=True)

    _insert_project(ids["vision_review"], "P7C视觉检查夹具", "production_ready")
    _insert_bible(ids["vision_review"], confirmed=True)
    _insert_shots(ids["vision_review"], 5, ready=0, first_frame=True)
    with connect() as conn:
        asset = conn.execute(
            "SELECT id FROM assets WHERE project_id = ? AND type = 'first-frame' LIMIT 1",
            (ids["vision_review"],),
        ).fetchone()
    _insert_vision_review(ids["vision_review"], asset["id"])

    _insert_project(ids["video_partial"], "P7C部分镜头视频", "production_ready")
    _insert_bible(ids["video_partial"], confirmed=True)
    _insert_shots(ids["video_partial"], 5, ready=2, first_frame=True, waiting=True)
    job_id = create_job(ids["video_partial"], "video_generation", "等待云端夹具", shot_id=None, stage="waiting_remote")
    update_job(job_id, "waiting_remote", 40, "等待远端返回（本地夹具，未发真实请求）", stage="waiting_remote")

    _insert_project(ids["video_complete"], "P7C五镜视频完成", "video_ready")
    _insert_bible(ids["video_complete"], confirmed=True)
    _insert_shots(ids["video_complete"], 5, ready=5, first_frame=True)

    _insert_project(ids["assembly_running"], "P7C成片合成中", "production_ready")
    _insert_bible(ids["assembly_running"], confirmed=True)
    _insert_shots(ids["assembly_running"], 5, ready=5, first_frame=True)
    job_id = create_job(ids["assembly_running"], "sequence_assembly", "正在合成成片夹具", stage="running")
    update_job(job_id, "running", 55, "正在按镜头顺序合成（本地夹具）", stage="running")

    _insert_project(ids["assembly_complete"], "P7C成片可下载", "completed")
    _insert_bible(ids["assembly_complete"], confirmed=True)
    _insert_shots(ids["assembly_complete"], 5, ready=5, first_frame=True)
    _insert_final(ids["assembly_complete"], stale=0)

    _insert_project(ids["assembly_stale"], "P7C成片已过期", "production_ready")
    _insert_bible(ids["assembly_stale"], confirmed=True)
    _insert_shots(ids["assembly_stale"], 5, ready=5, first_frame=True)
    _insert_final(ids["assembly_stale"], stale=1)
    return ids


def main() -> None:
    for key in list(os.environ):
        if key.startswith("VISIONCRAFT_ALLOW_LIVE"):
            os.environ.pop(key, None)
    os.environ["VISIONCRAFT_ALLOW_LIVE_LLM"] = "0"
    os.environ["VISIONCRAFT_ALLOW_LIVE_VISION"] = "0"
    os.environ["VISIONCRAFT_ALLOW_LIVE_VIDEO"] = "0"

    init_environment()
    init_db()
    ids = _seed_all()
    harness = ROOT / ".playwright-cli"
    _ensure_playwright(harness)
    server = None
    try:
        server, base = _start_backend()
        env = _strip_live(os.environ.copy())
        env["NODE_PATH"] = str(harness / "node_modules")
        env["VISIONCRAFT_BASE_URL"] = base
        env["P7C_IDS"] = json.dumps(ids, ensure_ascii=False)
        script = ROOT / "tools" / "p7c_ui_state.cjs"
        print("RUN: node", script)
        print("INFO: live_network=否 cost_cny=0")
        completed = subprocess.run(["node", str(script)], cwd=ROOT, env=env, check=False)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        out = ROOT / "output" / "playwright" / "p7c-ui-state"
        for name in ("browser_evidence.json", "browser_dom_snapshots.json", "browser_screenshot_hashes.json"):
            path = out / name
            if not path.exists():
                raise SystemExit(f"缺少证据文件：{path}")
        hashes = json.loads((out / "browser_screenshot_hashes.json").read_text(encoding="utf-8"))
        ordered = hashes.get("ordered") or []
        for prev, curr in zip(ordered, ordered[1:]):
            if prev["sha256"] == curr["sha256"] and not curr.get("allow_same"):
                raise SystemExit(f"相邻截图哈希相同：{prev['id']} / {curr['id']}")
        pass_("P7-C 阶段状态与浏览器证据")
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
