"""P8-B no-cost tests: project asset upload, magic-byte checks, and isolation.

真实模型请求：否。费用：0 元。
"""
from __future__ import annotations

import json
import os
import shutil
import struct
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
from backend.services.asset_upload_service import AssetUploadError, upload_project_asset
from backend.services.project_service import delete_project, get_project
from backend.services.video_service import AssemblyError, get_assembly_settings_payload, save_assembly_settings

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
SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"></svg>'
VALID_SRT = "1\n00:00:00,000 --> 00:00:01,000\n你好\n\n2\n00:00:01,000 --> 00:00:02,000\n世界\n"
PREFIX = "p8b_"
CREATED: list[str] = []


def pass_(msg: str) -> None:
    print(f"PASS: {msg}")


def skip(msg: str) -> None:
    print(f"SKIP: {msg}")


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


def _project(title: str = "P8B 上传") -> str:
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
    CREATED.append(project_id)
    return project_id


def _shot_id(project_id: str) -> str:
    project = get_project(project_id)
    return project["shots"][0]["id"]


def _asset_blob(project_id: str) -> str:
    with connect() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM assets WHERE project_id = ?", (project_id,)).fetchall()]
        events = [dict(row) for row in conn.execute("SELECT * FROM job_events WHERE project_id = ?", (project_id,)).fetchall()]
    return json.dumps({"assets": rows, "events": events}, ensure_ascii=False)


def test_jpeg_png_and_security() -> None:
    project_id = _project()
    other_id = _project("P8B 隔离")
    shot_id = _shot_id(project_id)
    other_shot = _shot_id(other_id)
    folder = PROJECTS_DIR / project_id
    before = set(folder.glob("*")) if folder.exists() else set()
    jpeg = upload_project_asset(project_id, asset_role="first_frame", content=JPEG_BYTES, filename="../../../gyfy.jpg", shot_id=shot_id)
    png = upload_project_asset(project_id, asset_role="last_frame", content=PNG_BYTES, filename="frame.png", shot_id=shot_id)
    again = upload_project_asset(project_id, asset_role="reference_image", content=JPEG_BYTES, filename="gyfy.jpg", shot_id=shot_id)
    assert jpeg["asset"]["id"] != again["asset"]["id"]
    assert jpeg["asset"]["file_path"].startswith(f"/assets/{project_id}/")
    assert ".." not in jpeg["asset"]["file_path"]
    assert jpeg["asset"]["width"] == 1 and jpeg["asset"]["height"] == 1
    assert png["asset"]["mime_type"] == "image/png"
    assert jpeg["asset"]["file_path"] != again["asset"]["file_path"]
    project = get_project(project_id)
    assert project["shots"][0]["current_version_id"]
    assert len(project["shots"][0]["versions"]) >= 2
    try:
        upload_project_asset(project_id, asset_role="first_frame", content=SVG_BYTES, filename="x.jpg")
        raise AssertionError("SVG 应被拒绝")
    except AssetUploadError as exc:
        assert "JPEG" in str(exc) or "PNG" in str(exc)
    try:
        upload_project_asset(project_id, asset_role="first_frame", content=b"not-an-image", filename="x.jpg")
        raise AssertionError("伪装图片应被拒绝")
    except AssetUploadError as exc:
        assert exc.code in {"UNSUPPORTED_IMAGE_FORMAT", "SVG_NOT_ALLOWED"}
    huge = JPEG_BYTES + b"\x00" * (20 * 1024 * 1024)
    try:
        upload_project_asset(project_id, asset_role="keyframe", content=huge, filename="big.jpg")
        raise AssertionError("超大图片应被拒绝")
    except AssetUploadError as exc:
        assert exc.code == "FILE_TOO_LARGE"
    try:
        upload_project_asset(project_id, asset_role="first_frame", content=JPEG_BYTES, filename="x.jpg", shot_id=other_shot)
        raise AssertionError("跨项目镜头应被拒绝")
    except AssetUploadError as exc:
        assert exc.code in {"SHOT_MISMATCH", "SHOT_NOT_FOUND"}
    after = set(folder.glob("*"))
    assert not any(path.name.endswith(".jpg") and "gyfy.jpg" == path.name for path in after)
    assert not any("data:image" in str(path) for path in after - before)
    isolated = get_project(other_id)
    assert not any(item.get("file_path") == jpeg["asset"]["file_path"] for item in isolated.get("assets") or [])
    blob = _asset_blob(project_id)
    assert "data:image" not in blob.lower()
    assert "base64," not in blob.lower()
    assert "sk-" not in blob
    pass_("JPEG/PNG 上传、尺寸、去重、跨项目拒绝与伪装文件拒绝")


def test_audio_and_subtitle() -> None:
    project_id = _project("P8B 音频字幕")
    other_id = _project("P8B 音频隔离")
    wav = tiny_wav()
    try:
        audio = upload_project_asset(project_id, asset_role="background_audio", content=wav, filename="bg.wav")
    except AssetUploadError as exc:
        if exc.code == "AUDIO_PROBE_UNAVAILABLE":
            skip("本机没有 FFprobe，跳过音频上传校验")
            audio = None
        else:
            raise
    if audio:
        assert audio["asset"]["type"] == "audio"
        assert audio["asset"]["duration_seconds"] and audio["asset"]["duration_seconds"] > 0
        assert audio["asset"]["byte_size"] == len(wav)
        saved = save_assembly_settings(
            project_id,
            {"audio_enabled": True, "audio_asset_path": audio["asset"]["file_path"], "audio_volume": 0.4},
        )
        assert saved["settings"]["audio_asset_path"] == audio["asset"]["file_path"]
        restored = get_assembly_settings_payload(project_id)
        assert restored["settings"]["audio_asset_path"] == audio["asset"]["file_path"]
        try:
            save_assembly_settings(
                other_id,
                {"audio_enabled": True, "audio_asset_path": audio["asset"]["file_path"]},
            )
            raise AssertionError("其他项目不得使用当前音频")
        except AssemblyError as exc:
            assert "项目" in str(exc)
        try:
            upload_project_asset(project_id, asset_role="background_audio", content=b"not-audio", filename="x.wav")
            raise AssertionError("不可读音频应被拒绝")
        except AssetUploadError as exc:
            assert exc.code in {"AUDIO_UNREADABLE", "FILE_TOO_LARGE"}
        pass_("合法 WAV 上传、FFprobe 可读、成片配置可保存，非法音频被拒绝")
    srt = upload_project_asset(project_id, asset_role="subtitle", content=VALID_SRT.encode("utf-8"), filename="zh.srt")
    generated = upload_project_asset(project_id, asset_role="subtitle", subtitle_text="方源停在山门前。")
    assert srt["asset"]["type"] == "subtitle"
    assert generated["asset"]["file_path"].endswith(".srt")
    try:
        upload_project_asset(project_id, asset_role="subtitle", content=b"hello world", filename="bad.srt")
        raise AssertionError("非法字幕应被拒绝")
    except AssetUploadError as exc:
        assert exc.code == "SRT_INVALID"
    try:
        upload_project_asset(
            project_id,
            asset_role="subtitle",
            content="1\n00:00:02,000 --> 00:00:01,000\nreversed\n".encode("utf-8"),
            filename="rev.srt",
        )
        raise AssertionError("倒序时间轴应被拒绝")
    except AssetUploadError as exc:
        assert exc.code == "SRT_INVALID"
    try:
        save_assembly_settings(project_id, {"subtitle_enabled": True, "subtitle_srt_path": srt["asset"]["file_path"]})
    except AssemblyError as exc:
        if "FFmpeg" not in str(exc) and "字体" not in str(exc):
            raise
        save_assembly_settings(project_id, {"subtitle_enabled": False, "subtitle_srt_path": srt["asset"]["file_path"]})
        skip("本机无法烧录字幕，已保存当前项目 SRT 路径")
    restored_sub = get_assembly_settings_payload(project_id)
    assert restored_sub["settings"]["subtitle_srt_path"] == srt["asset"]["file_path"]
    other = get_project(other_id)
    assert not any(item.get("file_path") == srt["asset"]["file_path"] for item in other.get("assets") or [])
    pass_("合法 SRT 上传与文本生成，非法时间轴拒绝，项目切换不串字幕")


def test_http_upload_and_cleanup() -> None:
    client = TestClient(app)
    project_id = _project("P8B HTTP")
    other_id = _project("P8B HTTP隔离")
    shot_id = _shot_id(project_id)
    folder = PROJECTS_DIR / project_id
    before = {path.name for path in folder.glob("*")} if folder.exists() else set()
    ok = client.post(
        f"/api/projects/{project_id}/assets/upload",
        data={"asset_role": "first_frame", "shot_id": shot_id, "path": r"C:\Windows\secret.jpg"},
        files={"file": ("note.jpg", JPEG_BYTES, "image/jpeg")},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["ok"] is True
    assert body["asset"]["id"].startswith("asset_")
    assert body["asset"]["file_path"].startswith(f"/assets/{project_id}/")
    assert "C:\\" not in json.dumps(body)
    assert "data:image" not in json.dumps(body).lower()
    missing = client.post(
        "/api/projects/missing/assets/upload",
        data={"asset_role": "keyframe"},
        files={"file": ("x.jpg", JPEG_BYTES, "image/jpeg")},
    )
    assert missing.status_code == 404
    assert "不存在" in missing.json()["detail"]
    svg = client.post(
        f"/api/projects/{project_id}/assets/upload",
        data={"asset_role": "keyframe"},
        files={"file": ("x.jpg", SVG_BYTES, "image/jpeg")},
    )
    assert svg.status_code == 400
    assert "traceback" not in svg.json()["detail"].lower()
    assert "ffmpeg" not in svg.json()["detail"].lower()
    foreign = client.post(
        f"/api/projects/{project_id}/assets/upload",
        data={"asset_role": "first_frame", "shot_id": _shot_id(other_id)},
        files={"file": ("x.jpg", JPEG_BYTES, "image/jpeg")},
    )
    assert foreign.status_code == 400
    after_fail = {path.name for path in folder.glob("*")}
    extra = after_fail - before - {Path(body["asset"]["file_path"]).name}
    assert not any(name.endswith(".svg") for name in extra)
    events = client.get(f"/api/projects/{project_id}/job-events").json()
    event_blob = json.dumps(events, ensure_ascii=False)
    assert JPEG_BYTES.hex() not in event_blob
    assert "data:image" not in event_blob.lower()
    pass_("HTTP 上传、路径穿越无效、失败不留孤儿文件、事件不含文件内容")


def main() -> None:
    os.environ.pop("VISIONCRAFT_ALLOW_LIVE_LLM", None)
    os.environ.pop("VISIONCRAFT_ALLOW_LIVE_VISION", None)
    os.environ.pop("VISIONCRAFT_ALLOW_LIVE_VIDEO", None)
    init_environment()
    init_db()
    try:
        test_jpeg_png_and_security()
        test_audio_and_subtitle()
        test_http_upload_and_cleanup()
        print("PASS: P8-B project asset upload (no live network, cost 0)")
        print("INFO: real_network=否 cost_cny=0")
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
