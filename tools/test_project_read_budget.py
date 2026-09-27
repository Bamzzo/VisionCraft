"""项目读取路径的 ffprobe 预算。

背景（2026-09-27 实测）：`get_assembly_status` 为每个就绪镜头调用 `_has_audio_stream`
→ `_ffprobe_json`，而本机单次 ffprobe 约 0.9–1.1 秒。于是 4 镜项目每次
`GET /api/projects/{id}` 约 4.2 秒、`GET .../assembly` 约 8.2 秒；两个
assembly-settings 端点只是做存在性检查，却调用完整的 `get_project()`，又各多付一次。

本用例不测墙钟（机器一忙就假失败），只数**真正起 ffprobe 进程的次数**：
  - 同一批文件、同一份 (path, mtime, size) 只允许起一次进程；
  - 存在性检查不得装配整个项目。
计数点选在 `_QUERY_RUN`（subprocess.run）而不是 `_ffprobe_json`：后者在缓存
命中时也会被进入，用它计数会得出「没有缓存」的假结论。
墙钟只作为副信号（1500ms 上限），因为 `get_project` 本身还有别的开销。
"""
from __future__ import annotations

import os
import shutil
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db, utc_now
from backend.services.asset_service import public_asset_path
from tools.p6c_ffmpeg import INSTALL_HINT, ensure_process_path, ffmpeg_available, make_color_clip, make_sine_wav
from tools.test_p6c_real_assembly import CLIP_SPECS

CREATED: list[str] = []


def _seed_project(title: str) -> str:
    project_id = f"readbudget_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode,
             status, routing_mode, assembly_stale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, "读取预算用例", "cinematic clean realism", "16:9", 5, "auto",
             "production_ready", "direct", 0, now, now),
        )
    CREATED.append(project_id)
    return project_id


def _seed_shots(project_id: str) -> tuple[list[Path], str]:
    """种 4 个就绪镜头 + 一段项目音频，返回（镜头文件列表, 音频的公开路径）。

    返回音频路径是为了让调用方不必先发一次 GET 去后端拿资产列表——
    那次预热会把探测缓存填好，冷读就没法测了。
    """
    folder = PROJECTS_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    clips: list[Path] = []
    for spec in CLIP_SPECS:
        shot_id = f"shot_{uuid.uuid4().hex[:10]}"
        version_id = f"version_{uuid.uuid4().hex[:10]}"
        filename = f"{shot_id}.mp4"
        clip = folder / filename
        make_color_clip(clip, color=spec["color"], size=spec["size"], duration=spec["duration"])
        clips.append(clip)
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
                 "ark", "local-fixture", "readbudget", now),
            )
            conn.execute(
                """INSERT INTO assets
                (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)""",
                (f"asset_{uuid.uuid4().hex[:10]}", project_id, f"镜头 {spec['index']}", "夹具", spec["color"],
                 video_path, "provider:ark:local-fixture", now),
            )
    audio_name = f"bg_{uuid.uuid4().hex[:8]}.wav"
    make_sine_wav(folder / audio_name, duration=1.2)
    audio_public = public_asset_path(project_id, audio_name)
    with connect() as conn:
        conn.execute(
            """INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, 'audio', ?, ?, ?, ?, ?, ?)""",
            (f"asset_{uuid.uuid4().hex[:10]}", project_id, "背景正弦音", "sine", "sine",
             audio_public, "provider:ffmpeg:local-audio", now),
        )
    return clips, audio_public


def _cleanup() -> None:
    for project_id in CREATED:
        if not str(project_id).startswith("readbudget_"):
            continue
        with connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)


class ProbeCounter:
    """数**真正起 ffprobe 进程**的次数。

    口径很重要：不要包 `_ffprobe_json`。缓存命中时那个函数照样会被进入，
    包它会得出「每次都在探测」的假象（本用例第一版就栽在这里：明明第二轮
    只用了 196ms，计数器却报 4 次）。唯一可靠的计数点是
    `video_service._QUERY_RUN`（= `subprocess.run`）；只统计带
    `-show_streams` 的调用，避免把成片合成用的 ffmpeg 也算进来。
    """

    def __init__(self) -> None:
        from backend.services import video_service

        self.module = video_service
        self.original = video_service._QUERY_RUN
        self.calls: list[str] = []

        def wrapper(args, *rest, **kwargs):
            argv = [str(item) for item in (args or [])]
            if "-show_streams" in argv:
                self.calls.append(argv[-1] if argv else "")
            return self.original(args, *rest, **kwargs)

        self.module._QUERY_RUN = wrapper

    def take(self) -> int:
        count = len(self.calls)
        self.calls = []
        return count

    def restore(self) -> None:
        self.module._QUERY_RUN = self.original


def main() -> None:
    ensure_process_path()
    if not ffmpeg_available():
        print("SKIP: 项目读取 ffprobe 预算（本机未安装 FFmpeg，无探测可计量）")
        print(INSTALL_HINT)
        return

    init_environment()
    init_db()
    from fastapi.testclient import TestClient

    from backend.main import app

    counter = ProbeCounter()
    try:
        from backend.services.video_service import invalidate_probe_cache

        project_id = _seed_project("读取预算")
        clips, audio = _seed_shots(project_id)
        shot_count = len(CLIP_SPECS)
        client = TestClient(app)
        payload = {
            "subtitle_enabled": False, "subtitle_text": "", "subtitle_srt_path": "",
            "audio_enabled": True, "audio_asset_path": audio, "audio_volume": 0.4,
            "keep_source_audio": False, "subtitle_font_size": 28, "subtitle_position": "bottom",
        }

        # 显式从冷缓存开始，否则「首次读取必须探测」的断言会被同一进程里的前序状态左右。
        invalidate_probe_cache()
        counter.take()
        first = client.get(f"/api/projects/{project_id}")
        first_probes = counter.take()
        if first.status_code != 200:
            raise SystemExit(f"FAIL: 首次项目读取返回 {first.status_code}")
        print(f"PASS: 首次项目读取状态码 200，探测 {first_probes} 次（镜头数 {shot_count}）")
        if not 0 < first_probes <= shot_count:
            print(f"FAIL: 首次读取探测次数应为 1..{shot_count}，实测 {first_probes}")
            raise SystemExit(1)
        print(f"PASS: 首次读取探测次数在预算内（1..{shot_count}），实测 {first_probes}")

        started = time.time()
        second = client.get(f"/api/projects/{project_id}")
        second_ms = int((time.time() - started) * 1000)
        second_probes = counter.take()
        if second.status_code != 200:
            raise SystemExit(f"FAIL: 二次项目读取返回 {second.status_code}")
        if second_probes != 0:
            print(f"FAIL: 同一批未改动文件二次读取应复用探测结果，实测又探测 {second_probes} 次")
            raise SystemExit(1)
        print(f"PASS: 二次读取复用探测缓存（新探测 0 次，用时 {second_ms}ms）")
        if second_ms >= 1500:
            print(f"FAIL: 二次读取应在 1500ms 内完成（本机单次 ffprobe 约 1s），实测 {second_ms}ms")
            raise SystemExit(1)
        print(f"PASS: 二次读取用时 {second_ms}ms < 1500ms（无缓存时约 {shot_count}s）")

        client.get(f"/api/projects/{project_id}/assembly")
        assembly_probes = counter.take()
        if assembly_probes != 0:
            print(f"FAIL: 成片状态读取应复用同一批探测结果，实测新探测 {assembly_probes} 次")
            raise SystemExit(1)
        print("PASS: 成片状态读取未重复探测")

        client.get(f"/api/projects/{project_id}/assembly-settings")
        get_probes = counter.take()
        if get_probes != 0:
            print(f"FAIL: 读取成片配置只为存在性检查，不应装配整个项目（实测探测 {get_probes} 次）")
            raise SystemExit(1)
        print("PASS: 读取成片配置未触发任何探测")

        saved = client.put(f"/api/projects/{project_id}/assembly-settings", json=payload)
        put_probes = counter.take()
        if saved.status_code != 200:
            raise SystemExit(f"FAIL: 保存成片配置返回 {saved.status_code} {saved.text[:200]}")
        if put_probes != 0:
            print(f"FAIL: 保存成片配置只为存在性检查，不应装配整个项目（实测探测 {put_probes} 次）")
            raise SystemExit(1)
        print("PASS: 保存成片配置未触发任何探测（返回 stale=%s）" % saved.json().get("stale"))

        clip = clips[0]
        stat = clip.stat()
        os.utime(clip, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        client.get(f"/api/projects/{project_id}")
        after_touch = counter.take()
        if after_touch < 1:
            print("FAIL: 镜头文件被改动后应重新探测，实测 0 次（缓存键失效）")
            raise SystemExit(1)
        print(f"PASS: 文件改动后缓存失效并重新探测（{after_touch} 次）")

        client.get("/api/projects")
        list_probes = counter.take()
        if list_probes != 0:
            print(f"FAIL: 项目列表不应触发探测，实测 {list_probes} 次")
            raise SystemExit(1)
        print("PASS: 项目列表未触发任何探测")
    finally:
        counter.restore()
        _cleanup()


if __name__ == "__main__":
    main()
