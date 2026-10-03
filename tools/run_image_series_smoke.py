"""Run one paid, verifiable DashScope image **series** call (组图模式).

Why this exists
---------------
`generate_image_series` 是 2026-10-03 为「多镜首帧必须连成一条视觉序列」新增的能力，
但它上线时**调用点为零**——产品里没有任何路径会走到它。未经执行的能力只是声明，
不是事实。这个脚本给它第一个真实调用点，并把产物留在一个可复核的工程里。

它**不自己开闸**：`VISIONCRAFT_ALLOW_LIVE_IMAGE` 必须由调用方在环境里显式给出，
否则直接退出。闸门要是能被工具自己绕过，那道闸门就不算数。

判据（必须能证伪）
------------------
1. **张数**：`--count` 张都要落盘，`assets` 表逐行可查。
2. **尺寸**：每张都必须等于 `--size` 指定的像素值（`宽*高`）。若模型把 16:9 出成方图，
   这里会红——那正是文档里「只写 2K 会出方图」那个坑。
3. **曝光一致性**：用 ffmpeg 把每张缩到 64x36 灰度求平均亮度，**任两张的亮度比不得超过
   `--luma-tolerance`**（默认 1.4）。夜景序列就该整组都暗；10-02「四首帧明暗来回跳」
   的病根在这一条上必然红。
4. **语义不做机器判定**：脚本只报事实，四张图留给竹木看——人眼才是最终判据。

Usage
-----
    # 只报价/只做前置检查（不开闸，必然停在闸门）
    .venv/Scripts/python.exe tools/run_image_series_smoke.py --from-project project_ade652fc91

    # 真跑（一次性、仅当前进程开闸）
    VISIONCRAFT_ALLOW_LIVE_IMAGE=1 VISIONCRAFT_LIVE_BUDGET_CNY=20 \
        .venv/Scripts/python.exe tools/run_image_series_smoke.py --from-project project_ade652fc91 --count 4
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import DATA_DIR, PROJECTS_DIR, init_environment
from backend.database import connect, init_db, utc_now
from backend.providers.image_provider import ImageAssetRequest, generate_image_series
from backend.providers.live_budget import (
    BudgetBlockedError,
    estimate_image_cny,
    live_image_authorized,
    live_max_images,
)
from backend.services.video_service import _ffmpeg_executable

SERIES_INVARIANTS = (
    "同一角色（年轻男子，素色长袍，束发，清冷气质）、同一夜色环境、同一冷色调、同一水墨写意质感。"
    "以下镜头构成**一条连续的分镜序列**，请按序号依次生成，保持角色身份、服装、光线方向完全一致。"
)
SERIES_CONSTRAINTS = (
    "硬约束：每个镜头**只有该角色一人**（不要出现第二个人）；**夜景、有月光**；"
    "不要出现文字、水印、logo、UI 覆盖层。"
)


def _shots_from_project(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT shot_index, title, description, scene, camera_motion, visual_prompt
            FROM shots WHERE project_id = ? ORDER BY shot_index
            """,
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _style_from_project(project_id: str) -> str:
    with connect() as conn:
        row = conn.execute("SELECT style FROM projects WHERE id = ?", (project_id,)).fetchone()
    return (row["style"] if row else "") or "cinematic clean realism, ink-wash wuxia"


def _mean_luma(ffmpeg: str, path: Path) -> float:
    """把图缩到 64x36 灰度后求平均亮度（0-255）。用 ffmpeg 而不是 PIL：本机没有 PIL。"""
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-vf", "scale=64:36,format=gray", "-f", "rawvideo", "-"],
        capture_output=True,
    )
    data = proc.stdout or b""
    if not data:
        raise RuntimeError(f"ffmpeg 未能读出像素：{path} :: {(proc.stderr or b'')[:200]!r}")
    return sum(data) / len(data)


def _probe_size(ffmpeg: str, path: Path) -> tuple[int, int]:
    probe = str(Path(ffmpeg).with_name("ffprobe.exe" if sys.platform == "win32" else "ffprobe"))
    proc = subprocess.run(
        [probe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "json", str(path)],
        capture_output=True, text=True, errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 失败：{path} :: {(proc.stderr or '')[:200]}")
    stream = (json.loads(proc.stdout or "{}").get("streams") or [{}])[0]
    return int(stream.get("width") or 0), int(stream.get("height") or 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-project", default="", help="取该项目的分镜文案作为序列内容")
    parser.add_argument("--count", type=int, default=4, help="要出几张（组图 n，1-12）")
    parser.add_argument("--size", default="1280*720", help="宽*高，像素写法（不是 2K/720p）")
    parser.add_argument("--title", default="", help="新工程标题后缀")
    parser.add_argument("--luma-tolerance", type=float, default=1.4,
                        help="任两张平均亮度的最大允许比值；超过即判曝光不一致")
    args = parser.parse_args()

    init_environment()
    init_db()

    if not args.from_project:
        return _fail("必须用 --from-project 指明分镜来源，脚本不内置任何内容")
    shots = _shots_from_project(args.from_project)
    if not shots:
        return _fail(f"{args.from_project} 没有分镜，无法构造序列")
    if args.count < 1 or args.count > 12:
        return _fail(f"--count 必须在 1-12（组图上限），收到 {args.count}")

    style = _style_from_project(args.from_project)
    beats = " ".join(
        f"[镜头{int(shot['shot_index']):02d}] {shot['description']}"
        f"（场景：{shot['scene']}；运镜：{shot['camera_motion']}）"
        for shot in shots[: args.count]
    )
    prompt = f"{style}。{SERIES_INVARIANTS}{beats}。{SERIES_CONSTRAINTS}"

    cost = estimate_image_cny(args.count)
    plan = {
        "source_project": args.from_project,
        "count": args.count,
        "size": args.size,
        "estimated_cny": round(cost, 4),
        "image_authorized": live_image_authorized(),
        "image_max": live_max_images(),
    }
    print(json.dumps({"plan": plan}, ensure_ascii=False))

    # 闸门必须由调用方在环境里开，本脚本不会替它开。
    if not live_image_authorized():
        return _fail(
            "图像闸门未开：请显式设置 VISIONCRAFT_ALLOW_LIVE_IMAGE=1（本脚本刻意不代开闸门）"
        )

    ffmpeg = _ffmpeg_executable()
    if not ffmpeg:
        return _fail("本机未找到 FFmpeg，无法做曝光一致性核验")

    project_id = f"imgseries_{uuid.uuid4().hex[:8]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode,
             status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, args.title or f"图像组图序列 smoke · 源 {args.from_project}", beats,
             style, "16:9", 5, "fixed", "testing", "direct", now, now),
        )

    request = ImageAssetRequest(
        project_id=project_id,
        asset_type="first-frame",
        name="序列首帧",
        description=f"4 镜连续分镜序列（源：{args.from_project}）",
        prompt=prompt,
        accent="#2563eb",
    )
    try:
        asset_ids = generate_image_series(request, count=args.count)
    except BudgetBlockedError as exc:
        return _fail(f"被闸门拦下：{exc}")

    with connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT id, file_path, width, height, byte_size, source_provider, source_model "
                "FROM assets WHERE id IN (%s) ORDER BY created_at" % ",".join("?" * len(asset_ids)),
                tuple(asset_ids),
            )
        ]
        used = conn.execute("SELECT live_image_count FROM projects WHERE id = ?", (project_id,)).fetchone()

    want_w, want_h = (int(x) for x in args.size.split("*"))
    checks: list[str] = []
    ok = True
    if len(rows) != args.count:
        ok = False
        checks.append(f"FAIL: 期望 {args.count} 张，实际落盘 {len(rows)} 张")
    lumas: list[float] = []
    for index, row in enumerate(rows, start=1):
        # 公开路径是 /assets/<project>/<file>；磁盘上是 PROJECTS_DIR/<project>/<file>。
        path = PROJECTS_DIR / project_id / Path(row["file_path"]).name
        width, height = _probe_size(ffmpeg, path)
        luma = _mean_luma(ffmpeg, path)
        lumas.append(luma)
        size_ok = (width, height) == (want_w, want_h)
        if not size_ok:
            ok = False
        checks.append(
            f"{'PASS' if size_ok else 'FAIL'}: 第 {index} 张 {width}x{height} "
            f"（要求 {want_w}x{want_h}）亮度 {luma:.1f} {row['file_path']}"
        )

    ratio = (max(lumas) / min(lumas)) if lumas and min(lumas) > 0 else float("inf")
    luma_ok = ratio <= args.luma_tolerance
    if not luma_ok:
        ok = False
    checks.append(
        f"{'PASS' if luma_ok else 'FAIL'}: 曝光一致性 max/min = {ratio:.3f} "
        f"（允许 ≤ {args.luma_tolerance}）亮度={[round(x, 1) for x in lumas]}"
    )

    report = {
        "schema": "visioncraft.image_series_smoke.v1",
        "plan": plan,
        "project_id": project_id,
        "asset_ids": asset_ids,
        "assets": rows,
        "luma": lumas,
        "luma_ratio": round(ratio, 4) if lumas and min(lumas) > 0 else None,
        "live_image_count": used["live_image_count"] if used else None,
        "verdict": "PASS" if ok else "FAIL",
    }
    report_path = DATA_DIR / "smoke" / f"image-series-{project_id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for line in checks:
        print(line)
    print(json.dumps({
        "project_id": project_id, "asset_ids": asset_ids, "verdict": report["verdict"],
        "report": str(report_path),
        "images": [str(PROJECTS_DIR / project_id / Path(r["file_path"]).name) for r in rows],
    }, ensure_ascii=False))
    return 0 if ok else 1


def _fail(message: str) -> int:
    print(f"FAIL: {message}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
