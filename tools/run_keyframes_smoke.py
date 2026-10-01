"""Run one paid first/last-frame acceptance test.

Why this exists: `keyframes` is implemented in the provider layer -- all three
providers assemble a last frame, and the capability table marks the mode as
requiring both frames -- but every paid runner pins a different mode
(`run_i2v_smoke.py` sends i2v, `run_reference_smoke.py` sends reference). So the
first/last-frame path had never actually been submitted. The zero-cost suite
pins the contract; only a real call can say whether the clip converges to the
frame it was given.

The two input frames are chosen so the answer is visible. The first is the
project's anchor image: a figure facing the camera in a bright, windblown
landscape. The last is a frame pulled from a demo-project shot: the same story,
a figure walking away into a dark ink wash, cropped to the first frame's ratio.
If a provider ignored the last frame, the tail of the clip would still look
like the first frame -- so this acceptance cannot pass by accident.

The project it creates is retained on purpose: the generated asset, the provider
task record and the media-transfer provenance are the evidence. It never prints
API keys or unredacted Data URLs.

    # price it, send nothing
    .venv/Scripts/python.exe tools/run_keyframes_smoke.py ark

    # actually spend -- one call, and the gate has to be open in this process
    VISIONCRAFT_ALLOW_LIVE_VIDEO=1 VISIONCRAFT_LIVE_BUDGET_CNY=20 \
        .venv/Scripts/python.exe tools/run_keyframes_smoke.py ark --live
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import DATA_DIR, init_environment  # noqa: E402
from backend.database import connect, init_db, to_json, utc_now  # noqa: E402
from backend.providers.capabilities import validate_video_generation  # noqa: E402
from backend.providers.video_provider import refresh_remote_video_task  # noqa: E402
from backend.providers.live_budget import (  # noqa: E402
    estimate_closed_loop_cny,
    live_budget_cny,
    live_max_video_calls,
    live_video_authorized,
    video_price_per_second,
)
from backend.services.asset_service import persist_binary_asset  # noqa: E402
from backend.services.job_service import create_job, redact_value  # noqa: E402
from backend.services.video_service import generate_shot_video  # noqa: E402

# Describes the transition the two frames imply, not just the subject: the
# character must be readable in the first frame and clearly turned away by the
# last one, otherwise neither "converged" nor "ignored" can be told apart.
PROMPT = (
    "A cinematic fantasy character turns away from the camera and walks off into the dark. "
    "His long black hair and dark robe drift in the wind. "
    "The bright green butterfly-like spirit and the small golden insect fade behind him. "
    "Continuous camera move, stable character identity, ink-wash fantasy illustration style, "
    "no text, no watermark, no logo."
)
NEGATIVE = "text, watermark, logo, identity drift, frozen frames"

# Duration has to land on a supported tier or the request cannot be built at
# all -- MiniMax has 4/6/10/15, so a flat 5 s default would price a combination
# that could never be submitted.
DEFAULT_DURATIONS = {"ark": 5, "dashscope": 5, "minimax": 4}
DEFAULT_RATIO = "16:9"


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:16]


def refresh_only(task_id: str) -> None:
    """回查一个已经在云端的任务。

    这条路**不提交、不产生新费用**：云端已经有 remote_task_id，回查只是把它取回来。
    项目的既有约定写得很清楚——超时后刷新同一个任务，绝不允许重新提交替代刷新。
    重新提交会花第二次钱，而且会得到第二段视频。
    """
    with connect() as conn:
        row = conn.execute("SELECT * FROM video_tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise SystemExit(f"找不到本地视频任务：{task_id}")
    row = dict(row)
    print(
        f"回查 {task_id}（provider={row['provider']} model={row['model']} "
        f"remote={row.get('remote_task_id')} status={row['status']}）—— 不提交，不产生新费用。"
    )
    error = None
    try:
        video_path = refresh_remote_video_task(task_id)
    except Exception as exc:  # noqa: BLE001
        video_path, error = None, f"{type(exc).__name__}: {exc}"

    project_id = row["project_id"]
    with connect() as conn:
        task = dict(conn.execute("SELECT * FROM video_tasks WHERE id = ?", (task_id,)).fetchone())
        assets = [dict(r) for r in conn.execute(
            "SELECT id, type, name, file_path, duration_seconds FROM assets WHERE project_id = ?",
            (project_id,),
        ).fetchall()]
        transfers = [dict(r) for r in conn.execute(
            "SELECT mt.target_provider, mt.target_model, mt.transfer_mode, mt.role, mt.request_reference "
            "FROM media_transfers mt JOIN assets a ON a.id = mt.asset_id WHERE a.project_id = ?",
            (project_id,),
        ).fetchall()]

    report = {
        "mode": "refresh_only",
        "video_task_id": task_id,
        "project_id": project_id,
        "error": error,
        "video_tasks": [task],
        "assets": assets,
        "media_transfers": transfers,
    }
    out_dir = DATA_DIR / "keyframes-smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"refresh-{task_id}.json"
    report_path.write_text(json.dumps(redact_value(report), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(
        {
            "status": task["status"],
            "model": task["model"],
            "result_path": task.get("result_path"),
            "video_path": getattr(video_path, "video_path", None)
            or (video_path if isinstance(video_path, str) else None),
            "error": error,
            "report": str(report_path),
        },
        ensure_ascii=False,
    ))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", nargs="?", choices=("ark", "dashscope", "minimax"))
    parser.add_argument("--refresh", metavar="VIDEO_TASK_ID", help="回查已存在的云端任务，不提交、不产生新费用")
    parser.add_argument("--first", type=Path, default=ROOT.parent / "gyfy.jpg")
    parser.add_argument(
        "--last",
        type=Path,
        default=ROOT / "output" / "keyframes-smoke" / "end-frame.jpg",
    )
    parser.add_argument("--duration", type=int, default=None, help="不传则按 provider 取合法档位")
    parser.add_argument("--ratio", default=DEFAULT_RATIO)
    parser.add_argument("--live", action="store_true", help="不加这个旗标就只报价，不发请求")
    args = parser.parse_args()

    init_environment()
    init_db()
    if args.refresh:
        refresh_only(args.refresh)
        return
    if not args.provider:
        raise SystemExit(
            "需要 provider 参数（ark / dashscope / minimax），或用 --refresh <video_task_id> 回查已有任务"
        )
    for label, p in (("first", args.first), ("last", args.last)):
        if not p.is_file():
            raise SystemExit(f"Acceptance frame does not exist ({label}): {p}")
    if args.first.resolve() == args.last.resolve():
        raise SystemExit("首尾帧是同一个文件——这样测不出「是否收束」，请换一张尾帧。")

    first_bytes = args.first.read_bytes()
    last_bytes = args.last.read_bytes()
    duration = args.duration if args.duration is not None else DEFAULT_DURATIONS.get(args.provider, 5)

    # --- 1) 先校验参数并报价（零费用：只读能力表与价目表） ---
    plan = validate_video_generation(
        provider=args.provider,
        model=None,
        video_mode="keyframes",
        duration_seconds=duration,
        aspect_ratio=args.ratio,
        first_frame_path=str(args.first),
        last_frame_path=str(args.last),
        reference_paths=None,
    )
    resolution = plan.get("resolution") or "720p"
    quote = estimate_closed_loop_cny(
        video_seconds=plan["duration_seconds"], provider=args.provider, resolution=resolution
    )
    unit, unit_source = video_price_per_second(
        args.provider, resolution=resolution, has_input_video=True
    )
    manifest = {
        "provider": args.provider,
        "model": plan["model"],
        "video_mode": plan["video_mode"],
        "duration_seconds": plan["duration_seconds"],
        "resolution": resolution,
        "aspect_ratio": args.ratio,
        "first_frame": {"file": args.first.name, "bytes": len(first_bytes), "sha1_16": _sha1(first_bytes)},
        "last_frame": {"file": args.last.name, "bytes": len(last_bytes), "sha1_16": _sha1(last_bytes)},
        "unit_cny_per_second": unit,
        "unit_basis": unit_source,
        "estimated_video_cny": quote["video_cny"],
        "estimated_closed_loop_cny": quote["total_cny"],
        "gate": {
            "authorized": live_video_authorized(),
            "max_video_calls": live_max_video_calls(),
            "budget_cny": live_budget_cny(),
        },
    }
    print("=== 清单（这一条会被写进报告）===")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))

    if not args.live:
        print("\nDRY RUN：未发送任何请求，费用 0 元。加 --live 才会真正提交。")
        return

    if not live_video_authorized():
        raise SystemExit(
            "闸门关闭：请在本进程设置 VISIONCRAFT_ALLOW_LIVE_VIDEO=1 后再跑。"
            "仅配置密钥不会发起真实请求。"
        )

    # --- 2) 建工程：两张帧都按真实通路挂进 shot_version ---
    project_id = f"kfsmoke_{args.provider}_{uuid.uuid4().hex[:8]}"
    shot_id = f"shot_{uuid.uuid4().hex[:10]}"
    version_id = f"ver_{uuid.uuid4().hex[:10]}"
    now = utc_now()

    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode, status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                f"First/last-frame acceptance - {args.provider}",
                "First/last-frame acceptance run (keyframes verification)",
                "ink-wash fantasy",
                args.ratio,
                plan["duration_seconds"],
                "fixed",
                "testing",
                "direct",
                now,
                now,
            ),
        )

    first_asset = persist_binary_asset(
        project_id,
        "first-frame",
        "首帧（方源立身风沙）",
        "首尾帧验收的固定首帧",
        PROMPT,
        first_bytes,
        args.first.suffix,
        "local",
        args.first.name,
    )
    last_asset = persist_binary_asset(
        project_id,
        "last-frame",
        "尾帧（转身走入暗处）",
        "首尾帧验收的固定尾帧",
        PROMPT,
        last_bytes,
        args.last.suffix,
        "local",
        args.last.name,
    )
    with connect() as conn:
        first_path = conn.execute(
            "SELECT file_path FROM assets WHERE id = ?", (first_asset,)
        ).fetchone()["file_path"]
        last_path = conn.execute(
            "SELECT file_path FROM assets WHERE id = ?", (last_asset,)
        ).fetchone()["file_path"]

    with connect() as conn:
        conn.execute(
            """INSERT INTO shots
            (id, project_id, shot_index, title, description, characters, scene, camera_motion, visual_prompt, negative_prompt, audio_prompt, status, current_version_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                shot_id, project_id, 1, "方源转身入暗", "首尾帧一致性验收镜头",
                to_json(["方源"]), "风沙荒原 → 水墨暗处", "continuous move", PROMPT, NEGATIVE, "",
                "keyframes_ready", version_id, now, now,
            ),
        )
        conn.execute(
            """INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt, first_frame_path, last_frame_path, video_mode, provider, model, duration_seconds, reference_frame_path, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                version_id, shot_id, 1, "首尾帧一致性验收镜头", PROMPT, NEGATIVE, "",
                first_path, last_path, "keyframes", args.provider, plan["model"],
                plan["duration_seconds"], None, "keyframes-acceptance", now,
            ),
        )

    # --- 3) 提交（真实付费，走 generate_video_asset 的分发环闸门） ---
    job_id = create_job(project_id, "shot_video", "首尾帧验收提交", shot_id=shot_id, stage="queued")
    error = None
    try:
        generate_shot_video(
            project_id,
            shot_id,
            job_id,
            video_mode="keyframes",
            provider=args.provider,
            duration_seconds=plan["duration_seconds"],
            version_id=version_id,
        )
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"

    # --- 4) 取证 ---
    with connect() as conn:
        tasks = [dict(r) for r in conn.execute(
            "SELECT * FROM video_tasks WHERE project_id = ? ORDER BY created_at DESC", (project_id,)
        ).fetchall()]
        transfers = [dict(r) for r in conn.execute(
            "SELECT target_provider, target_model, transfer_mode, role, request_reference "
            "FROM media_transfers WHERE asset_id IN (?, ?)",
            (first_asset, last_asset),
        ).fetchall()]
        assets = [dict(r) for r in conn.execute(
            "SELECT id, type, name, file_path, duration_seconds FROM assets WHERE project_id = ?",
            (project_id,),
        ).fetchall()]
        version_row = dict(conn.execute(
            "SELECT * FROM shot_versions WHERE id = ?", (version_id,)
        ).fetchone())
        # generate_shot_video 不往外抛异常：失败会写进 job。不读这一条，
        # 一次 403（比如账号欠费）就会显示成"没任务、没错误"，看着像什么都没发生。
        jobs = [dict(r) for r in conn.execute(
            "SELECT id, status, message, error_message, stage FROM jobs WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()]

    report = {
        "manifest": manifest,
        "project_id": project_id,
        "shot_id": shot_id,
        "version_id": version_id,
        "first_asset_id": first_asset,
        "last_asset_id": last_asset,
        "first_frame_path": first_path,
        "last_frame_path": last_path,
        "error": error,
        "jobs": jobs,
        "video_tasks": tasks,
        "media_transfers": transfers,
        "assets": assets,
        "shot_version": version_row,
    }
    out_dir = DATA_DIR / "keyframes-smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"keyframes-{args.provider}-{project_id}.json"
    report_path.write_text(
        json.dumps(redact_value(report), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    video_path = (tasks[0].get("result_path") if tasks else None) or next(
        (a["file_path"] for a in assets if "video" in (a.get("type") or "")), None
    )
    print("\n=== 结果 ===")
    print(json.dumps(
        {
            "provider": args.provider,
            "status": (tasks[0]["status"] if tasks else "no-task"),
            "model": (tasks[0]["model"] if tasks else plan["model"]),
            "remote_task_id": (tasks[0]["remote_task_id"] if tasks else None),
            "video_path": video_path,
            "error": error,
            "job_error": next((j.get("error_message") for j in jobs if j.get("error_message")), None),
            "report": str(report_path),
        },
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
