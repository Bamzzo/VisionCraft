"""准备 P6 的三条固定演示样本。

路线图 P6 要求三条固定演示样本：短文本完整闭环、不同模型同镜头对比、
失败恢复案例。此前只有第一条以本地夹具（lavfi 彩条）形式存在。

本脚本把三条都做成**可重复准备**的固定样本，并且坚持两条底线：

1. 不拿彩条冒充 AI 视频。镜头视频一律取自 2026-08-28 / 08-31 那批真实
   付费产物（复制文件，不重新生成、不重新计费）；失败案例的错误原文
   逐字取自真实调用记录。
2. 准备过程零外发。改编走本地 mock 规划器，成片由本地 FFmpeg 合成，
   不调用任何图片/视频/语音/LLM API，也不写 `live_*_call_count`。

只创建或重置 ``p6demo_*`` 项目，从不触碰 ``project_*`` 用户数据。

用法::

    .venv\\Scripts\\python.exe tools\\prepare_p6_demo_samples.py
    .venv\\Scripts\\python.exe tools\\prepare_p6_demo_samples.py --only story
    .venv\\Scripts\\python.exe tools\\prepare_p6_demo_samples.py --clean
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import PROJECTS_DIR, init_environment  # noqa: E402
from backend.database import connect, init_db, utc_now  # noqa: E402
from backend.services.adaptation_service import (  # noqa: E402
    confirm_bible,
    confirm_scope,
    confirm_storyboard,
    list_adaptation_options,
    start_adaptation_workflow,
)
from backend.services.asset_service import public_asset_path  # noqa: E402
from backend.services.project_service import delete_project  # noqa: E402
from backend.services.video_service import (  # noqa: E402
    AssemblyError,
    assemble_project_video,
    enqueue_project_assembly,
    save_assembly_settings,
)
from tools.p6c_ffmpeg import (  # noqa: E402
    INSTALL_HINT,
    ensure_process_path,
    ffmpeg_available,
    make_sine_wav,
    probe_media,
    probe_video,
)

DEMO_PREFIX = "p6demo_"

#: 真实源资产的所在地。默认就是工作数据库的项目目录；验收时可以用
#: ``--source-projects-dir`` 指过去，从而在隔离数据目录里建样本而不污染真实库。
SOURCE_PROJECTS_DIR: Path = PROJECTS_DIR

STORY_ID = "p6demo_story"
STORY_TITLE = "P6 演示 A · 短文本完整闭环"

COMPARE_ID = "p6demo_compare"
COMPARE_TITLE = "P6 演示 B · 同镜头多模型对比"

RECOVERY_ID = "p6demo_recovery"
RECOVERY_TITLE = "P6 演示 C · 失败恢复案例"

#: 短文本闭环的四镜真实产物来源（2026-08-28 火山方舟 doubao-seedance-2-0 付费调用）。
STORY_SOURCE_PROJECT = "project_5fdac03f50"
STORY_SHOT_SPECS = (
    ("asset_7dd1def30b.mp4", 5.088),
    ("asset_6b0c7f32b4.mp4", 5.062),
    ("asset_224744e8f0.mp4", 5.088),
    ("asset_2dbbea16da.mp4", 5.088),
)

#: 同镜头对比：同一提示词、同一首帧，三家 Provider 各跑一次（2026-08-28 真实付费）。
#: 三家的输出分辨率与时长都不一样，这正是该样本要展示的能力差异。
COMPARE_PROMPT = (
    "A cinematic fantasy character stands in a windswept, misty landscape. "
    "His long black hair and dark robe move naturally in the wind. "
    "A bright green butterfly-like spirit gently flutters on his left, while a "
    "small golden insect on his right shoulder emits a soft glow. Slow camera "
    "push-in, stable character identity, ink-wash fantasy illustration style, "
    "no text, no watermark, no logo. 固定首帧 I2V 兼容性验证."
)
COMPARE_SOURCES = (
    {
        "version_number": 1,
        "provider": "ark",
        "model": "doubao-seedance-2-0-260128",
        "src_project": "i2v_ark_b97ea96a",
        "src_asset": "asset_0fab834ac2.mp4",
        "requested": "720p / 5s / 16:9",
    },
    {
        "version_number": 2,
        "provider": "dashscope",
        "model": "wan2.7-i2v",
        "src_project": "i2v_dashscope_612ff237",
        "src_asset": "asset_89afa38ec3.mp4",
        "requested": "720P / 2s",
    },
    {
        "version_number": 3,
        "provider": "minimax",
        "model": "MiniMax-H3",
        "src_project": "i2v_minimax_f12c30c7",
        "src_asset": "asset_edb963f99f.mp4",
        "requested": "768P / 4s",
    },
)
COMPARE_FIRST_FRAME = ("i2v_ark_b97ea96a", "asset_918aceaf9c.jpg")

#: 失败恢复：错误原文逐字取自 2026-08-31 对 MiniMax-H3 的一次真实调用。
#: 平台侧任务已不存在，回查返回 HTTP 400 invalid task_id (2013)。
RECOVERY_FAILURE = {
    "provider": "minimax",
    "model": "MiniMax-H3",
    "remote_task_id": "demo-436764667060538",
    "cloud_status": "invalid_task_id",
    "error_code": "TASK_ID_INVALID",
    "error_message": (
        "MiniMax query returned HTTP 400 invalid task_id (2013); "
        "task no longer exists platform-side, request_id "
        "06fe8f6d03411586041c21721c59fc1a"
    ),
    "status_payload": '{"task_id": "436764667060538"}',
    "source_note": (
        "错误原文取自 2026-08-31 project_a43afde7c5 对 MiniMax-H3 的真实调用；"
        "该次调用产生真实费用，本样本只复制记录、不重发请求"
    ),
}

SAMPLE_TEXT = (
    "方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。"
    "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。"
    "但是族中长老已经设下阻碍，他只能选择冒险一搏。"
    "最终他停在山门前，留下未说完的话。"
)


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def is_script_project(project_id: str) -> bool:
    return str(project_id).startswith(DEMO_PREFIX)


def _source_file(project_id: str, filename: str) -> Path:
    """定位真实源资产。缺失就明确失败——这份素材不能凭空生成。"""
    path = SOURCE_PROJECTS_DIR / project_id / filename
    if not path.is_file():
        raise SystemExit(
            f"缺少源资产：{path}\n"
            f"这是既有的真实付费产物，不能凭空生成或改用夹具替代。\n"
            f"请确认 {project_id} 未被清理；如已迁移，请先把该文件放回上述路径，"
            f"或用 --source-projects-dir 指向它所在的项目目录。"
        )
    return path


def _new_asset_id() -> str:
    return f"asset_{uuid.uuid4().hex[:10]}"


def _new_version_id() -> str:
    return f"version_{uuid.uuid4().hex[:10]}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_media(src: Path, project_id: str, filename: str) -> str:
    """把真实产物复制进演示项目，返回可公开访问的 URL 路径。"""
    folder = PROJECTS_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / filename
    if not dest.exists() or dest.stat().st_size != src.stat().st_size:
        shutil.copy2(src, dest)
    return public_asset_path(project_id, filename)


def _project_source_text(project_id: str) -> str:
    with connect() as conn:
        row = conn.execute(
            "SELECT source_text FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
    if row and (row["source_text"] or "").strip():
        return row["source_text"]
    return SAMPLE_TEXT


def _insert_project(project_id: str, title: str, source_text: str, *, duration: int = 5) -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, output_resolution,
             shot_count_mode, status, routing_mode, assembly_stale, generation_mode,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, source_text, "cinematic clean realism", "16:9", duration,
             "1280x720", "auto", "created", "direct", 0, "mock", now, now),
        )


def _run_adaptation(project_id: str) -> None:
    """本地 mock 改编，把方案/Bible/分镜各阶段数据补齐。不调用外部 API。"""
    start_adaptation_workflow(project_id)
    options = list_adaptation_options(project_id)
    if not options:
        raise RuntimeError("演示改编方案生成失败（mock 规划器未产出候选）。")
    confirm_scope(project_id, options[0]["id"])
    confirm_bible(project_id)
    confirm_storyboard(project_id)


def _shots(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM shots WHERE project_id = ? ORDER BY shot_index",
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _trim_shots(project_id: str, keep: int) -> list[dict]:
    """只留前 N 个镜头，方便把样本收敛成单镜头（CASCADE 会带走版本）。"""
    shots = _shots(project_id)
    if len(shots) < keep:
        raise RuntimeError(f"{project_id} 只有 {len(shots)} 个镜头，少于需要的 {keep} 个。")
    for shot in shots[keep:]:
        with connect() as conn:
            conn.execute("DELETE FROM shots WHERE id = ?", (shot["id"],))
    return shots[:keep]


def _register_video_asset(
    project_id: str,
    *,
    name: str,
    description: str,
    prompt: str,
    public_path: str,
    local: Path,
    provider: str | None,
    model: str | None,
    role: str = "shot_video",
    source_task_id: str | None = None,
) -> str:
    info = probe_video(local) if ffmpeg_available() else {}
    asset_id = _new_asset_id()
    with connect() as conn:
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref,
             created_at, mime_type, byte_size, sha256, width, height, source_provider,
             source_model, source_task_id, asset_role, duration_seconds, source)
            VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                asset_id, project_id, name, description, prompt, public_path,
                f"provider:{provider or 'unknown'}:{model or 'unknown'}", utc_now(),
                "video/mp4", local.stat().st_size, _sha256(local),
                info.get("width"), info.get("height"), provider, model,
                source_task_id, role, info.get("duration"), "p6-demo-sample",
            ),
        )
    return asset_id


# --------------------------------------------------------------------------- #
# 样本 A：短文本完整闭环
# --------------------------------------------------------------------------- #
def _prepare_story() -> dict:
    existing = None
    with connect() as conn:
        existing = conn.execute("SELECT id FROM projects WHERE id = ?", (STORY_ID,)).fetchone()
    if existing:
        delete_project(STORY_ID)
        print(f"INFO: 复用前已重置 {STORY_ID}")

    _insert_project(
        STORY_ID, STORY_TITLE,
        _project_source_text(STORY_SOURCE_PROJECT),
        duration=20,
    )
    _run_adaptation(STORY_ID)
    shots = _trim_shots(STORY_ID, len(STORY_SHOT_SPECS))

    # 用源项目真实分镜的文本覆盖 mock 骨架，避免画面与描述对不上。
    with connect() as conn:
        source_shots = conn.execute(
            "SELECT shot_index, title, description, visual_prompt, scene, characters, camera_motion"
            " FROM shots WHERE project_id = ? ORDER BY shot_index",
            (STORY_SOURCE_PROJECT,),
        ).fetchall()
    texts = {row["shot_index"]: dict(row) for row in source_shots}

    used = []
    for index, shot in enumerate(shots):
        spec_name, spec_duration = STORY_SHOT_SPECS[index]
        src = _source_file(STORY_SOURCE_PROJECT, spec_name)
        filename = f"{shot['id']}_{spec_name}"
        public_path = _copy_media(src, STORY_ID, filename)
        local = PROJECTS_DIR / STORY_ID / filename
        version_id = shot.get("current_version_id")
        text = texts.get(shot["shot_index"]) or {}
        info = probe_video(local)
        note = (
            f"真实产物复制自 {STORY_SOURCE_PROJECT}/{spec_name}；"
            f"原生成：ark / doubao-seedance-2-0-260128"
        )
        with connect() as conn:
            conn.execute(
                """UPDATE shots
                   SET title = ?, description = ?, visual_prompt = ?, scene = ?,
                       characters = ?, camera_motion = ?, status = ?, duration_seconds = ?,
                       updated_at = ?
                   WHERE id = ? AND project_id = ?""",
                (
                    text.get("title") or shot["title"],
                    text.get("description") or shot["description"],
                    text.get("visual_prompt") or shot["visual_prompt"],
                    text.get("scene") or shot["scene"],
                    text.get("characters") or shot["characters"],
                    text.get("camera_motion") or shot["camera_motion"],
                    "video_ready", int(round(info.get("duration") or spec_duration)),
                    utc_now(), shot["id"], STORY_ID,
                ),
            )
            conn.execute(
                """UPDATE shot_versions
                   SET video_path = ?, video_mode = 'i2v', provider = ?, model = ?,
                       duration_seconds = ?, change_summary = ?
                   WHERE id = ? AND shot_id = ?""",
                (public_path, "ark", "doubao-seedance-2-0-260128",
                 int(round(info.get("duration") or spec_duration)), note,
                 version_id, shot["id"]),
            )
        _register_video_asset(
            STORY_ID,
            name=f"{text.get('title') or shot['title']} Video",
            description=note,
            prompt=text.get("visual_prompt") or shot["visual_prompt"],
            public_path=public_path,
            local=local,
            provider="ark",
            model="doubao-seedance-2-0-260128",
        )
        used.append(shot)

    # 本地音频 + 字幕，然后真实合成一版成片。
    folder = PROJECTS_DIR / STORY_ID
    settings = {
        "subtitle_enabled": False,
        "subtitle_text": "",
        "subtitle_srt_path": "",
        "audio_enabled": False,
        "audio_asset_path": "",
        "audio_volume": 0.35,
        "keep_source_audio": True,
        "subtitle_font_size": 28,
        "subtitle_position": "bottom",
    }
    if ffmpeg_available():
        audio_name = "p6demo_bg.wav"
        make_sine_wav(folder / audio_name, duration=2.0, frequency=196)
        audio_path = public_asset_path(STORY_ID, audio_name)
        srt_name = "p6demo.srt"
        (folder / srt_name).write_text(
            "1\n00:00:00,000 --> 00:00:05,000\n他知进入场景，气氛尚未点破。\n\n"
            "2\n00:00:05,000 --> 00:00:10,000\n他知必须立刻回应眼前的压力。\n\n"
            "3\n00:00:10,000 --> 00:00:15,000\n他知直面冲突。\n\n"
            "4\n00:00:15,000 --> 00:00:20,000\n短片停在悬念上。\n",
            encoding="utf-8",
        )
        srt_path = public_asset_path(STORY_ID, srt_name)
        settings.update({
            "subtitle_enabled": True,
            "subtitle_text": "他知进入场景，气氛尚未点破。",
            "subtitle_srt_path": srt_path,
            "audio_enabled": True,
            "audio_asset_path": audio_path,
        })
        with connect() as conn:
            conn.execute(
                """INSERT INTO assets
                (id, project_id, type, name, description, prompt, file_path, created_at)
                VALUES (?, ?, 'audio', ?, ?, ?, ?, ?)""",
                (_new_asset_id(), STORY_ID, "演示背景音", "本地 sine 波",
                 "sine", audio_path, utc_now()),
            )
            conn.execute(
                """INSERT INTO assets
                (id, project_id, type, name, description, prompt, file_path, created_at)
                VALUES (?, ?, 'subtitle', ?, ?, ?, ?, ?)""",
                (_new_asset_id(), STORY_ID, "演示字幕", "本地 SRT", "srt",
                 srt_path, utc_now()),
            )
    try:
        save_assembly_settings(STORY_ID, settings)
    except AssemblyError as exc:
        settings["subtitle_enabled"] = False
        settings["audio_enabled"] = False
        print(f"INFO: 成片包装配置降级：{exc}")
        save_assembly_settings(STORY_ID, settings)

    assembled = None
    if ffmpeg_available():
        plan = enqueue_project_assembly(STORY_ID)
        assemble_project_video(STORY_ID, plan["job_id"])
        with connect() as conn:
            row = conn.execute(
                "SELECT file_path FROM assets WHERE project_id = ? AND type = 'final-video'"
                " ORDER BY created_at DESC LIMIT 1",
                (STORY_ID,),
            ).fetchone()
        if row:
            assembled = row["file_path"]
            local = PROJECTS_DIR / STORY_ID / Path(assembled).name
            info = probe_media(local)
            print(f"INFO: {STORY_ID} 成片 {Path(assembled).name} "
                  f"{info.get('width')}x{info.get('height')} {info.get('duration'):.2f}s "
                  f"audio={info.get('audio_codec') or 'none'}")
    else:
        print("SKIP: 未生成本地成片，因为没有 FFmpeg。")
        print(INSTALL_HINT)

    return {"project_id": STORY_ID, "shots": len(used), "final": assembled}


# --------------------------------------------------------------------------- #
# 样本 B：同镜头多模型对比
# --------------------------------------------------------------------------- #
def _prepare_compare() -> dict:
    with connect() as conn:
        existing = conn.execute("SELECT id FROM projects WHERE id = ?", (COMPARE_ID,)).fetchone()
    if existing:
        delete_project(COMPARE_ID)
        print(f"INFO: 复用前已重置 {COMPARE_ID}")

    _insert_project(COMPARE_ID, COMPARE_TITLE, COMPARE_PROMPT, duration=5)
    _run_adaptation(COMPARE_ID)
    shot = _trim_shots(COMPARE_ID, 1)[0]

    frame_src = _source_file(*COMPARE_FIRST_FRAME)
    frame_name = f"{shot['id']}_first.jpg"
    frame_public = _copy_media(frame_src, COMPARE_ID, frame_name)

    # 改编流程给每个镜头建了一个自动版本，那不是"某家模型的输出"。
    # 清掉它，让这个镜头下只剩三家真实产物，版本历史才对得上演示口径。
    with connect() as conn:
        conn.execute("DELETE FROM shot_versions WHERE shot_id = ?", (shot["id"],))
        conn.execute(
            "UPDATE shots SET current_version_id = NULL WHERE id = ?", (shot["id"],)
        )

    versions = []
    for spec in COMPARE_SOURCES:
        src = _source_file(spec["src_project"], spec["src_asset"])
        filename = f"{shot['id']}_v{spec['version_number']}_{spec['src_asset']}"
        public_path = _copy_media(src, COMPARE_ID, filename)
        local = PROJECTS_DIR / COMPARE_ID / filename
        info = probe_video(local)
        note = (
            f"同一提示词、同一首帧；{spec['provider']} / {spec['model']}，"
            f"请求 {spec['requested']}，实际输出 "
            f"{info.get('width')}x{info.get('height')} / {info.get('duration'):.2f}s"
        )
        version_id = _new_version_id()
        with connect() as conn:
            conn.execute(
                """INSERT INTO shot_versions
                (id, shot_id, version_number, description, visual_prompt, negative_prompt,
                 audio_prompt, first_frame_path, last_frame_path, video_path, video_mode,
                 created_by, created_at, provider, model, camera_motion, duration_seconds,
                 reference_frame_path, change_summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    version_id, shot["id"], spec["version_number"],
                    f"{spec['provider']} / {spec['model']} 的输出",
                    COMPARE_PROMPT, "", "", frame_public, None, public_path, "i2v",
                    "p6demo", utc_now(), spec["provider"], spec["model"],
                    "慢推", int(round(info.get("duration") or 0)), frame_public, note,
                ),
            )
        _register_video_asset(
            COMPARE_ID,
            name=f"{spec['provider']} / {spec['model']}",
            description=note,
            prompt=COMPARE_PROMPT,
            public_path=public_path,
            local=local,
            provider=spec["provider"],
            model=spec["model"],
        )
        versions.append({"version_id": version_id, **spec, "probe": info})

    # 当前版本指向第一家；另外两家留在版本历史里供对比。
    first = versions[0]
    with connect() as conn:
        conn.execute(
            """UPDATE shots
               SET title = ?, description = ?, visual_prompt = ?, scene = ?, characters = ?,
                   camera_motion = ?, status = ?, current_version_id = ?, duration_seconds = ?,
                   updated_at = ?
               WHERE id = ? AND project_id = ?""",
            (
                "同镜头三模型对比 · 固定首帧 I2V",
                "同一个镜头、同一提示词、同一张首帧，分别交给三家视频模型生成，"
                "输出规格与动态表现各不相同。",
                COMPARE_PROMPT, "misty landscape", '["方源"]', "慢推",
                "video_ready", first["version_id"],
                int(round(first["probe"].get("duration") or 5)),
                utc_now(), shot["id"], COMPARE_ID,
            ),
        )
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, created_at, asset_role)
            VALUES (?, ?, 'first-frame', ?, ?, ?, ?, ?, ?)""",
            (_new_asset_id(), COMPARE_ID, "固定首帧 gyfy.jpg",
             "三家 Provider 共用的同一张首帧", "keyframe", frame_public, utc_now(),
             "first_frame"),
        )
    print(f"INFO: {COMPARE_ID} 已挂 {len(versions)} 个版本供同镜头对比")
    return {"project_id": COMPARE_ID, "versions": len(versions)}


# --------------------------------------------------------------------------- #
# 样本 C：失败恢复案例
# --------------------------------------------------------------------------- #
def _prepare_recovery() -> dict:
    with connect() as conn:
        existing = conn.execute("SELECT id FROM projects WHERE id = ?", (RECOVERY_ID,)).fetchone()
    if existing:
        delete_project(RECOVERY_ID)
        print(f"INFO: 复用前已重置 {RECOVERY_ID}")

    _insert_project(RECOVERY_ID, RECOVERY_TITLE, SAMPLE_TEXT, duration=5)
    _run_adaptation(RECOVERY_ID)
    shot = _trim_shots(RECOVERY_ID, 1)[0]

    frame_src = _source_file(*COMPARE_FIRST_FRAME)
    frame_name = f"{shot['id']}_first.jpg"
    frame_public = _copy_media(frame_src, RECOVERY_ID, frame_name)

    failure = RECOVERY_FAILURE
    version_id = shot.get("current_version_id")
    with connect() as conn:
        conn.execute(
            """UPDATE shots
               SET title = ?, description = ?, status = ?, visual_prompt = ?, scene = ?,
                   characters = ?, camera_motion = ?, retry_count = 1, updated_at = ?
               WHERE id = ? AND project_id = ?""",
            (
                "失败恢复 · 云端任务丢失",
                "提交后云端任务在平台侧不存在，回查返回 HTTP 400 invalid task_id，"
                "镜头进入 video_failed；后续镜头被阻止继续提交，等待人工处置。",
                "video_failed",
                "Cinematic clean realism, golden hour light, a young man with short hair "
                "standing under a large old locust tree at a village entrance, looking up "
                "at the canopy, leaves rustling, cicadas singing, fields in background.",
                "村口老树", '["少年"]', "仰拍",
                utc_now(), shot["id"], RECOVERY_ID,
            ),
        )
        conn.execute(
            """UPDATE shot_versions
               SET video_path = NULL, first_frame_path = ?, provider = ?, model = ?,
                   change_summary = ?
               WHERE id = ? AND shot_id = ?""",
            (frame_public, failure["provider"], failure["model"],
             failure["source_note"], version_id, shot["id"]),
        )
        conn.execute(
            """INSERT INTO video_tasks
            (id, project_id, shot_id, version_id, job_id, provider, model, remote_task_id,
             status, cloud_status, prompt, submit_payload, status_payload, video_url,
             result_path, error_code, error_message, created_at, updated_at)
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?, 'failed', ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)""",
            (
                f"vt_{uuid.uuid4().hex[:10]}", RECOVERY_ID, shot["id"], version_id,
                failure["provider"], failure["model"], failure["remote_task_id"],
                failure["cloud_status"], "少年站在老槐树下，仰望树冠，蝉鸣阵阵",
                '{"model": "MiniMax-H3", "resolution": "768P", "duration": 4}',
                failure["status_payload"], failure["error_code"], failure["error_message"],
                utc_now(), utc_now(),
            ),
        )
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, created_at, asset_role)
            VALUES (?, ?, 'first-frame', ?, ?, ?, ?, ?, ?)""",
            (_new_asset_id(), RECOVERY_ID, "固定首帧 gyfy.jpg",
             "失败镜头提交时使用的首帧", "keyframe", frame_public, utc_now(), "first_frame"),
        )
    print(f"INFO: {RECOVERY_ID} 已写入真实失败记录（{failure['error_code']}）")
    return {"project_id": RECOVERY_ID, "error_code": failure["error_code"]}


# --------------------------------------------------------------------------- #
# 清理与入口
# --------------------------------------------------------------------------- #
def list_script_projects() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id FROM projects WHERE id LIKE ? ORDER BY id",
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


PREPARERS = {
    "story": _prepare_story,
    "compare": _prepare_compare,
    "recovery": _prepare_recovery,
}


def main() -> None:
    global SOURCE_PROJECTS_DIR

    parser = argparse.ArgumentParser(description="准备或清理 VisionCraft P6 固定演示样本")
    parser.add_argument("--clean", action="store_true", help="只删除本脚本创建的 p6demo_* 项目")
    parser.add_argument(
        "--only",
        default="",
        help="只准备指定样本，逗号分隔：story,compare,recovery",
    )
    parser.add_argument(
        "--source-projects-dir",
        default="",
        help="真实源资产所在的项目目录（默认使用当前数据目录的 projects/）",
    )
    args = parser.parse_args()

    if args.source_projects_dir:
        SOURCE_PROJECTS_DIR = Path(args.source_projects_dir).resolve()
        if not SOURCE_PROJECTS_DIR.is_dir():
            raise SystemExit(f"--source-projects-dir 不存在：{SOURCE_PROJECTS_DIR}")
        print(f"INFO: 源资产目录 {SOURCE_PROJECTS_DIR}")

    init_environment()
    init_db()
    ensure_process_path()

    if args.clean:
        removed = clean_script_projects()
        if not removed:
            print("INFO: 没有可清理的 p6demo_* 项目。")
        print("INFO: 未删除任何非 p6demo_ 用户项目。")
        return

    selected = [name.strip() for name in args.only.split(",") if name.strip()] or list(PREPARERS)
    unknown = [name for name in selected if name not in PREPARERS]
    if unknown:
        raise SystemExit(f"未知样本名：{', '.join(unknown)}；可选 {', '.join(PREPARERS)}")

    results = {}
    for name in selected:
        print(f"--- 准备样本 {name} ---")
        results[name] = PREPARERS[name]()
        print()

    print(f"INFO: ffmpeg {'可用' if ffmpeg_available() else '不可用'}")
    for name, result in results.items():
        print(f"INFO: {name} -> {result}")
    print()
    print("打开工作台后可直接选择这三个「P6 演示」项目。")
    print("清理：.venv\\Scripts\\python.exe tools\\prepare_p6_demo_samples.py --clean")


if __name__ == "__main__":
    main()
