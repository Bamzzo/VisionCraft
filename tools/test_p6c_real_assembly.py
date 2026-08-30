"""P6-C 真实 FFmpeg 合成验收。

有 ffmpeg/ffprobe 时：用 lavfi 生成 4 个可播放短片，走现有服务层合成、替换与并发。
无 FFmpeg 时：只输出 SKIP 与安装说明，不把跳过写成通过，不生成 p6c-real 截图。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db, utc_now
from backend.main import app
from backend.services.asset_service import public_asset_path
from backend.services.job_service import create_job, get_job
from backend.services.video_service import assemble_project_video, enqueue_project_assembly
from tools.p6c_ffmpeg import (
    INSTALL_HINT,
    ensure_process_path,
    ffmpeg_available,
    ffmpeg_bin,
    ffmpeg_version,
    ffprobe_bin,
    ffprobe_version,
    make_color_clip,
    probe_video,
)

CREATED: list[str] = []
CLIP_SPECS = (
    {"index": 1, "color": "red", "size": "640x360", "duration": 1.2},
    {"index": 2, "color": "blue", "size": "1280x720", "duration": 1.5},
    {"index": 3, "color": "green", "size": "640x360", "duration": 1.2},
    {"index": 4, "color": "yellow", "size": "1280x720", "duration": 1.6},
)


def skip(msg: str) -> None:
    print(f"SKIP: {msg}")


def pass_(msg: str) -> None:
    print(f"PASS: {msg}")


def _client() -> TestClient:
    return TestClient(app)


def _cleanup(project_id: str) -> None:
    if not project_id or not str(project_id).startswith("p6c_"):
        return
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
            (project_id, title, "P6-C 真实合成样本", "cinematic clean realism", "16:9", 5, "auto",
             "production_ready", "direct", 1, now, now),
        )
    CREATED.append(project_id)
    return project_id


def _seed_real_shots(project_id: str) -> list[dict]:
    shots = []
    folder = PROJECTS_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    for spec in CLIP_SPECS:
        shot_id = f"shot_{uuid.uuid4().hex[:10]}"
        version_id = f"version_{uuid.uuid4().hex[:10]}"
        filename = f"{shot_id}.mp4"
        local = folder / filename
        make_color_clip(local, color=spec["color"], size=spec["size"], duration=spec["duration"])
        info = probe_video(local)
        if not info["has_video"] or info["width"] <= 0:
            raise RuntimeError(f"夹具无法被 ffprobe 识别：{local}")
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
                 "固定", "color bars", "", "", "video_ready", 0, version_id, now, now),
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
                (f"asset_{uuid.uuid4().hex[:10]}", project_id, f"镜头 {spec['index']:02d}", "真实 lavfi 夹具",
                 spec["color"], video_path, "provider:ark:local-fixture", now),
            )
        shots.append({"shot_id": shot_id, "version_id": version_id, "video_path": video_path, "local": local, **spec})
    return shots


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _local_from_public(project_id: str, public_path: str) -> Path:
    return PROJECTS_DIR / project_id / Path(public_path).name


def test_real_assemble_and_replace() -> None:
    client = _client()
    project_id = _seed_project("P6C 四镜头真实合成")
    try:
        shots = _seed_real_shots(project_id)
        print(
            "INFO: 夹具规格 "
            + ", ".join(f"{item['index']}:{item['size']}/{item['duration']}s/{item['color']}" for item in shots)
        )
        status = client.get(f"/api/projects/{project_id}/assembly").json()
        assert status["ok"] is True
        assert status["shot_count"] == 4
        assert all(item["ready"] for item in status["shots"])
        pass_("GET /assembly 四个真实镜头全部 ready")

        plan = enqueue_project_assembly(project_id)
        queued = get_job(plan["job_id"])
        assert queued["status"] == "queued"
        worker = threading.Thread(target=assemble_project_video, args=(project_id, plan["job_id"]))
        worker.start()
        seen = {queued["status"]}
        deadline = time.time() + 30
        while worker.is_alive() and time.time() < deadline:
            seen.add(get_job(plan["job_id"]).get("status") or "")
            time.sleep(0.05)
        worker.join(timeout=30)
        job = get_job(plan["job_id"])
        seen.add(job["status"])
        assert "queued" in seen
        assert job["status"] == "completed", job.get("error_message")
        event_types = [item["event_type"] for item in job["events"]]
        stages = [item.get("stage") for item in job["events"]]
        assert "asset.ready" in event_types
        assert "project.refresh_required" in event_types
        assert "validate_inputs" in stages and "concat" in stages and "persist_asset" in stages
        pass_("任务从 queued 进入 completed，并写出 asset.ready")

        detail = client.get(f"/api/projects/{project_id}").json()
        assert detail["assembly_stale"] in {0, False}
        final = detail["assembly"]["current_final"]
        output = _local_from_public(project_id, final["file_path"])
        assert output.is_file() and output.stat().st_size > 0
        probe = probe_video(output)
        expected_duration = sum(item["duration"] for item in CLIP_SPECS)
        assert probe["width"] == 1280 and probe["height"] == 720
        assert probe["pix_fmt"] == "yuv420p"
        assert probe["codec"] == "h264"
        assert expected_duration - 0.8 <= probe["duration"] <= expected_duration + 1.2
        first_hash = _file_hash(output)
        print(
            f"INFO: 成片 {output.name} {probe['width']}x{probe['height']} "
            f"{probe['pix_fmt']} {probe['duration']:.2f}s hash={first_hash[:12]}"
        )
        pass_("真实 FFmpeg 成片可被 ffprobe 识别，规格已统一，当前 P6 不处理音频")

        replace = shots[1]
        alt_name = f"{replace['shot_id']}_v2.mp4"
        alt_local = PROJECTS_DIR / project_id / alt_name
        make_color_clip(alt_local, color="white", size="960x540", duration=1.4)
        alt_path = public_asset_path(project_id, alt_name)
        alt_version = f"version_{uuid.uuid4().hex[:10]}"
        now = utc_now()
        with connect() as conn:
            conn.execute(
                """INSERT INTO shot_versions
                (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
                 first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (alt_version, replace["shot_id"], 2, "white", "color", "", "", None, None, alt_path, "t2v",
                 "ark", "local-fixture", "p6c", now),
            )
            conn.execute(
                """INSERT INTO assets
                (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)""",
                (f"asset_{uuid.uuid4().hex[:10]}", project_id, "替换镜头", "白色夹具", "white", alt_path,
                 "provider:ark:local-fixture", now),
            )
        rolled = client.post(
            f"/api/projects/{project_id}/shots/{replace['shot_id']}/versions/{alt_version}/rollback"
        )
        assert rolled.status_code == 200, rolled.text
        stale = client.get(f"/api/projects/{project_id}").json()
        assert stale["assembly_stale"] in {1, True}
        assert stale["assembly"]["stale"] is True
        assert stale["assembly"]["current_final"]["file_path"] == final["file_path"]
        video_jobs = [job for job in stale.get("jobs") or [] if job.get("type") == "video_generation"]
        assert video_jobs == []
        pass_("替换一个镜头后旧成片过期，且未触发其他镜头视频生成")

        second = client.post(f"/api/projects/{project_id}/assemble")
        assert second.status_code == 200, second.text
        refreshed = client.get(f"/api/projects/{project_id}").json()
        new_final = refreshed["assembly"]["current_final"]
        new_output = _local_from_public(project_id, new_final["file_path"])
        assert new_final["id"] != final["id"]
        assert new_output != output
        assert _file_hash(new_output) != first_hash
        assert refreshed["assembly"]["stale"] is False
        assert any(item["id"] == final["id"] for item in refreshed["assembly"]["history"])
        probe2 = probe_video(new_output)
        assert probe2["width"] == 1280 and probe2["has_video"]
        pass_("重新合成产生新的可播放成片，旧成片保留为历史")
    finally:
        _cleanup(project_id)


def test_concurrency_and_precheck() -> None:
    client = _client()
    project_id = _seed_project("P6C 并发与预检")
    missing = None
    removed = None
    try:
        _seed_real_shots(project_id)
        existing = create_job(project_id, "sequence_assembly", "成片合成已排队")
        first = client.post(f"/api/projects/{project_id}/assemble")
        second = client.post(f"/api/projects/{project_id}/assemble")
        assert first.json()["job_id"] == existing == second.json()["job_id"]
        assert first.json().get("reused") is True
        with connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE project_id = ? AND type = 'sequence_assembly'",
                (project_id,),
            ).fetchone()["n"]
        assert count == 1
        pass_("连续提交复用同一合成任务，不产生并发 sequence_assembly")

        missing = _seed_project("P6C 缺视频预检")
        with connect() as conn:
            conn.execute(
                """INSERT INTO shots
                (id, project_id, shot_index, title, description, characters, scene, camera_motion,
                 visual_prompt, negative_prompt, audio_prompt, status, retry_count, current_version_id,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (f"shot_{uuid.uuid4().hex[:8]}", missing, 1, "空镜头", "", "[]", "", "", "", "", "",
                 "keyframes_ready", 0, None, utc_now(), utc_now()),
            )
        res = client.post(f"/api/projects/{missing}/assemble")
        assert res.status_code == 400
        assert "尚未生成视频" in res.json()["detail"]
        with connect() as conn:
            jobs = conn.execute("SELECT id FROM jobs WHERE project_id = ?", (missing,)).fetchall()
        assert jobs == []
        pass_("缺视频时入队前中文 400，不创建任务")

        removed = _seed_project("P6C 缺文件预检")
        gone_shots = _seed_real_shots(removed)
        gone_shots[0]["local"].unlink()
        res = client.post(f"/api/projects/{removed}/assemble")
        assert res.status_code == 400
        assert "不存在或已失效" in res.json()["detail"]
        with connect() as conn:
            jobs = conn.execute("SELECT id FROM jobs WHERE project_id = ?", (removed,)).fetchall()
        assert jobs == []
        pass_("视频文件被移除后不会创建新的合成任务")
    finally:
        _cleanup(project_id)
        if missing:
            _cleanup(missing)
        if removed:
            _cleanup(removed)


def main() -> None:
    init_environment()
    init_db()
    ensure_process_path()
    print(f"INFO: ffmpeg={ffmpeg_version() or 'missing'}")
    print(f"INFO: ffprobe={ffprobe_version() or 'missing'}")
    print(f"INFO: ffmpeg_bin={ffmpeg_bin() or 'missing'}")
    print(f"INFO: ffprobe_bin={ffprobe_bin() or 'missing'}")
    if not ffmpeg_available():
        skip("真实 FFmpeg 四镜头合成（PATH 中没有 ffmpeg/ffprobe）")
        skip("替换镜头后真实重新合成")
        skip("真实输出 ffprobe 规格检查")
        skip("生成 output/playwright/p6c-real-*.png")
        print(INSTALL_HINT)
        print("P6-C real acceptance did not run")
        return
    print(f"INFO: 使用 {ffmpeg_version()}")
    try:
        test_real_assemble_and_replace()
        test_concurrency_and_precheck()
        print("PASS: P6-C real FFmpeg assembly contract")
    finally:
        for project_id in list(CREATED):
            if (PROJECTS_DIR / project_id).exists():
                _cleanup(project_id)


if __name__ == "__main__":
    main()
