"""No-cost integration test for the provider-neutral image handoff layer."""
from __future__ import annotations

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
from backend.services.asset_service import persist_binary_asset
from backend.services.media_transfer_service import prepare_image_reference

# Valid 1x1 PNG.  The test checks the transport contract, not image aesthetics.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)


def main() -> None:
    init_environment()
    init_db()
    project_id = f"media_test_{uuid.uuid4().hex[:10]}"
    with connect() as conn:
        now = utc_now()
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode, status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, "Media handoff test", "Test source", "test", "16:9", 5, "auto", "testing", "direct", now, now),
        )
    try:
        asset_id = persist_binary_asset(
            project_id, "first-frame", "Handoff test frame", "One pixel validation frame", "test prompt",
            PNG_BYTES, ".png", "test", "local-fixture",
        )
        with connect() as conn:
            path = conn.execute("SELECT file_path FROM assets WHERE id = ?", (asset_id,)).fetchone()["file_path"]
        reference = prepare_image_reference(
            project_id, path, target_provider="ark", target_model="seedance-test", role="first_frame",
        )
        assert reference is not None
        assert reference.transfer_mode == "data_url"
        assert reference.url.startswith("data:image/png;base64,")
        with connect() as conn:
            transfer = conn.execute(
                "SELECT transfer_mode, request_reference, metadata_json FROM media_transfers WHERE asset_id = ?", (asset_id,)
            ).fetchone()
        assert transfer["transfer_mode"] == "data_url"
        assert transfer["request_reference"] == "<data-url-omitted>"
        print("PASS: local image asset -> validated Data URL -> redacted transfer record")
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)


if __name__ == "__main__":
    main()
