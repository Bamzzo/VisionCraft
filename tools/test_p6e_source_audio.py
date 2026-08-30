"""P6-E 成片原声保留与混音合同。不调用付费 API。"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db, utc_now
from backend.main import app
from backend.services.asset_service import public_asset_path
from backend.services.job_service import get_job
from backend.services.video_service import assemble_project_video, enqueue_project_assembly
from tools.p6c_ffmpeg import (
    audio_mean_volume,
    ensure_process_path,
    ffmpeg_available,
    ffmpeg_version,
    ffprobe_version,
    make_color_clip,
    make_color_clip_with_sine,
    make_sine_wav,
    probe_media,
)

CREATED: list[str] = []


def pass_(msg: str) -> None:
    print(f"PASS: {msg}")


def skip(msg: str) -> None:
    print(f"SKIP: {msg}")


def _client() -> TestClient:
    return TestClient(app)


def _cleanup(project_id: str) -> None:
    if not project_id or not str(project_id).startswith("p6e_"):
        return
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
    print(f"CLEANED: {project_id}")


def _leftovers(project_id: str) -> list[str]:
    names = []
    for path in (PROJECTS_DIR / project_id).glob("*"):
        name = path.name
        if "_concat" in name or name.endswith("_pack.srt") or name.endswith("_packed.mp4") or "_norm_" in name:
            names.append(name)
    return names


def _seed_project(title: str) -> str:
    project_id = f"p6e_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode,
             status, routing_mode, assembly_stale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, "P6-E 样本", "cinematic clean realism", "16:9", 5, "auto",
             "production_ready", "direct", 0, now, now),
        )
    CREATED.append(project_id)
    return project_id


def _insert_shot(project_id: str, index: int, filename: str, title: str) -> str:
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
             "ark", "local-fixture", "p6e", now),
        )
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)""",
            (f"asset_{uuid.uuid4().hex[:10]}", project_id, f"镜头 {index}", "夹具", title,
             video_path, "provider:ark:local-fixture", now),
        )
    return video_path


def _seed_mixed_shots(project_id: str) -> None:
    folder = PROJECTS_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)
    make_color_clip_with_sine(folder / "shot_a.mp4", color="red", size="640x360", duration=1.2, frequency=440)
    make_color_clip_with_sine(folder / "shot_b.mp4", color="blue", size="1280x720", duration=1.5, frequency=880)
    make_color_clip(folder / "shot_c.mp4", color="green", size="640x360", duration=1.0)
    _insert_shot(project_id, 1, "shot_a.mp4", "440Hz")
    _insert_shot(project_id, 2, "shot_b.mp4", "880Hz")
    _insert_shot(project_id, 3, "shot_c.mp4", "无音轨")


def _seed_audio(project_id: str, duration: float, name: str = "bg.wav") -> str:
    folder = PROJECTS_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)
    make_sine_wav(folder / name, duration=duration, frequency=220)
    public = public_asset_path(project_id, name)
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
    worker.join(timeout=90)
    job = get_job(plan["job_id"])
    assert job["status"] == "completed", job.get("error_message")
    return job


def _final_path(client: TestClient, project_id: str) -> Path:
    detail = client.get(f"/api/projects/{project_id}").json()
    current = detail["assembly"]["current_final"]
    assert current, "缺少当前成片"
    return PROJECTS_DIR / project_id / Path(current["file_path"]).name, detail


def test_settings_restore() -> None:
    client = _client()
    project_id = _seed_project("P6E 配置恢复")
    try:
        put = client.put(
            f"/api/projects/{project_id}/assembly-settings",
            json={
                "keep_source_audio": True,
                "audio_enabled": False,
                "subtitle_enabled": False,
                "audio_volume": 0.55,
                "subtitle_text": "恢复校验",
            },
        )
        assert put.status_code == 200, put.text
        again = client.get(f"/api/projects/{project_id}/assembly-settings").json()
        assert again["settings"]["keep_source_audio"] is True
        assert again["settings"]["audio_volume"] == 0.55
        assert again["settings"]["subtitle_text"] == "恢复校验"
        assembly = client.get(f"/api/projects/{project_id}/assembly").json()
        assert assembly["settings"]["keep_source_audio"] is True
        pass_("页面接口刷新后能恢复原声开关和成片配置")
    finally:
        _cleanup(project_id)


def test_source_audio_matrix() -> None:
    if not ffmpeg_available():
        skip("原声/背景音真实合成矩阵（无 FFmpeg）")
        return
    client = _client()
    project_id = _seed_project("P6E 原声矩阵")
    try:
        _seed_mixed_shots(project_id)
        short_bg = _seed_audio(project_id, 0.4, "short.wav")
        long_bg = _seed_audio(project_id, 12.0, "long.wav")
        status = client.get(f"/api/projects/{project_id}/assembly").json()
        assert status["source_audio_shot_count"] == 2
        assert status["source_audio_available"] is True
        assert status["source_audio_used"] is False
        pass_("能识别带音轨和无音轨镜头，未开启原声时不声明使用原声")

        _run_assembly(project_id)
        path, detail = _final_path(client, project_id)
        probe = probe_media(path)
        assert probe["has_video"] and probe["width"] == 1280 and probe["codec"] == "h264"
        assert probe["has_audio"] is False
        assert abs(probe["duration"] - 3.7) < 0.4
        first_id = detail["assembly"]["current_final"]["id"]
        assert _leftovers(project_id) == []
        pass_("无音频配置仍然输出无音频成片，规格 1280x720")

        put = client.put(
            f"/api/projects/{project_id}/assembly-settings",
            json={"keep_source_audio": True, "audio_enabled": False, "subtitle_enabled": False},
        )
        assert put.status_code == 200, put.text
        stale = client.get(f"/api/projects/{project_id}").json()
        assert stale["assembly_stale"] in {1, True}
        _run_assembly(project_id)
        path, detail = _final_path(client, project_id)
        probe = probe_media(path)
        print(
            "INFO: keep-source ffprobe "
            f"codec={probe['audio_codec']} rate={probe['audio_sample_rate']} "
            f"ch={probe['audio_channels']} streams={probe['audio_streams']} dur={probe['duration']:.2f}"
        )
        assert probe["has_audio"] is True
        assert probe["audio_streams"] == 1
        assert probe["audio_codec"] in {"aac"}
        assert probe["audio_sample_rate"] == 44100
        assert abs(probe["duration"] - 3.7) < 0.45
        vol_a = audio_mean_volume(path, start=0.3, duration=0.4)
        vol_b = audio_mean_volume(path, start=1.6, duration=0.4)
        vol_silent = audio_mean_volume(path, start=2.95, duration=0.4)
        print(f"INFO: mean_volume a={vol_a:.1f}dB b={vol_b:.1f}dB silent={vol_silent:.1f}dB")
        assert vol_a > -40 and vol_b > -40
        assert vol_silent < -50
        assert detail["assembly"]["source_audio_used"] is True
        second_id = detail["assembly"]["current_final"]["id"]
        assert second_id != first_id
        assert any(item["id"] == first_id for item in detail["assembly"]["history"])
        assert _leftovers(project_id) == []
        pass_("仅保留原声时拼接各镜头原声，无音轨片段不伪造有声，历史成片可查询")

        put = client.put(
            f"/api/projects/{project_id}/assembly-settings",
            json={
                "keep_source_audio": False,
                "audio_enabled": True,
                "audio_asset_path": short_bg,
                "audio_volume": 0.6,
            },
        )
        assert put.status_code == 200, put.text
        _run_assembly(project_id)
        path, detail = _final_path(client, project_id)
        probe = probe_media(path)
        print(
            "INFO: bg-only ffprobe "
            f"codec={probe['audio_codec']} rate={probe['audio_sample_rate']} dur={probe['duration']:.2f}"
        )
        assert probe["has_audio"] is True
        assert probe["audio_streams"] == 1
        assert abs(probe["duration"] - 3.7) < 0.45
        vol = audio_mean_volume(path, start=3.1, duration=0.3)
        assert vol > -40
        assert _leftovers(project_id) == []
        pass_("仅背景音时输出背景音，短于成片则循环补齐到成片时长")

        put = client.put(
            f"/api/projects/{project_id}/assembly-settings",
            json={
                "keep_source_audio": True,
                "audio_enabled": True,
                "audio_asset_path": long_bg,
                "audio_volume": 0.3,
            },
        )
        assert put.status_code == 200, put.text
        _run_assembly(project_id)
        path, detail = _final_path(client, project_id)
        probe = probe_media(path)
        print(
            "INFO: mix ffprobe "
            f"codec={probe['audio_codec']} rate={probe['audio_sample_rate']} "
            f"streams={probe['audio_streams']} dur={probe['duration']:.2f}"
        )
        assert probe["has_audio"] is True
        assert probe["audio_streams"] == 1
        assert probe["audio_codec"] == "aac"
        assert probe["audio_sample_rate"] == 44100
        assert abs(probe["duration"] - 3.7) < 0.45
        assert probe["duration"] < 8.0
        assert _leftovers(project_id) == []
        pass_("原声与背景音同时启用时混音，长背景音被裁剪到成片时长")
    finally:
        _cleanup(project_id)


def test_keep_source_without_audio_does_not_invent() -> None:
    if not ffmpeg_available():
        skip("无原声镜头开启保留原声（无 FFmpeg）")
        return
    client = _client()
    project_id = _seed_project("P6E 无原声")
    try:
        folder = PROJECTS_DIR / project_id
        folder.mkdir(parents=True, exist_ok=True)
        make_color_clip(folder / "mute_a.mp4", color="red", size="640x360", duration=1.0)
        make_color_clip(folder / "mute_b.mp4", color="blue", size="640x360", duration=1.0)
        _insert_shot(project_id, 1, "mute_a.mp4", "静音A")
        _insert_shot(project_id, 2, "mute_b.mp4", "静音B")
        put = client.put(
            f"/api/projects/{project_id}/assembly-settings",
            json={"keep_source_audio": True},
        )
        assert put.status_code == 200, put.text
        status = client.get(f"/api/projects/{project_id}/assembly").json()
        assert status["source_audio_available"] is False
        assert status["source_audio_used"] is False
        _run_assembly(project_id)
        path, detail = _final_path(client, project_id)
        probe = probe_media(path)
        assert probe["has_audio"] is False
        assert detail["assembly"]["source_audio_used"] is False
        pass_("没有原声时不会错误声明存在原声，成片仍无音轨")
    finally:
        _cleanup(project_id)


def test_pack_failure_keeps_history() -> None:
    if not ffmpeg_available():
        skip("原声合成失败不登记（无 FFmpeg）")
        return
    from backend.services import video_service

    client = _client()
    project_id = _seed_project("P6E 失败")
    try:
        _seed_mixed_shots(project_id)
        _run_assembly(project_id)
        first = client.get(f"/api/projects/{project_id}").json()["assembly"]["current_final"]["id"]
        audio_path = _seed_audio(project_id, 1.0, "fail.wav")
        client.put(
            f"/api/projects/{project_id}/assembly-settings",
            json={"keep_source_audio": True, "audio_enabled": True, "audio_asset_path": audio_path},
        )
        with patch.object(
            video_service,
            "_pack_assembly_output",
            side_effect=RuntimeError("ffmpeg -i C:\\keys\\secret.wav"),
        ):
            plan = enqueue_project_assembly(project_id)
            assemble_project_video(project_id, plan["job_id"])
            job = get_job(plan["job_id"])
        assert job["status"] == "failed"
        err = (job.get("error_message") or "").lower()
        assert "ffmpeg" not in err and "secret" not in err
        detail = client.get(f"/api/projects/{project_id}").json()
        finals = [item for item in detail["assets"] if item["type"] == "final-video"]
        assert len(finals) == 1
        assert finals[0]["id"] == first
        assert detail["assembly_stale"] in {1, True}
        assert _leftovers(project_id) == []
        pass_("失败不登记无效 final-video，不误清 stale，临时文件已清理")
    finally:
        _cleanup(project_id)


def main() -> None:
    init_environment()
    init_db()
    ensure_process_path()
    print(f"INFO: ffmpeg={ffmpeg_version() or 'missing'}")
    print(f"INFO: ffprobe={ffprobe_version() or 'missing'}")
    try:
        test_settings_restore()
        test_keep_source_without_audio_does_not_invent()
        test_source_audio_matrix()
        test_pack_failure_keeps_history()
        print("PASS: P6-E source audio assembly contract")
    finally:
        for project_id in list(CREATED):
            if (PROJECTS_DIR / project_id).exists():
                _cleanup(project_id)


if __name__ == "__main__":
    main()
