"""Run one paid, traceable image-to-video smoke test for a selected provider.

The script deliberately creates a retained project so the generated asset,
provider task record, and media-transfer provenance can be reviewed later.
It never prints API keys or unredacted Data URLs.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import DATA_DIR, init_environment
from backend.database import connect, init_db, utc_now
from backend.providers.video_provider import VideoAssetRequest, generate_video_asset
from backend.services.asset_service import persist_binary_asset


PROMPT = (
    "A cinematic fantasy character stands in a windswept, misty landscape. "
    "His long black hair and dark robe move naturally in the wind. "
    "A bright green butterfly-like spirit gently flutters on his left, while a small golden insect on his right shoulder emits a soft glow. "
    "Slow camera push-in, stable character identity, ink-wash fantasy illustration style, no text, no watermark, no logo."
)

PROVIDER_SETTINGS = {
    "ark": {"model_env": "VOLC_VIDEO_MODEL", "duration": 5, "resolution": "720p"},
    "dashscope": {"model_env": "DASHSCOPE_I2V_MODEL", "duration": 2, "resolution": "720P"},
    "minimax": {"model_env": "MINIMAX_VIDEO_MODEL", "duration": 4, "resolution": "768P"},
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=PROVIDER_SETTINGS)
    parser.add_argument("--image", type=Path, default=ROOT.parent / "gyfy.jpg")
    args = parser.parse_args()

    init_environment()
    init_db()
    if not args.image.is_file():
        raise SystemExit(f"Test image does not exist: {args.image}")

    source = args.image.read_bytes()
    project_id = f"i2v_{args.provider}_{uuid.uuid4().hex[:8]}"
    shot_id = f"shot_{uuid.uuid4().hex[:10]}"
    version_id = f"ver_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    settings = PROVIDER_SETTINGS[args.provider]

    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode, status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, f"I2V smoke - {args.provider}", "Controlled provider compatibility test", "ink-wash fantasy", "16:9", settings["duration"], "fixed", "testing", "direct", now, now),
        )
    asset_id = persist_binary_asset(
        project_id, "first-frame", "gyfy fixed reference", "Fixed I2V compatibility reference", PROMPT,
        source, args.image.suffix, "local", "gyfy.jpg",
    )
    with connect() as conn:
        frame_path = conn.execute("SELECT file_path FROM assets WHERE id = ?", (asset_id,)).fetchone()["file_path"]
        conn.execute(
            """INSERT INTO shots
            (id, project_id, shot_index, title, description, characters, scene, camera_motion, visual_prompt, negative_prompt, audio_prompt, status, current_version_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (shot_id, project_id, 1, "方源与双蛊", "固定首帧 I2V 兼容性验证", "方源", "风沙荒原", "slow push-in", PROMPT, "text, watermark, logo, identity drift", "", "keyframes_ready", version_id, now, now),
        )
        conn.execute(
            """INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt, first_frame_path, last_frame_path, video_mode, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, shot_id, 1, "固定首帧 I2V 兼容性验证", PROMPT, "text, watermark, logo, identity drift", "", frame_path, None, "i2v", "smoke-test", now),
        )

    result = generate_video_asset(
        VideoAssetRequest(
            project_id=project_id, shot_id=shot_id, version_id=version_id, title="方源与双蛊",
            description="固定首帧 I2V 兼容性验证", prompt=PROMPT, first_frame_path=frame_path,
            duration_seconds=settings["duration"], aspect_ratio="16:9", video_mode="i2v", provider_override=args.provider,
        )
    )
    with connect() as conn:
        task = conn.execute("SELECT * FROM video_tasks WHERE project_id = ? ORDER BY created_at DESC LIMIT 1", (project_id,)).fetchone()
        transfers = conn.execute("SELECT target_provider, target_model, transfer_mode, role, request_reference, metadata_json FROM media_transfers WHERE asset_id = ?", (asset_id,)).fetchall()
    report = {
        "provider": args.provider,
        "model": result.model,
        "project_id": project_id,
        "shot_id": shot_id,
        "first_frame_asset_id": asset_id,
        "input_file": args.image.name,
        "input_bytes": len(source),
        "settings": {"duration_seconds": settings["duration"], "resolution": settings["resolution"], "aspect_ratio": "16:9"},
        "result": result.__dict__,
        "video_task": dict(task) if task else None,
        "media_transfers": [dict(row) for row in transfers],
    }
    report_path = DATA_DIR / "smoke" / f"i2v-{args.provider}-{project_id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"provider": args.provider, "status": result.status, "model": result.model, "project_id": project_id, "video_path": result.video_path, "report": str(report_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
