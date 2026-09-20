"""无费用验收：P6 三条固定演示样本。

检查 ``tools/prepare_p6_demo_samples.py`` 真的产出路线图 P6 要求的三个样本，
而且是用真实素材产出的、没有偷偷外发过一次请求。

关于源资产
----------
样本的镜头视频来自 2026-08-28 / 08-31 那批真实付费产物，它们住在**工作库**
(``backend/data/projects``) 里。本检查跑在隔离数据目录中，所以源路径故意
**不**跟随 ``VISIONCRAFT_DATA_DIR``：只读地引用工作库，把样本建在自己的
隔离目录里。源资产不在本机时整项 SKIP，并说明原因——那是外部前提，不是
可以在隔离目录里种出来的夹具。

无 FFmpeg 时 SKIP：合成与探测都依赖它，跳过不得记为通过。

用法::

    .venv\\Scripts\\python.exe tools\\test_p6_demo_samples.py
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from backend.config import DB_PATH, PROJECTS_DIR, init_environment  # noqa: E402
from backend.database import init_db  # noqa: E402
from tools.p6c_ffmpeg import ffmpeg_available, probe_media, probe_video  # noqa: E402

# 真实源资产所在地：工作库，绝不跟随 VISIONCRAFT_DATA_DIR。
SOURCE_PROJECTS_DIR = Path(
    os.environ.get("P6DEMO_SOURCE_PROJECTS_DIR") or (REPO / "backend" / "data" / "projects")
)

STORY_ID = "p6demo_story"
COMPARE_ID = "p6demo_compare"
RECOVERY_ID = "p6demo_recovery"
DEMO_IDS = (STORY_ID, COMPARE_ID, RECOVERY_ID)

PREPARE = str(REPO / "tools" / "prepare_p6_demo_samples.py")

REQUIRED_SOURCES = (
    "i2v_ark_b97ea96a/asset_0fab834ac2.mp4",
    "i2v_dashscope_612ff237/asset_89afa38ec3.mp4",
    "i2v_minimax_f12c30c7/asset_edb963f99f.mp4",
    "i2v_ark_b97ea96a/asset_918aceaf9c.jpg",
    "project_5fdac03f50/asset_7dd1def30b.mp4",
    "project_5fdac03f50/asset_6b0c7f32b4.mp4",
    "project_5fdac03f50/asset_224744e8f0.mp4",
    "project_5fdac03f50/asset_2dbbea16da.mp4",
)

passed = failed = skipped = 0
failures: list[str] = []


def ok(message: str) -> None:
    global passed
    passed += 1
    print(f"PASS: {message}")


def bad(message: str) -> None:
    global failed
    failed += 1
    failures.append(message)
    print(f"FAIL: {message}")


def skip(message: str) -> None:
    global skipped
    skipped += 1
    print(f"SKIP: {message}")


def check(condition: bool, message: str) -> bool:
    if condition:
        ok(message)
    else:
        bad(message)
    return bool(condition)


def run_prepare(*extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, PREPARE, "--source-projects-dir", str(SOURCE_PROJECTS_DIR), *extra],
        cwd=str(REPO), capture_output=True, text=True, env=env, timeout=300, errors="replace",
    )


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def local_of(public_path: str) -> Path:
    """/assets/<project>/<file> -> 当前数据目录下的真实文件。"""
    return PROJECTS_DIR / Path(public_path).parent.name / Path(public_path).name


def project_ids() -> set[str]:
    with connect() as conn:
        return {row["id"] for row in conn.execute("SELECT id FROM projects")}


def main() -> int:
    init_environment()
    init_db()

    if not ffmpeg_available():
        skip("本机没有 FFmpeg，无法验证样本的合成与可解码性")
        print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
        return 0

    missing = [rel for rel in REQUIRED_SOURCES if not (SOURCE_PROJECTS_DIR / rel).is_file()]
    if missing:
        skip(f"真实源资产不在本机（{len(missing)} 项缺失，例如 {missing[0]}），无法验证演示样本")
        print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
        return 0

    sentinel_before = project_ids()
    print(f"INFO: 源资产目录 {SOURCE_PROJECTS_DIR}")
    print(f"INFO: 隔离数据目录 {PROJECTS_DIR.parent}")
    print()

    # ---- 1. 准备三个样本 -------------------------------------------------
    first = run_prepare()
    if first.returncode != 0:
        bad(f"准备脚本退出码 {first.returncode}")
        print(first.stdout[-1500:])
        print(first.stderr[-800:])
        print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
        return 1
    ok("准备脚本退出码 0")

    with connect() as conn:
        rows = {row["id"]: dict(row) for row in conn.execute(
            "SELECT * FROM projects WHERE id LIKE 'p6demo_%'")}
    check(set(rows) == set(DEMO_IDS),
          f"三个样本项目齐备（实际 {sorted(rows)}）")

    if set(rows) != set(DEMO_IDS):
        print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
        return 1

    # ---- 2. 零外发：全部 mock，且真实调用计数为 0 -------------------------
    for pid in DEMO_IDS:
        row = rows[pid]
        check(row["generation_mode"] == "mock",
              f"{pid} 生成模式为 mock（实际 {row['generation_mode']}）")
        counts = (row["live_text_call_count"], row["live_vision_call_count"],
                  row["live_video_call_count"])
        check(counts == (0, 0, 0), f"{pid} 真实调用计数全 0（实际 {counts}）")

    # ---- 3. 样本 A：短文本完整闭环 ---------------------------------------
    with connect() as conn:
        story_shots = [dict(r) for r in conn.execute(
            "SELECT * FROM shots WHERE project_id = ? ORDER BY shot_index", (STORY_ID,))]
        story_final = [dict(r) for r in conn.execute(
            "SELECT * FROM assets WHERE project_id = ? AND type = 'final-video'"
            " ORDER BY created_at DESC", (STORY_ID,))]
        story_versions = {r["shot_id"]: dict(r) for r in conn.execute(
            "SELECT v.* FROM shot_versions v JOIN shots s ON s.id = v.shot_id"
            " WHERE s.project_id = ?", (STORY_ID,))}

    check(len(story_shots) == 4, f"样本 A 有 4 个镜头（实际 {len(story_shots)}）")
    check(all(s["status"] == "video_ready" for s in story_shots),
          "样本 A 全部镜头为 video_ready")
    clip_ok = True
    res_set = set()
    for shot in story_shots:
        version = story_versions.get(shot["id"]) or {}
        path = version.get("video_path")
        if not path:
            clip_ok = False
            continue
        local = local_of(path)
        if not local.is_file():
            clip_ok = False
            continue
        info = probe_video(local)
        res_set.add((info.get("width"), info.get("height")))
        if info.get("codec") != "h264":
            clip_ok = False
    check(clip_ok, "样本 A 每个镜头的视频都存在且可解码为 H.264")
    check(res_set == {(1280, 720)}, f"样本 A 镜头规格统一为 1280x720（实际 {sorted(res_set)}）")

    check(bool(story_final), "样本 A 有成片资产（final-video）")
    if story_final:
        local = local_of(story_final[0]["file_path"])
        check(local.is_file(), f"样本 A 成片文件存在（{local.name}）")
        if local.is_file():
            info = probe_media(local)
            check(info.get("width") == 1280 and info.get("height") == 720,
                  f"样本 A 成片为 1280x720（实际 {info.get('width')}x{info.get('height')}）")
            check(bool(info.get("audio_codec")),
                  f"样本 A 成片带音轨（实际 {info.get('audio_codec') or '无'}）")
            check(18.0 <= (info.get("duration") or 0) <= 22.0,
                  f"样本 A 成片时长约 20 秒（实际 {info.get('duration'):.2f}s）")

    # ---- 4. 样本 B：同镜头多模型对比 -------------------------------------
    with connect() as conn:
        compare_shots = [dict(r) for r in conn.execute(
            "SELECT * FROM shots WHERE project_id = ? ORDER BY shot_index", (COMPARE_ID,))]
        compare_versions = [dict(r) for r in conn.execute(
            "SELECT v.* FROM shot_versions v JOIN shots s ON s.id = v.shot_id"
            " WHERE s.project_id = ? ORDER BY v.version_number", (COMPARE_ID,))]

    check(len(compare_shots) == 1, f"样本 B 收敛为单个镜头（实际 {len(compare_shots)}）")
    check(len(compare_versions) == 3, f"样本 B 有 3 个版本（实际 {len(compare_versions)}）")
    providers = sorted(v.get("provider") or "" for v in compare_versions)
    check(providers == ["ark", "dashscope", "minimax"],
          f"样本 B 三家 Provider 齐备（实际 {providers}）")

    compare_ok = True
    compare_shapes = {}
    frames = set()
    for version in compare_versions:
        path = version.get("video_path")
        if not path or not local_of(path).is_file():
            compare_ok = False
            continue
        info = probe_video(local_of(path))
        compare_shapes[version["provider"]] = (info.get("width"), info.get("height"),
                                               round(info.get("duration") or 0, 2))
        if version.get("first_frame_path"):
            frames.add(version["first_frame_path"])
    check(compare_ok, "样本 B 三个版本的视频都存在且可解码")
    check(len(set(compare_shapes.values())) == 3,
          f"样本 B 三家输出规格各不相同（实际 {compare_shapes}）")
    check(len(frames) == 1, f"样本 B 三家共用同一张首帧（实际 {len(frames)} 张不同）")
    if compare_shots:
        check(compare_shots[0]["current_version_id"] in {v["id"] for v in compare_versions},
              "样本 B 当前版本指向已挂载的版本")

    # ---- 4b. 对比图工具（讲稿引用的演示素材） -----------------------------
    sheet_out = PROJECTS_DIR.parent / "p6demo-compare-sheet.png"
    sheet_env = dict(os.environ)
    sheet_env["PYTHONIOENCODING"] = "utf-8"
    sheet = subprocess.run(
        [sys.executable, str(REPO / "tools" / "make_p6_compare_sheet.py"),
         "--source-projects-dir", str(SOURCE_PROJECTS_DIR), "--out", str(sheet_out)],
        cwd=str(REPO), capture_output=True, text=True, env=sheet_env, timeout=120,
        errors="replace",
    )
    check(sheet.returncode == 0, f"对比图工具退出码 0（实际 {sheet.returncode}）")
    if sheet.returncode != 0:
        print(sheet.stderr[-500:])
    check(sheet_out.is_file() and sheet_out.stat().st_size > 50_000,
          "对比图已生成且非占位（"
          f"{(sheet_out.stat().st_size // 1024) if sheet_out.is_file() else 0} KB）")

    # ---- 5. 样本 C：失败恢复案例 -----------------------------------------
    with connect() as conn:
        recovery_shots = [dict(r) for r in conn.execute(
            "SELECT * FROM shots WHERE project_id = ?", (RECOVERY_ID,))]
        recovery_tasks = [dict(r) for r in conn.execute(
            "SELECT * FROM video_tasks WHERE project_id = ?", (RECOVERY_ID,))]

    check(len(recovery_shots) == 1, f"样本 C 有 1 个镜头（实际 {len(recovery_shots)}）")
    if recovery_shots:
        check(recovery_shots[0]["status"] == "video_failed",
              f"样本 C 镜头处于 video_failed（实际 {recovery_shots[0]['status']}）")
    check(len(recovery_tasks) == 1, f"样本 C 有 1 条失败任务记录（实际 {len(recovery_tasks)}）")
    if recovery_tasks:
        task = recovery_tasks[0]
        check(task["status"] == "failed" and task["cloud_status"] == "invalid_task_id",
              f"样本 C 任务云端状态为 invalid_task_id（实际 {task['cloud_status']}）")
        check(task["error_code"] == "TASK_ID_INVALID",
              f"样本 C 错误码为 TASK_ID_INVALID（实际 {task['error_code']}）")
        check("invalid task_id" in (task["error_message"] or ""),
              "样本 C 错误原文保留平台返回的 invalid task_id 字样")
        check(not task["result_path"], "样本 C 失败任务没有落地产物（确实没成功过）")

    # ---- 6. 幂等：重复准备不新增项目、不新增版本 -------------------------
    counts_before = {}
    with connect() as conn:
        for pid in DEMO_IDS:
            counts_before[pid] = (
                conn.execute("SELECT COUNT(*) FROM shots WHERE project_id = ?", (pid,)).fetchone()[0],
                conn.execute(
                    "SELECT COUNT(*) FROM shot_versions v JOIN shots s ON s.id = v.shot_id"
                    " WHERE s.project_id = ?", (pid,)).fetchone()[0],
            )
    second = run_prepare("--only", "story,compare,recovery")
    check(second.returncode == 0, f"重复准备退出码 0（实际 {second.returncode}）")
    check(project_ids() == sentinel_before | set(DEMO_IDS),
          "重复准备没有堆出额外项目")
    with connect() as conn:
        for pid in DEMO_IDS:
            after = (
                conn.execute("SELECT COUNT(*) FROM shots WHERE project_id = ?", (pid,)).fetchone()[0],
                conn.execute(
                    "SELECT COUNT(*) FROM shot_versions v JOIN shots s ON s.id = v.shot_id"
                    " WHERE s.project_id = ?", (pid,)).fetchone()[0],
            )
            check(after == counts_before[pid],
                  f"{pid} 重复准备后镜头/版本数不变（{counts_before[pid]} -> {after}）")

    # ---- 7. 清理只删自己的项目 -------------------------------------------
    clean = run_prepare("--clean")
    check(clean.returncode == 0, f"--clean 退出码 0（实际 {clean.returncode}）")
    remaining = project_ids()
    check(not any(pid.startswith("p6demo_") for pid in remaining),
          "清理后没有 p6demo_* 残留")
    check(remaining == sentinel_before, "清理没有触碰任何非 p6demo_ 项目")

    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    if failures:
        print("\n失败明细：")
        for item in failures:
            print(f"  - {item}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
