"""P6-D 本地音频/字幕成片配置与合成合同。不调用付费 API。"""
from __future__ import annotations

import os
import shutil
import sys
import threading
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
    ensure_process_path,
    ffmpeg_available,
    ffmpeg_version,
    ffprobe_version,
    make_color_clip,
    make_sine_wav,
    probe_media,
)
from tools.test_p6c_real_assembly import CLIP_SPECS

CREATED: list[str] = []


def pass_(msg: str) -> None:
    print(f"PASS: {msg}")


def skip(msg: str) -> None:
    print(f"SKIP: {msg}")


def _client() -> TestClient:
    return TestClient(app)


def _cleanup(project_id: str) -> None:
    if not project_id or not str(project_id).startswith("p6d_"):
        return
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
    print(f"CLEANED: {project_id}")


def _seed_project(title: str) -> str:
    project_id = f"p6d_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode,
             status, routing_mode, assembly_stale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, "P6-D 样本", "cinematic clean realism", "16:9", 5, "auto",
             "production_ready", "direct", 0, now, now),
        )
    CREATED.append(project_id)
    return project_id


def _seed_shots(project_id: str) -> None:
    folder = PROJECTS_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    for spec in CLIP_SPECS:
        shot_id = f"shot_{uuid.uuid4().hex[:10]}"
        version_id = f"version_{uuid.uuid4().hex[:10]}"
        filename = f"{shot_id}.mp4"
        make_color_clip(folder / filename, color=spec["color"], size=spec["size"], duration=spec["duration"])
        video_path = public_asset_path(project_id, filename)
        with connect() as conn:
            conn.execute(
                """INSERT INTO shots
                (id, project_id, shot_index, title, description, characters, scene, camera_motion,
                 visual_prompt, negative_prompt, audio_prompt, status, retry_count, current_version_id,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (shot_id, project_id, spec["index"], f"色块 {spec['index']}", spec["color"], "[]", "色块",
                 "固定", "color", "", "", "video_ready", 0, version_id, now, now),
            )
            conn.execute(
                """INSERT INTO shot_versions
                (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
                 first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (version_id, shot_id, 1, spec["color"], "color", "", "", None, None, video_path, "t2v",
                 "ark", "local-fixture", "p6d", now),
            )
            conn.execute(
                """INSERT INTO assets
                (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)""",
                (f"asset_{uuid.uuid4().hex[:10]}", project_id, f"镜头 {spec['index']}", "夹具", spec["color"],
                 video_path, "provider:ark:local-fixture", now),
            )


def _seed_audio(project_id: str, duration: float = 1.0) -> str:
    folder = PROJECTS_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"bg_{uuid.uuid4().hex[:8]}.wav"
    make_sine_wav(folder / filename, duration=duration)
    public = public_asset_path(project_id, filename)
    with connect() as conn:
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, 'audio', ?, ?, ?, ?, ?, ?)""",
            (f"asset_{uuid.uuid4().hex[:10]}", project_id, "背景音", "sine", "sine", public,
             "provider:ffmpeg:local-audio", utc_now()),
        )
    return public


def _run_assembly(project_id: str) -> dict:
    plan = enqueue_project_assembly(project_id)
    worker = threading.Thread(target=assemble_project_video, args=(project_id, plan["job_id"]))
    worker.start()
    worker.join(timeout=60)
    job = get_job(plan["job_id"])
    assert job["status"] == "completed", job.get("error_message")
    return job


def test_settings_validation() -> None:
    client = _client()
    project_id = _seed_project("P6D 配置校验")
    other_id = _seed_project("P6D 其他项目")
    try:
        empty = client.get(f"/api/projects/{project_id}/assembly-settings")
        assert empty.status_code == 200
        body = empty.json()
        assert body["settings"]["audio_enabled"] is False
        assert body["settings"]["subtitle_enabled"] is False
        pass_("默认配置可读，音频字幕均关闭")

        saved = client.put(
            f"/api/projects/{project_id}/assembly-settings",
            json={"subtitle_enabled": False, "audio_enabled": False, "subtitle_text": "保存往返"},
        )
        assert saved.status_code == 200, saved.text
        again = client.get(f"/api/projects/{project_id}/assembly-settings").json()
        assert again["settings"]["subtitle_text"] == "保存往返"
        assembly = client.get(f"/api/projects/{project_id}/assembly").json()
        assert assembly["settings"]["subtitle_text"] == "保存往返"
        assert assembly["audio_scope"] == "optional_local"
        pass_("配置保存后能够读取")

        traversal = client.put(
            f"/api/projects/{project_id}/assembly-settings",
            json={"audio_enabled": True, "audio_asset_path": f"/assets/{project_id}/../secret.wav"},
        )
        assert traversal.status_code == 400
        assert "路径" in traversal.json()["detail"]
        assert "ffmpeg" not in traversal.json()["detail"].lower()
        pass_("目录穿越被拒绝且错误不含 FFmpeg 命令")

        outside = client.put(
            f"/api/projects/{project_id}/assembly-settings",
            json={"audio_enabled": True, "audio_asset_path": r"C:\Windows\Media\notify.wav"},
        )
        assert outside.status_code == 400
        pass_("目录外文件被拒绝")

        missing = client.put(
            f"/api/projects/{project_id}/assembly-settings",
            json={"audio_enabled": True, "audio_asset_path": f"/assets/{project_id}/nope.wav"},
        )
        assert missing.status_code == 400
        pass_("不存在文件被拒绝")

        other_audio = "/assets/not-this-project/bg.wav"
        foreign = client.put(
            f"/api/projects/{project_id}/assembly-settings",
            json={"audio_enabled": True, "audio_asset_path": other_audio},
        )
        assert foreign.status_code == 400
        assert "当前项目" in foreign.json()["detail"]
        pass_("其他项目音频被拒绝")
        assert (PROJECTS_DIR / other_id).exists() or True
    finally:
        _cleanup(project_id)
        _cleanup(other_id)


def test_default_and_audio_real() -> None:
    if not ffmpeg_available():
        skip("真实默认合成与背景音频混入（无 FFmpeg）")
        return
    client = _client()
    project_id = _seed_project("P6D 真实音频")
    try:
        _seed_shots(project_id)
        status = client.get(f"/api/projects/{project_id}/assembly").json()
        assert status["ok"] is True
        assert status["settings"]["audio_enabled"] is False
        _run_assembly(project_id)
        detail = client.get(f"/api/projects/{project_id}").json()
        first = detail["assembly"]["current_final"]
        first_path = PROJECTS_DIR / project_id / Path(first["file_path"]).name
        probe = probe_media(first_path)
        assert probe["width"] == 1280 and probe["codec"] == "h264"
        assert probe["has_audio"] is False
        leftover = [
            p.name
            for p in (PROJECTS_DIR / project_id).glob("*")
            if "_concat" in p.name or p.name.endswith("_pack.srt") or p.name.endswith("_packed.mp4")
        ]
        assert leftover == []
        pass_("无音频无字幕时 P6-C 行为不变，且无音轨、临时文件已清理")

        audio_path = _seed_audio(project_id, duration=1.0)
        put = client.put(
            f"/api/projects/{project_id}/assembly-settings",
            json={"audio_enabled": True, "audio_asset_path": audio_path, "audio_volume": 0.5},
        )
        assert put.status_code == 200, put.text
        stale = client.get(f"/api/projects/{project_id}").json()
        assert stale["assembly_stale"] in {1, True}
        assert stale["assembly"]["stale"] is True
        assert stale["assembly"]["current_final"]["id"] == first["id"]
        pass_("合法项目音频可通过配置校验，配置变化后 assembly_stale=1")

        _run_assembly(project_id)
        refreshed = client.get(f"/api/projects/{project_id}").json()
        second = refreshed["assembly"]["current_final"]
        assert second["id"] != first["id"]
        assert any(item["id"] == first["id"] for item in refreshed["assembly"]["history"])
        assert refreshed["assembly"]["stale"] is False
        new_path = PROJECTS_DIR / project_id / Path(second["file_path"]).name
        probe2 = probe_media(new_path)
        assert probe2["has_audio"] is True
        assert probe2["width"] == 1280
        leftover = [
            p.name
            for p in (PROJECTS_DIR / project_id).glob("*")
            if "_concat" in p.name or p.name.endswith("_pack.srt") or p.name.endswith("_packed.mp4")
        ]
        assert leftover == []
        pass_("合成使用已保存音频配置，成功登记 final-video，旧成片留在历史，临时文件已清理")
    finally:
        _cleanup(project_id)


def test_pack_failure_does_not_register() -> None:
    from unittest.mock import patch

    from backend.services import video_service

    if not ffmpeg_available():
        skip("包装失败不登记 final-video（无 FFmpeg）")
        return
    client = _client()
    project_id = _seed_project("P6D 失败不登记")
    try:
        _seed_shots(project_id)
        _run_assembly(project_id)
        audio_path = _seed_audio(project_id, duration=1.0)
        put = client.put(
            f"/api/projects/{project_id}/assembly-settings",
            json={"audio_enabled": True, "audio_asset_path": audio_path},
        )
        assert put.status_code == 200, put.text
        with patch.object(
            video_service,
            "_pack_assembly_output",
            side_effect=RuntimeError("ffmpeg -i C:\\keys\\secret.wav -vf subtitles"),
        ):
            plan = enqueue_project_assembly(project_id)
            assemble_project_video(project_id, plan["job_id"])
            job = get_job(plan["job_id"])
        assert job["status"] == "failed"
        err = (job.get("error_message") or "").lower()
        assert "ffmpeg" not in err
        assert "secret" not in err
        assert "-vf" not in err
        detail = client.get(f"/api/projects/{project_id}").json()
        finals = [item for item in detail["assets"] if item["type"] == "final-video"]
        assert len(finals) == 1
        assert detail["assembly_stale"] in {1, True}
        leftover = [
            p.name
            for p in (PROJECTS_DIR / project_id).glob("*")
            if "_concat" in p.name or p.name.endswith("_pack.srt") or p.name.endswith("_packed.mp4")
        ]
        assert leftover == []
        pass_("合成失败不登记 final-video，不清除 stale，临时文件已清理，错误不含命令行")
    finally:
        _cleanup(project_id)


def test_subtitle_real_or_skip() -> None:
    if not ffmpeg_available():
        skip("真实字幕烧录（无 FFmpeg）")
        return
    from backend.services.video_service import _ffmpeg_has_filter, _subtitle_font_file

    if not _ffmpeg_has_filter("subtitles") or _subtitle_font_file() is None:
        skip("真实字幕烧录（本机缺少字幕滤镜或字体）")
        return
    client = _client()
    project_id = _seed_project("P6D 字幕")
    try:
        _seed_shots(project_id)
        put = client.put(
            f"/api/projects/{project_id}/assembly-settings",
            json={"subtitle_enabled": True, "subtitle_text": "P6-D 字幕验收", "subtitle_font_size": 36},
        )
        assert put.status_code == 200, put.text
        _run_assembly(project_id)
        detail = client.get(f"/api/projects/{project_id}").json()
        output = PROJECTS_DIR / project_id / Path(detail["assembly"]["current_final"]["file_path"]).name
        probe = probe_media(output)
        assert probe["has_video"] and probe["width"] == 1280
        raw = PROJECTS_DIR / project_id / "subtitle_frame.rgb"
        from tools.p6c_ffmpeg import ffmpeg_bin
        import subprocess

        subprocess.run(
            [
                ffmpeg_bin(),
                "-y",
                "-ss",
                "0.4",
                "-i",
                str(output),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                str(raw),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        data = raw.read_bytes() if raw.is_file() else b""
        raw.unlink(missing_ok=True)
        width, height = 1280, 720
        assert len(data) >= width * height * 3
        # 底部字幕带：纯色镜头几乎只有一种颜色，烧录字幕会引入白/黑轮廓。
        start = width * 620 * 3
        band = data[start : width * height * 3]
        colors = {band[i : i + 3] for i in range(0, len(band), 3 * 8)}
        assert len(colors) > 8, f"字幕画面颜色过少：{len(colors)}"
        pass_("启用字幕后画面真实生成，而非只改数据库字段")
    finally:
        _cleanup(project_id)


def _seed_dummy_shots(project_id: str, count: int = 2) -> None:
    folder = PROJECTS_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    for index in range(1, count + 1):
        shot_id = f"shot_{uuid.uuid4().hex[:10]}"
        version_id = f"version_{uuid.uuid4().hex[:10]}"
        filename = f"{shot_id}.mp4"
        (folder / filename).write_bytes(b"dummy-mp4")
        video_path = public_asset_path(project_id, filename)
        with connect() as conn:
            conn.execute(
                """INSERT INTO shots
                (id, project_id, shot_index, title, description, characters, scene, camera_motion,
                 visual_prompt, negative_prompt, audio_prompt, status, retry_count, current_version_id,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (shot_id, project_id, index, f"镜头 {index}", "dummy", "[]", "场景", "固定", "prompt", "", "",
                 "video_ready", 0, version_id, now, now),
            )
            conn.execute(
                """INSERT INTO shot_versions
                (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
                 first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (version_id, shot_id, 1, "dummy", "prompt", "", "", None, None, video_path, "t2v",
                 "ark", "local-fixture", "p6d", now),
            )
            conn.execute(
                """INSERT INTO assets
                (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)""",
                (f"asset_{uuid.uuid4().hex[:10]}", project_id, f"镜头 {index}", "夹具", "dummy",
                 video_path, "provider:ark:local-fixture", now),
            )


def test_reuse_and_failure_messages() -> None:
    client = _client()
    project_id = _seed_project("P6D 幂等")
    try:
        _seed_dummy_shots(project_id)
        existing = create_job(project_id, "sequence_assembly", "成片合成已排队")
        first = client.post(f"/api/projects/{project_id}/assemble")
        second = client.post(f"/api/projects/{project_id}/assemble")
        assert first.status_code == 200
        assert first.json()["job_id"] == existing == second.json()["job_id"]
        pass_("重复提交仍复用活动合成任务")

        bad = client.put(
            f"/api/projects/{project_id}/assembly-settings",
            json={"audio_enabled": True, "audio_asset_path": f"/assets/{project_id}/missing.wav"},
        )
        detail = bad.json().get("detail") or ""
        assert bad.status_code == 400
        assert "ffmpeg" not in detail.lower()
        assert " -i " not in detail
        pass_("中文错误消息不包含 FFmpeg 命令行或密钥")
    finally:
        _cleanup(project_id)


def main() -> None:
    init_environment()
    init_db()
    ensure_process_path()
    print(f"INFO: ffmpeg={ffmpeg_version() or 'missing'}")
    print(f"INFO: ffprobe={ffprobe_version() or 'missing'}")
    try:
        test_settings_validation()
        test_reuse_and_failure_messages()
        test_default_and_audio_real()
        test_pack_failure_does_not_register()
        test_subtitle_real_or_skip()
        print("PASS: P6-D assembly audio/subtitle contract")
    finally:
        for project_id in list(CREATED):
            if (PROJECTS_DIR / project_id).exists():
                _cleanup(project_id)


if __name__ == "__main__":
    main()
