"""录制无人版演示视频：真实界面 + 磁盘上已有的真实产物，零付费、可重复录制。

素材来源与费用边界：
- 全流程片段用 `prepare_v1_demo.prepare()` 造的本地夹具：改编流程走 mock 规划器，
  关键帧/视频/配乐/字幕由 ffmpeg lavfi 生成，成片是真实 MP4。**不调用任何付费接口。**
- 长文本片段用工作区根的真实语料（`output/test_texts/蛊真人100000字.txt`），
  章节树、按章范围、全文检索都是本地算出来的。
- 全程跑在**隔离数据目录**里（默认 `output/demo-session/data`），不碰竹木的真实项目库。

产出：
- `output/demo/visioncraft-demo.mp4`（交付用）
- `output/demo/raw/*.webm`（Playwright 原始录屏，留着方便重新编码）
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

DEMO_DATA_DIR = ROOT / "output" / "demo-session" / "data"
VIDEO_OUT = ROOT / "output" / "demo"
RAW_DIR = VIDEO_OUT / "raw"
CORPUS = ROOT.parent / "output" / "test_texts" / "蛊真人100000字.txt"
LONG_ID = "demolong_main"
LONG_TITLE = "VisionCraft 演示 · 长文本按章选范围"
_BACKEND_LOG_HANDLE = None


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


def _backend_log_path(port: int) -> Path:
    root = DEMO_DATA_DIR.parent
    root.mkdir(parents=True, exist_ok=True)
    return root / f"backend_{port}.log"


def _start_backend() -> tuple[subprocess.Popen, str]:
    global _BACKEND_LOG_HANDLE
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    exe = str(python if python.exists() else sys.executable)
    env = os.environ.copy()
    # 演示是零付费的：把三个 live 开关全部摘掉，杜绝"录着录着真花钱"。
    for key in ("VISIONCRAFT_ALLOW_LIVE_LLM", "VISIONCRAFT_ALLOW_LIVE_VISION", "VISIONCRAFT_ALLOW_LIVE_VIDEO"):
        env.pop(key, None)
    env["VISIONCRAFT_DATA_DIR"] = str(DEMO_DATA_DIR)
    # 8080 段：避开嵌套验收服务的 8013-8018 / 8020-8025 / 8030-8039 与驱动器 8070-8109。
    for port in range(8080, 8090):
        if _port_in_use(port):
            continue
        log_path = _backend_log_path(port)
        _BACKEND_LOG_HANDLE = log_path.open("w", encoding="utf-8")
        proc = subprocess.Popen(
            [exe, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(ROOT),
            env=env,
            stdout=_BACKEND_LOG_HANDLE,
            stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                _BACKEND_LOG_HANDLE.flush()
                err = log_path.read_text(encoding="utf-8", errors="replace")
                raise SystemExit(f"演示后端启动失败：{err[-800:]}")
            if _health_ok(base):
                print(f"INFO: 演示后端 {base}")
                print(f"INFO: 后端日志 {log_path}")
                return proc, base
            time.sleep(0.3)
        proc.terminate()
        proc.wait(timeout=5)
    raise SystemExit("录制失败：8080-8089 端口均不可用。")


def _prepare_long_project() -> str | None:
    if not CORPUS.is_file():
        print(f"WARN: 长文本语料不存在（{CORPUS}），演示将跳过章节树片段")
        return None
    from backend.database import connect, utc_now
    from backend.services.memory_service import index_project_memory
    from backend.services.medium_text_service import run_medium_analysis
    from backend.services.project_service import delete_project

    source = CORPUS.read_text(encoding="utf-8", errors="replace")
    delete_project(LONG_ID)
    shutil.rmtree(DEMO_DATA_DIR / "projects" / LONG_ID, ignore_errors=True)
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode,
             status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (LONG_ID, LONG_TITLE, source, "ink-wash fantasy", "16:9", 45, "auto",
             "created", "direct", now, now),
        )
    run_medium_analysis(LONG_ID)
    index_project_memory(LONG_ID)
    with connect() as conn:
        chapters = conn.execute(
            "SELECT COUNT(*) AS n FROM source_chapters WHERE project_id = ?", (LONG_ID,)
        ).fetchone()["n"]
        chunks = conn.execute(
            "SELECT COUNT(*) AS n FROM source_chunks WHERE project_id = ?", (LONG_ID,)
        ).fetchone()["n"]
    print(f"INFO: 长文本演示项目 {LONG_ID}：{len(source)} 字 / {chapters} 节 / {chunks} 块")
    return LONG_ID


def _encode(raw: Path, target: Path) -> None:
    from tools.p6c_ffmpeg import ensure_process_path, ffmpeg_bin

    ensure_process_path()
    ffmpeg = ffmpeg_bin()
    if not ffmpeg:
        raise SystemExit("录制成功但找不到 ffmpeg，无法转码为 MP4（见 tools/p6c_ffmpeg.py 的 INSTALL_HINT）")
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg, "-y", "-loglevel", "error",
            "-i", str(raw),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-an",
            str(target),
        ],
        check=True,
    )


def _probe(path: Path) -> str:
    from tools.p6c_ffmpeg import probe_media

    info = probe_media(path)
    return (
        f"{info.get('codec')} {info.get('width')}x{info.get('height')} "
        f"{info.get('duration') or 0:.1f}s {path.stat().st_size / 1048576:.1f}MB"
    )


def main() -> None:
    # 隔离数据目录必须在任何 backend.* 导入之前设好：config 在导入时读它，且不读 .env。
    os.environ["VISIONCRAFT_DATA_DIR"] = str(DEMO_DATA_DIR)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    from backend.config import init_environment
    from backend.database import init_db
    from tools.prepare_v1_demo import DEMO_ID, prepare

    init_environment()
    init_db()
    prepared = prepare()
    print(f"INFO: 全流程演示项目 {prepared['project_id']}，镜头 {prepared['shot_count']} 个")
    print(f"INFO: 成片 {prepared.get('final') or '(无：ffmpeg 不可用)'}")
    long_id = _prepare_long_project()

    harness = ROOT / ".playwright-cli"
    server = None
    started = time.time()
    try:
        server, base = _start_backend()
        env = os.environ.copy()
        env["NODE_PATH"] = str(harness / "node_modules")
        env["VISIONCRAFT_BASE_URL"] = base
        env["DEMO_PROJECT_ID"] = DEMO_ID
        env["DEMO_LONG_PROJECT_ID"] = long_id or ""
        env["DEMO_VIDEO_DIR"] = str(RAW_DIR)
        completed = subprocess.run(
            ["node", str(ROOT / "tools" / "record_v1_demo.cjs")], cwd=ROOT, env=env, check=False
        )
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()

    raws = sorted(RAW_DIR.glob("*.webm"), key=lambda item: item.stat().st_mtime)
    if not raws:
        raise SystemExit("录制结束但没有找到 webm 文件")
    raw = raws[-1]
    target = VIDEO_OUT / "visioncraft-demo.mp4"
    _encode(raw, target)
    print(f"INFO: 原始录屏 {raw.name} {raw.stat().st_size / 1048576:.1f}MB")
    print(f"INFO: 交付视频 {target} —— {_probe(target)}")
    print(f"INFO: 全程耗时 {time.time() - started:.0f}s，零付费（三个 live 开关已摘除）")
    print(f"INFO: 演示数据目录 {DEMO_DATA_DIR}（隔离，未触碰真实项目库）")


if __name__ == "__main__":
    main()
