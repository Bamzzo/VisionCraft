"""Run one paid reference-image acceptance test.

The zero-cost suite pins the payload contract: which providers accept a
reference image, in what order the images go out, and how Ark's mutual
exclusion with the first frame is handled. None of that proves the thing the
gate promises the user -- that the reference image actually holds the subject
together *on screen*. Only a real call can answer that, so this script makes
exactly one and then gets out of the way.

The project it creates is retained on purpose: the generated asset, the
provider task record and the media-transfer provenance are the evidence. It
never prints API keys or unredacted Data URLs.

    # price it, send nothing
    .venv/Scripts/python.exe tools/run_reference_smoke.py ark

    # actually spend -- one call, and the gate has to be open in this process
    VISIONCRAFT_ALLOW_LIVE_VIDEO=1 VISIONCRAFT_LIVE_BUDGET_CNY=20 \
        .venv/Scripts/python.exe tools/run_reference_smoke.py ark --live
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
from backend.database import connect, init_db, utc_now  # noqa: E402
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

# The subject described here has to match the reference image, otherwise the
# acceptance cannot tell "the reference held" from "the prompt happened to win".
PROMPT = (
    "A cinematic fantasy character stands in a windswept, misty landscape. "
    "His long black hair and dark robe move naturally in the wind. "
    "A bright green butterfly-like spirit gently flutters on his left, while a small golden insect on his right shoulder emits a soft glow. "
    "Slow camera push-in, stable character identity, ink-wash fantasy illustration style, no text, no watermark, no logo."
)
NEGATIVE = "text, watermark, logo, identity drift"
CHARACTER_NAME = "方源"


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
            "SELECT id, type, name, file_path, duration_seconds FROM assets WHERE project_id = ?", (project_id,)
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
    out_dir = DATA_DIR / "reference-smoke"
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
    parser.add_argument("provider", nargs="?", choices=("ark", "dashscope"))
    parser.add_argument("--refresh", metavar="VIDEO_TASK_ID", help="回查已存在的云端任务，不提交、不产生新费用")
    parser.add_argument("--image", type=Path, default=ROOT.parent / "gyfy.jpg")
    parser.add_argument("--duration", type=int, default=5)
    parser.add_argument("--live", action="store_true", help="不加这个旗标就只报价，不发请求")
    args = parser.parse_args()

    init_environment()
    init_db()
    if args.refresh:
        refresh_only(args.refresh)
        return
    if not args.provider:
        raise SystemExit("需要 provider 参数（ark / dashscope），或用 --refresh <video_task_id> 回查已有任务")
    if not args.image.is_file():
        raise SystemExit(f"Acceptance image does not exist: {args.image}")
    source = args.image.read_bytes()

    # --- 1) 先校验参数并报价（零费用：只读能力表与价目表） ---
    plan = validate_video_generation(
        provider=args.provider,
        model=None,
        video_mode="reference",
        duration_seconds=args.duration,
        aspect_ratio="16:9",
        first_frame_path=None,
        last_frame_path=None,
        reference_paths=["/assets/placeholder/ref.png"],
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
        "aspect_ratio": "16:9",
        "reference_includes_first_frame": plan["reference_includes_first_frame"],
        "reference_image": {"file": args.image.name, "bytes": len(source), "sha1_16": _sha1(source)},
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

    # --- 2) 建工程：参考图挂在角色锚点上，走的是切片 3 的真实通路 ---
    project_id = f"refsmoke_{args.provider}_{uuid.uuid4().hex[:8]}"
    shot_id = f"shot_{uuid.uuid4().hex[:10]}"
    version_id = f"ver_{uuid.uuid4().hex[:10]}"
    char_id = f"char_{uuid.uuid4().hex[:8]}"
    now = utc_now()

    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode, status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                f"Reference acceptance - {args.provider}",
                "Reference-image acceptance run (slice 3 verification)",
                "ink-wash fantasy",
                "16:9",
                plan["duration_seconds"],
                "fixed",
                "testing",
                "direct",
                now,
                now,
            ),
        )

    anchor_asset = persist_binary_asset(
        project_id,
        "character-anchor",
        f"{CHARACTER_NAME} 参考图",
        "参考图一致性验收的固定输入",
        PROMPT,
        source,
        args.image.suffix,
        "local",
        args.image.name,
    )
    with connect() as conn:
        anchor_path = conn.execute(
            "SELECT file_path FROM assets WHERE id = ?", (anchor_asset,)
        ).fetchone()["file_path"]
        conn.execute(
            "INSERT INTO characters (id, project_id, name, role, description, visual_prompt, asset_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (char_id, project_id, CHARACTER_NAME, "protagonist", "验收主体", PROMPT, anchor_asset, now),
        )

    # Ark 的参考图与首帧互斥，所以参考图模式下它不拿首帧；DashScope 的 r2v
    # 允许两者并存。这个差异不靠 if provider 硬编码，而是读刚才那条 plan。
    first_frame_path = anchor_path if plan["reference_includes_first_frame"] else None

    with connect() as conn:
        conn.execute(
            """INSERT INTO shots
            (id, project_id, shot_index, title, description, characters, scene, camera_motion, visual_prompt, negative_prompt, audio_prompt, status, current_version_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                shot_id, project_id, 1, "方源立身风沙", "参考图一致性验收镜头",
                CHARACTER_NAME, "风沙荒原", "slow push-in", PROMPT, NEGATIVE, "",
                "keyframes_ready", version_id, now, now,
            ),
        )
        conn.execute(
            """INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt, first_frame_path, last_frame_path, video_mode, provider, model, duration_seconds, reference_frame_path, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                version_id, shot_id, 1, "参考图一致性验收镜头", PROMPT, NEGATIVE, "",
                first_frame_path, None, "reference", args.provider, plan["model"],
                plan["duration_seconds"], None, "reference-acceptance", now,
            ),
        )

    # --- 3) 提交（真实付费，走 generate_video_asset 的分发环闸门） ---
    job_id = create_job(project_id, "shot_video", "参考图验收提交", shot_id=shot_id, stage="queued")
    error = None
    try:
        generate_shot_video(
            project_id,
            shot_id,
            job_id,
            video_mode="reference",
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
            "SELECT target_provider, target_model, transfer_mode, role, request_reference FROM media_transfers WHERE asset_id = ?",
            (anchor_asset,),
        ).fetchall()]
        assets = [dict(r) for r in conn.execute(
            "SELECT id, type, name, file_path, duration_seconds FROM assets WHERE project_id = ?", (project_id,)
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
        "anchor_asset_id": anchor_asset,
        "anchor_file_path": anchor_path,
        "reference_images_sent": "见 media_transfers（顺序即语义）",
        "error": error,
        "jobs": jobs,
        "video_tasks": tasks,
        "media_transfers": transfers,
        "assets": assets,
        "shot_version": version_row,
    }
    out_dir = DATA_DIR / "reference-smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"reference-{args.provider}-{project_id}.json"
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
