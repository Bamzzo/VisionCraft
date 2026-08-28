"""No-cost tests for provider capability contracts and shot-level generation constraints."""
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
from backend.providers.capabilities import (
    CapabilityError,
    get_provider_capabilities,
    validate_video_generation,
)
from backend.services.video_service import prepare_shot_video_generation


REQUIRED_VIDEO_FIELDS = {
    "id",
    "label",
    "mode",
    "supported_modes",
    "supported_ratios",
    "supported_durations",
    "supported_resolutions",
    "default_model",
    "models",
}


def expect_error(code: str, **kwargs) -> None:
    try:
        validate_video_generation(**kwargs)
    except CapabilityError as exc:
        assert exc.code == code, f"expected {code}, got {exc.code}: {exc}"
        return
    raise AssertionError(f"expected CapabilityError {code}")


def test_capability_contract() -> None:
    payload = get_provider_capabilities()
    assert "mode_requirements" in payload
    assert payload["mode_requirements"]["i2v"]["requires_first_frame"] is True
    assert payload["mode_requirements"]["keyframes"]["requires_last_frame"] is True
    ids = {item["id"] for item in payload["video"]}
    assert {"ark", "dashscope", "minimax", "siliconflow"} <= ids
    for item in payload["video"]:
        missing = REQUIRED_VIDEO_FIELDS - set(item)
        assert not missing, f"{item['id']} missing {missing}"
        assert item["models"], f"{item['id']} has no models"
        for model in item["models"]:
            assert model["id"]
            assert model["supported_modes"]


def test_validate_frames_and_modes() -> None:
    common = dict(provider="ark", model=None, duration_seconds=5, aspect_ratio="16:9")
    expect_error(
        "MISSING_FIRST_FRAME",
        video_mode="i2v",
        first_frame_path=None,
        last_frame_path=None,
        **common,
    )
    expect_error(
        "MISSING_LAST_FRAME",
        video_mode="keyframes",
        first_frame_path="/assets/demo/first.jpg",
        last_frame_path=None,
        **common,
    )
    expect_error(
        "UNSUPPORTED_MODE_FOR_MODEL",
        provider="siliconflow",
        model=None,
        video_mode="i2v",
        duration_seconds=5,
        aspect_ratio="16:9",
        first_frame_path="/assets/demo/first.jpg",
        last_frame_path=None,
    )
    expect_error(
        "UNSUPPORTED_DURATION",
        provider="minimax",
        model=None,
        video_mode="t2v",
        duration_seconds=5,
        aspect_ratio="16:9",
        first_frame_path=None,
        last_frame_path=None,
    )
    plan = validate_video_generation(
        provider="seedance",
        model=None,
        video_mode="i2v",
        duration_seconds=5,
        aspect_ratio="16:9",
        first_frame_path="/assets/demo/first.jpg",
        last_frame_path=None,
    )
    assert plan["provider"] == "ark"
    assert plan["video_mode"] == "i2v"


def _insert_shot(project_id: str, shot_id: str, version_id: str, first_frame: str | None, video_path: str | None) -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode, status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, "Capability test", "Test source", "test", "16:9", 5, "auto", "testing", "direct", now, now),
        )
        conn.execute(
            """INSERT INTO shots
            (id, project_id, shot_index, title, description, characters, scene, camera_motion,
             visual_prompt, negative_prompt, audio_prompt, status, retry_count, current_version_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (shot_id, project_id, 1, "Shot 1", "desc", "[]", "scene", "static", "prompt", "", "", "keyframes_ready", 0, version_id, now, now),
        )
        conn.execute(
            """INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
             first_frame_path, last_frame_path, video_path, video_mode, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, shot_id, 1, "desc", "prompt", "", "", first_frame, None, video_path, "t2v", "test", now),
        )


def test_prepare_forks_on_provider_switch() -> None:
    project_id = f"cap_test_{uuid.uuid4().hex[:10]}"
    shot_id = f"shot_{uuid.uuid4().hex[:8]}"
    version_id = f"version_{uuid.uuid4().hex[:8]}"
    _insert_shot(project_id, shot_id, version_id, "/assets/demo/first.jpg", "/assets/demo/old.mp4")
    try:
        first = prepare_shot_video_generation(project_id, shot_id, video_mode="i2v", provider="ark", duration_seconds=5)
        second = prepare_shot_video_generation(project_id, shot_id, video_mode="i2v", provider="dashscope", duration_seconds=5)
        assert first["provider"] == "ark"
        assert second["provider"] == "dashscope"
        assert first["version_id"] != version_id
        assert second["version_id"] != first["version_id"]
        with connect() as conn:
            rows = conn.execute(
                "SELECT id, provider, model, video_path FROM shot_versions WHERE shot_id = ? ORDER BY version_number",
                (shot_id,),
            ).fetchall()
            current = conn.execute("SELECT current_version_id FROM shots WHERE id = ?", (shot_id,)).fetchone()
        assert len(rows) == 3
        assert rows[0]["video_path"] == "/assets/demo/old.mp4"
        assert current["current_version_id"] == second["version_id"]
        print("PASS: switching provider creates a new shot version and keeps the old video")
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)


def test_prepare_rejects_i2v_without_frame() -> None:
    project_id = f"cap_test_{uuid.uuid4().hex[:10]}"
    shot_id = f"shot_{uuid.uuid4().hex[:8]}"
    version_id = f"version_{uuid.uuid4().hex[:8]}"
    _insert_shot(project_id, shot_id, version_id, None, None)
    try:
        try:
            prepare_shot_video_generation(project_id, shot_id, video_mode="i2v", provider="ark", duration_seconds=5)
        except CapabilityError as exc:
            assert exc.code == "MISSING_FIRST_FRAME"
            print("PASS: I2V without first frame is rejected before provider submit")
            return
        raise AssertionError("I2V without first frame should fail")
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)


def main() -> None:
    init_environment()
    init_db()
    test_capability_contract()
    test_validate_frames_and_modes()
    test_prepare_forks_on_provider_switch()
    test_prepare_rejects_i2v_without_frame()
    print("PASS: provider capability contract and shot-level constraints")


if __name__ == "__main__":
    main()
