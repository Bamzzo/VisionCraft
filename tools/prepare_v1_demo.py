"""准备固定 V1 演示项目。只创建/复用 v1demo_*，默认不删除用户项目。"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db, utc_now
from backend.services.adaptation_service import (
    confirm_bible,
    confirm_scope,
    confirm_storyboard,
    list_adaptation_options,
    start_adaptation_workflow,
)
from backend.services.asset_service import public_asset_path
from backend.services.project_service import delete_project, get_project
from backend.services.video_service import (
    AssemblyError,
    assemble_project_video,
    enqueue_project_assembly,
    save_assembly_settings,
)
from tools.p6c_ffmpeg import (
    INSTALL_HINT,
    ensure_process_path,
    ffmpeg_available,
    ffmpeg_version,
    make_color_clip,
    make_color_clip_with_sine,
    make_sine_wav,
    probe_media,
)

DEMO_PREFIX = "v1demo_"
DEMO_ID = "v1demo_main"
DEMO_TITLE = "VisionCraft V1 固定演示"
GYFY_CANDIDATES = [
    ROOT.parent / "gyfy.jpg",
    ROOT / "gyfy.jpg",
    Path(r"D:\Agent\summercompetition\StoryCraft\gyfy.jpg"),
]
SAMPLE = (
    "方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。"
    "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。"
    "但是族中长老已经设下阻碍，他只能选择冒险一搏。"
    "最终他停在山门前，留下未说完的话。"
)
CLIP_SPECS = (
    {"color": "red", "size": "640x360", "duration": 1.2, "frequency": 440},
    {"color": "blue", "size": "1280x720", "duration": 1.4, "frequency": 880},
    {"color": "green", "size": "640x360", "duration": 1.1, "frequency": None},
    {"color": "yellow", "size": "1280x720", "duration": 1.3, "frequency": 660},
)


def is_script_project(project_id: str) -> bool:
    return str(project_id).startswith(DEMO_PREFIX)


def list_script_projects() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id FROM projects WHERE id LIKE ?",
            (f"{DEMO_PREFIX}%",),
        ).fetchall()
    return [row["id"] for row in rows]


def clean_script_projects() -> list[str]:
    removed = []
    for project_id in list_script_projects():
        if not is_script_project(project_id):
            continue
        delete_project(project_id)
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
        removed.append(project_id)
        print(f"CLEANED: {project_id}")
    return removed


def _tiny_png(path: Path) -> None:
    path.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
        )
    )


def _gyfy_source() -> Path | None:
    for candidate in GYFY_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def _write_keyframe(dest: Path, gyfy: Path | None) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if gyfy and gyfy.is_file():
        shutil.copy2(gyfy, dest)
        return str(gyfy)
    _tiny_png(dest)
    return "generated-png"


def _insert_project() -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, output_resolution,
             shot_count_mode, status, routing_mode, assembly_stale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (DEMO_ID, DEMO_TITLE, SAMPLE, "cinematic clean realism", "16:9", 5, "1280x720",
             "auto", "created", "direct", 0, now, now),
        )


def _run_adaptation() -> None:
    start_adaptation_workflow(DEMO_ID)
    options = list_adaptation_options(DEMO_ID)
    if not options:
        raise RuntimeError("演示改编方案生成失败（mock 规划器未产出候选）。")
    confirm_scope(DEMO_ID, options[0]["id"])
    confirm_bible(DEMO_ID)
    confirm_storyboard(DEMO_ID)


def _attach_media() -> dict:
    project = get_project(DEMO_ID)
    shots = project.get("shots") or []
    if len(shots) < 3:
        raise RuntimeError(f"分镜镜头不足 3 个，当前 {len(shots)}。")
    folder = PROJECTS_DIR / DEMO_ID
    folder.mkdir(parents=True, exist_ok=True)
    gyfy = _gyfy_source()
    gyfy_note = str(gyfy) if gyfy else "仓库外 gyfy.jpg 不可用，已生成本地 1x1 PNG 关键帧"
    now = utc_now()
    used = []
    for index, shot in enumerate(shots[:4]):
        spec = CLIP_SPECS[index % len(CLIP_SPECS)]
        shot_id = shot["id"]
        version_id = shot.get("current_version_id")
        filename = f"{shot_id}.mp4"
        local = folder / filename
        if spec["frequency"] and ffmpeg_available():
            make_color_clip_with_sine(
                local,
                color=spec["color"],
                size=spec["size"],
                duration=spec["duration"],
                frequency=spec["frequency"],
            )
        elif ffmpeg_available():
            make_color_clip(local, color=spec["color"], size=spec["size"], duration=spec["duration"])
        else:
            local.write_bytes(b"")
        first_name = f"{shot_id}_first.png"
        last_name = f"{shot_id}_last.png"
        first_src = _write_keyframe(folder / first_name, gyfy if index == 0 else None)
        _write_keyframe(folder / last_name, None)
        video_path = public_asset_path(DEMO_ID, filename) if local.is_file() and local.stat().st_size > 0 else None
        first_path = public_asset_path(DEMO_ID, first_name)
        last_path = public_asset_path(DEMO_ID, last_name)
        with connect() as conn:
            conn.execute(
                """UPDATE shot_versions
                   SET first_frame_path = ?, last_frame_path = ?, video_path = ?,
                       provider = ?, model = ?, created_by = ?, change_summary = ?
                   WHERE id = ? AND shot_id = ?""",
                (first_path, last_path, video_path, "ark", "local-fixture", "v1demo",
                 "演示脚本写入本地夹具，不调用真实 Provider", version_id, shot_id),
            )
            conn.execute(
                "UPDATE shots SET status = ?, updated_at = ? WHERE id = ? AND project_id = ?",
                ("video_ready" if video_path else "keyframes_ready", now, shot_id, DEMO_ID),
            )
            if video_path:
                conn.execute(
                    """INSERT INTO assets
                    (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                    VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)""",
                    (f"asset_{uuid.uuid4().hex[:10]}", DEMO_ID, shot.get("title") or f"镜头 {index + 1}",
                     "本地 lavfi 夹具", spec["color"], video_path, "provider:ark:local-fixture", now),
                )
            conn.execute(
                """INSERT INTO assets
                (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                VALUES (?, ?, 'image', ?, ?, ?, ?, ?, ?)""",
                (f"asset_{uuid.uuid4().hex[:10]}", DEMO_ID, f"{shot.get('title') or '镜头'} 首帧",
                 first_src, "keyframe", first_path, "provider:local:keyframe", now),
            )
        used.append(shot)
        if index == 0:
            extra_id = f"version_{uuid.uuid4().hex[:10]}"
            with connect() as conn:
                conn.execute(
                    """INSERT INTO shot_versions
                    (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
                     first_frame_path, last_frame_path, video_path, video_mode, provider, model,
                     change_summary, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (extra_id, shot_id, 2, shot.get("description") or "第二版", "fixture", "", "",
                     first_path, last_path, video_path, "t2v", "ark", "local-fixture-v2",
                     "P3 演示：同镜头保留历史版本", "v1demo", now),
                )
                conn.execute(
                    "UPDATE shots SET current_version_id = ? WHERE id = ? AND project_id = ?",
                    (extra_id, shot_id, DEMO_ID),
                )
    audio_name = "bg_demo.wav"
    srt_name = "demo.srt"
    if ffmpeg_available():
        make_sine_wav(folder / audio_name, duration=1.6, frequency=220)
    else:
        (folder / audio_name).write_bytes(b"")
    (folder / srt_name).write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n青茅山夜路，传承将起。\n\n"
        "2\n00:00:02,000 --> 00:00:05,000\n方源停在山门前，留下未说完的话。\n",
        encoding="utf-8",
    )
    audio_path = public_asset_path(DEMO_ID, audio_name)
    srt_path = public_asset_path(DEMO_ID, srt_name)
    with connect() as conn:
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, 'audio', ?, ?, ?, ?, ?, ?)""",
            (f"asset_{uuid.uuid4().hex[:10]}", DEMO_ID, "演示背景音", "sine", "sine",
             audio_path, "provider:ffmpeg:local-audio", now),
        )
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, 'subtitle', ?, ?, ?, ?, ?, ?)""",
            (f"asset_{uuid.uuid4().hex[:10]}", DEMO_ID, "演示字幕", "srt", "srt",
             srt_path, "provider:local:srt", now),
        )
    settings = {
        "subtitle_enabled": True,
        "subtitle_text": "青茅山夜路，传承将起。",
        "subtitle_srt_path": srt_path,
        "audio_enabled": bool(ffmpeg_available()),
        "audio_asset_path": audio_path if ffmpeg_available() else "",
        "audio_volume": 0.35,
        "keep_source_audio": True,
        "subtitle_font_size": 28,
        "subtitle_position": "bottom",
    }
    try:
        save_assembly_settings(DEMO_ID, settings)
    except AssemblyError as exc:
        settings["subtitle_enabled"] = False
        settings["subtitle_srt_path"] = ""
        settings["subtitle_text"] = ""
        print(f"INFO: 成片字幕配置降级：{exc}")
        save_assembly_settings(DEMO_ID, settings)
    return {"shot_count": len(used), "gyfy": gyfy_note, "audio": audio_path, "srt": srt_path}


def prepare() -> dict:
    init_environment()
    init_db()
    ensure_process_path()
    existing = get_project(DEMO_ID)
    if existing:
        delete_project(DEMO_ID)
        print(f"INFO: 复用前已重置脚本项目 {DEMO_ID}")
    _insert_project()
    _run_adaptation()
    media = _attach_media()
    assembled = None
    if ffmpeg_available() and media["shot_count"] >= 3:
        plan = enqueue_project_assembly(DEMO_ID)
        assemble_project_video(DEMO_ID, plan["job_id"])
        project = get_project(DEMO_ID)
        final = (project.get("assembly") or {}).get("current_final") or {}
        assembled = final.get("file_path")
        if assembled:
            local = PROJECTS_DIR / DEMO_ID / Path(assembled).name
            info = probe_media(local)
            print(
                f"INFO: 成片 {local} {info.get('codec')} {info.get('width')}x{info.get('height')} "
                f"{info.get('duration'):.2f}s audio={info.get('audio_codec') or 'none'}"
            )
    elif not ffmpeg_available():
        print("SKIP: 未生成本地成片，因为没有 FFmpeg。")
        print(INSTALL_HINT)
    return {"project_id": DEMO_ID, **media, "final": assembled}


def main() -> None:
    parser = argparse.ArgumentParser(description="准备或清理 VisionCraft V1 固定演示项目")
    parser.add_argument("--clean", action="store_true", help="只删除本脚本创建的 v1demo_* 项目")
    args = parser.parse_args()
    init_environment()
    init_db()
    if args.clean:
        removed = clean_script_projects()
        if not removed:
            print("INFO: 没有可清理的 v1demo_* 项目。")
        print("INFO: 未删除任何非 v1demo_ 用户项目。")
        return
    result = prepare()
    print(f"INFO: ffmpeg {ffmpeg_version() or 'unavailable'}")
    print(f"INFO: 演示项目 {result['project_id']} 镜头 {result['shot_count']} 个")
    print(f"INFO: 关键帧来源 {result['gyfy']}")
    print("打开工作台后选择「VisionCraft V1 固定演示」，可查看改编、版本、成片配置与导出。")
    print("重复执行会重置 v1demo_main，不会创建无控制的重复项目。")
    print("清理：.venv\\Scripts\\python.exe tools\\prepare_v1_demo.py --clean")


if __name__ == "__main__":
    main()
