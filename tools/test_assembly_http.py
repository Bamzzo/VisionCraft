"""P6-B HTTP 合成闭环：前置校验、幂等、成片落库、替换镜头与失败清理。

成功路径通过 mock subprocess.run 覆盖，不调用付费 API。
若本机没有 FFmpeg，不会把跳过写成通过。
"""
from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db, utc_now
from backend.main import app
from backend.services import video_service
from backend.services.asset_service import public_asset_path
from backend.services.job_service import create_job

FFMPEG_AVAILABLE = bool(shutil.which("ffmpeg"))


def _client() -> TestClient:
    return TestClient(app)


def seed_project(title: str = "P6-B 合成") -> str:
    project_id = f"p6b_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode,
             status, routing_mode, assembly_stale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, "测试文本", "test", "16:9", 5, "auto", "production_ready", "direct", 1, now, now),
        )
    return project_id


def seed_shot(
    project_id: str,
    index: int,
    *,
    real: bool = True,
    with_file: bool = True,
    with_version: bool = True,
    video_path: str | None = None,
    asset_project_id: str | None = None,
    extra_version: bool = False,
) -> dict:
    shot_id = f"shot_{uuid.uuid4().hex[:10]}"
    version_id = f"version_{uuid.uuid4().hex[:10]}"
    filename = f"{shot_id}.mp4"
    path = video_path or (public_asset_path(project_id, filename) if with_file or with_version else None)
    now = utc_now()
    asset_owner = asset_project_id or project_id
    with connect() as conn:
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
                f"镜头 {index:02d}",
                "描述",
                "[]",
                "场景",
                "固定",
                "提示词",
                "",
                "",
                "video_ready" if path else "keyframes_ready",
                0,
                version_id if with_version else None,
                now,
                now,
            ),
        )
        if with_version:
            conn.execute(
                """INSERT INTO shot_versions
                (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
                 first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    version_id,
                    shot_id,
                    1,
                    "描述",
                    "提示词",
                    "",
                    "",
                    None,
                    None,
                    path if with_file else None,
                    "t2v",
                    "ark" if real else "ffmpeg",
                    "model",
                    "test",
                    now,
                ),
            )
            if path:
                conn.execute(
                    """INSERT INTO assets
                    (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                    VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)""",
                    (
                        f"asset_{uuid.uuid4().hex[:10]}",
                        asset_owner,
                        f"镜头 {index:02d} 视频",
                        "测试视频",
                        "测试",
                        path,
                        "provider:ark:model" if real else "provider:ffmpeg",
                        now,
                    ),
                )
    if with_file and path:
        local = PROJECTS_DIR / (asset_owner if asset_project_id else project_id) / Path(path).name
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(b"test-video-bytes")
    info = {"shot_id": shot_id, "version_id": version_id, "video_path": path}
    if extra_version and path:
        alt_id = f"version_{uuid.uuid4().hex[:10]}"
        alt_name = f"{shot_id}_v2.mp4"
        alt_path = public_asset_path(project_id, alt_name)
        with connect() as conn:
            conn.execute(
                """INSERT INTO shot_versions
                (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
                 first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (alt_id, shot_id, 2, "替换版本", "提示词", "", "", None, None, alt_path, "t2v", "ark", "model", "test", now),
            )
            conn.execute(
                """INSERT INTO assets
                (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)""",
                (f"asset_{uuid.uuid4().hex[:10]}", project_id, "替换镜头视频", "替换", "测试", alt_path, "provider:ark:model", now),
            )
        (PROJECTS_DIR / project_id / alt_name).write_bytes(b"replacement-video")
        info["alt_version_id"] = alt_id
        info["alt_path"] = alt_path
    return info


def cleanup(project_id: str) -> None:
    if not project_id or not str(project_id).startswith("p6b_"):
        return
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)


def mock_ffmpeg_success():
    original = video_service.subprocess.run

    def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"assembled-video")
        return video_service.subprocess.CompletedProcess(command, 0, "", "")

    video_service.subprocess.run = fake_run
    return original


def mock_ffmpeg_fail():
    original = video_service.subprocess.run

    def fake_run(command, **_kwargs):
        return video_service.subprocess.CompletedProcess(command, 1, "", "ffmpeg boom")

    video_service.subprocess.run = fake_run
    return original


def test_reject_no_shots() -> None:
    client = _client()
    project_id = seed_project()
    try:
        res = client.post(f"/api/projects/{project_id}/assemble")
        assert res.status_code == 400, res.text
        assert "镜头" in res.json()["detail"]
        with connect() as conn:
            jobs = conn.execute("SELECT id FROM jobs WHERE project_id = ?", (project_id,)).fetchall()
        assert jobs == []
        print("PASS: 无镜头时拒绝合成且不创建任务")
    finally:
        cleanup(project_id)


def test_reject_missing_video() -> None:
    client = _client()
    project_id = seed_project()
    try:
        seed_shot(project_id, 1)
        seed_shot(project_id, 2, with_file=False)
        res = client.post(f"/api/projects/{project_id}/assemble")
        assert res.status_code == 400, res.text
        detail = res.json()["detail"]
        assert "镜头 02" in detail and "尚未生成视频" in detail
        with connect() as conn:
            assert conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE project_id = ?", (project_id,)).fetchone()["n"] == 0
        print("PASS: 部分镜头无视频时拒绝并指出镜头")
    finally:
        cleanup(project_id)


def test_reject_missing_file() -> None:
    client = _client()
    project_id = seed_project()
    try:
        first = seed_shot(project_id, 1)
        seed_shot(project_id, 2)
        local = PROJECTS_DIR / project_id / Path(first["video_path"]).name
        local.unlink()
        res = client.post(f"/api/projects/{project_id}/assemble")
        assert res.status_code == 400, res.text
        assert "镜头 01" in res.json()["detail"]
        assert "不存在或已失效" in res.json()["detail"]
        print("PASS: 视频文件不存在时拒绝合成")
    finally:
        cleanup(project_id)


def test_reject_placeholder() -> None:
    client = _client()
    project_id = seed_project()
    try:
        seed_shot(project_id, 1)
        seed_shot(project_id, 2, real=False)
        res = client.post(f"/api/projects/{project_id}/assemble")
        assert res.status_code == 400, res.text
        assert "镜头 02" in res.json()["detail"]
        assert "占位视频" in res.json()["detail"]
        print("PASS: 占位视频不能进入成片")
    finally:
        cleanup(project_id)


def test_reject_foreign_asset() -> None:
    client = _client()
    owner = seed_project("资产归属方")
    project_id = seed_project("合成请求方")
    try:
        foreign = seed_shot(owner, 1)
        seed_shot(project_id, 1)
        seed_shot(project_id, 2, video_path=foreign["video_path"], asset_project_id=owner)
        res = client.post(f"/api/projects/{project_id}/assemble")
        assert res.status_code == 400, res.text
        assert "其他项目" in res.json()["detail"] or "不属于当前项目" in res.json()["detail"]
        print("PASS: 其他项目资产不能进入成片")
    finally:
        cleanup(project_id)
        cleanup(owner)


def test_success_persists_and_ready_event() -> None:
    client = _client()
    project_id = seed_project()
    original = mock_ffmpeg_success()
    try:
        seed_shot(project_id, 1)
        seed_shot(project_id, 2)
        res = client.post(f"/api/projects/{project_id}/assemble")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "queued" and body["job_id"]
        detail = client.get(f"/api/projects/{project_id}").json()
        assert detail["assembly_stale"] in {0, False}
        finals = [item for item in detail["assets"] if item["type"] == "final-video"]
        assert len(finals) == 1
        assert finals[0]["file_path"]
        assert detail["assembly"]["ok"] is True
        assert detail["assembly"]["stale"] is False
        events = detail["job_events"]
        assert any(item["event_type"] == "asset.ready" for item in events)
        job = client.get(f"/api/jobs/{body['job_id']}").json()
        assert job["status"] == "completed"
        ready = [item for item in job.get("events") or [] if item.get("event_type") == "asset.ready"]
        assert ready and ready[-1].get("detail", {}).get("shot_count") == 2
        print("PASS: 合成成功登记 final-video、清除 assembly_stale、追加 asset.ready")
    finally:
        video_service.subprocess.run = original
        cleanup(project_id)


def test_ffmpeg_failure_cleans_temp_and_skips_asset() -> None:
    client = _client()
    project_id = seed_project()
    original = mock_ffmpeg_fail()
    try:
        seed_shot(project_id, 1)
        seed_shot(project_id, 2)
        res = client.post(f"/api/projects/{project_id}/assemble")
        assert res.status_code == 200, res.text
        job = client.get(f"/api/jobs/{res.json()['job_id']}").json()
        assert job["status"] == "failed"
        assert "ffmpeg" not in (job.get("error_message") or "").lower()
        detail = client.get(f"/api/projects/{project_id}").json()
        assert detail["assembly_stale"] in {1, True}
        assert not [item for item in detail["assets"] if item["type"] == "final-video"]
        leftovers = list((PROJECTS_DIR / project_id).glob("*_concat.txt"))
        assert leftovers == []
        print("PASS: FFmpeg 失败后任务 failed、临时文件清理、不成片误登记")
    finally:
        video_service.subprocess.run = original
        cleanup(project_id)


def test_project_detail_exposes_preview() -> None:
    client = _client()
    project_id = seed_project()
    original = mock_ffmpeg_success()
    try:
        seed_shot(project_id, 1)
        seed_shot(project_id, 2)
        client.post(f"/api/projects/{project_id}/assemble")
        detail = client.get(f"/api/projects/{project_id}").json()
        assembly = client.get(f"/api/projects/{project_id}/assembly").json()
        assert assembly["current_final"]["file_path"] == detail["assembly"]["current_final"]["file_path"]
        assert assembly["current_final"]["file_path"].startswith(f"/assets/{project_id}/")
        print("PASS: 成片完成后项目详情可读到预览路径")
    finally:
        video_service.subprocess.run = original
        cleanup(project_id)


def test_replace_shot_marks_stale_and_reassemble_uses_new() -> None:
    client = _client()
    project_id = seed_project()
    original = mock_ffmpeg_success()
    used_paths: list[list[str]] = []

    def tracking_run(command, **_kwargs):
        concat = Path(command[command.index("-i") + 1])
        used_paths.append(concat.read_text(encoding="utf-8"))
        Path(command[-1]).write_bytes(b"assembled-video")
        return video_service.subprocess.CompletedProcess(command, 0, "", "")

    video_service.subprocess.run = tracking_run
    try:
        seed_shot(project_id, 1)
        shot2 = seed_shot(project_id, 2, extra_version=True)
        first = client.post(f"/api/projects/{project_id}/assemble")
        assert first.status_code == 200, first.text
        old_final = client.get(f"/api/projects/{project_id}").json()["assembly"]["current_final"]["id"]
        rolled = client.post(
            f"/api/projects/{project_id}/shots/{shot2['shot_id']}/versions/{shot2['alt_version_id']}/rollback"
        )
        assert rolled.status_code == 200, rolled.text
        stale = client.get(f"/api/projects/{project_id}").json()
        assert stale["assembly_stale"] in {1, True}
        assert stale["assembly"]["stale"] is True
        assert stale["assembly"]["current_final"]["id"] == old_final
        second = client.post(f"/api/projects/{project_id}/assemble")
        assert second.status_code == 200, second.text
        refreshed = client.get(f"/api/projects/{project_id}").json()
        finals = [item for item in refreshed["assets"] if item["type"] == "final-video"]
        assert len(finals) == 2
        assert refreshed["assembly"]["stale"] is False
        assert refreshed["assembly"]["current_final"]["id"] != old_final
        assert any(old_final == item["id"] for item in refreshed["assembly"]["history"])
        assert shot2["alt_path"].rsplit("/", 1)[-1] in used_paths[-1]
        print("PASS: 替换镜头后旧成片过期，重新合成使用新当前版本")
    finally:
        video_service.subprocess.run = original
        cleanup(project_id)


def test_duplicate_submit_reuses_active_job() -> None:
    client = _client()
    project_id = seed_project()
    try:
        seed_shot(project_id, 1)
        seed_shot(project_id, 2)
        existing = create_job(project_id, "sequence_assembly", "成片合成已排队")
        first = client.post(f"/api/projects/{project_id}/assemble")
        second = client.post(f"/api/projects/{project_id}/assemble")
        assert first.status_code == 200 and second.status_code == 200
        assert first.json()["job_id"] == existing
        assert second.json()["job_id"] == existing
        assert first.json().get("reused") is True
        with connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE project_id = ? AND type = 'sequence_assembly'",
                (project_id,),
            ).fetchone()["n"]
        assert count == 1
        print("PASS: 重复提交复用已有合成任务，不创建并发任务")
    finally:
        cleanup(project_id)


def main() -> None:
    init_environment()
    init_db()
    test_reject_no_shots()
    test_reject_missing_video()
    test_reject_missing_file()
    test_reject_placeholder()
    test_reject_foreign_asset()
    test_success_persists_and_ready_event()
    test_ffmpeg_failure_cleans_temp_and_skips_asset()
    test_project_detail_exposes_preview()
    test_replace_shot_marks_stale_and_reassemble_uses_new()
    test_duplicate_submit_reuses_active_job()
    if FFMPEG_AVAILABLE:
        print("INFO: 本机 PATH 中有 FFmpeg；成功路径仍使用 mock，避免改写真实媒体。")
    else:
        print("SKIP: 本机 PATH 中没有 FFmpeg，未运行真实 concat。静态校验与 mock 成功/失败路径已覆盖。")
    print("PASS: P6-B assembly HTTP contract")


if __name__ == "__main__":
    main()
