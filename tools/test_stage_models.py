"""No-cost tests for P7-A stage model selection and LLM/vision adapters.

These tests never open a network socket. Live HTTP is blocked unless a local
transport is injected; urllib.request.urlopen is also guarded.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db, utc_now
from backend.providers.capabilities import get_provider_capabilities, validate_video_generation
from backend.providers.llm_adapter import (
    FunctionTransport,
    JsonParseError,
    LiveCallNotAuthorized,
    adaptation_messages,
    build_text_request,
    captured_requests,
    coerce_adaptation_options,
    complete_json,
    parse_json_content,
    reset_chat_transport,
    set_chat_transport,
)
from backend.providers.llm_catalog import (
    DEEPSEEK_FLASH,
    DEEPSEEK_PRO,
    DEEPSEEK_VISION,
    ModelConfigError,
    default_for_stage,
    validate_stage_selection,
)
from backend.providers.vision_adapter import (
    VisionAdapterError,
    build_vision_request,
    captured_vision_requests,
    payload_contains_data_url,
    reset_vision_transport,
    set_vision_transport,
)
from backend.services.adaptation_service import AdaptationError, start_adaptation_workflow
from backend.services.asset_service import persist_binary_asset
from backend.services.job_service import get_job, get_recent_job_events
from backend.services.model_config_service import (
    resolve_stage_model,
    save_stage_config,
    set_generation_mode,
)
from backend.services.project_service import get_project
from backend.services.vision_review_service import review_project_image

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)
SAMPLE = (
    "方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。"
    "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。"
    "但是族中长老已经设下阻碍，他只能选择冒险一搏。"
    "最终他停在山门前，留下未说完的话。"
)


def _guard_network() -> None:
    import urllib.request

    def blocked(*_args, **_kwargs):
        raise AssertionError("P7-A tests must not open network sockets")

    urllib.request.urlopen = blocked  # type: ignore[assignment]


@contextmanager
def _without_env(*keys: str):
    saved = {key: os.environ.pop(key, None) for key in keys}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _project(title: str = "P7-A 模型选择") -> str:
    init_environment()
    init_db()
    project_id = f"p7a_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode, status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, SAMPLE, "cinematic", "16:9", 5, "auto", "created", "direct", now, now),
        )
    return project_id


def _cleanup(project_id: str) -> None:
    reset_chat_transport()
    reset_vision_transport()
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)


def _json_transport(payload: dict) -> FunctionTransport:
    def send(prepared):
        return {"id": "chatcmpl_mock", "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}

    return FunctionTransport(send)


def test_default_is_preselect_not_lock() -> None:
    project_id = _project()
    try:
        first = resolve_stage_model(project_id, "story_bible")
        assert first["provider"] == "deepseek"
        assert first["model"] == DEEPSEEK_FLASH
        assert first["is_default"] is True
        assert first["selected_by_user"] is False
        save_stage_config(project_id, "story_bible", provider="deepseek", model=DEEPSEEK_PRO)
        second = resolve_stage_model(project_id, "story_bible")
        assert second["model"] == DEEPSEEK_PRO
        assert second["selected_by_user"] is True
        assert second["is_default"] is False
        req1 = build_text_request(provider=first["provider"], model=first["model"], messages=adaptation_messages("t", SAMPLE, "s", 5))
        req2 = build_text_request(provider=second["provider"], model=second["model"], messages=adaptation_messages("t", SAMPLE, "s", 5))
        assert req1.body["model"] == DEEPSEEK_FLASH
        assert req2.body["model"] == DEEPSEEK_PRO
        print("PASS: default model is preselect; user choice changes the request plan")
    finally:
        _cleanup(project_id)


def test_unconfigured_does_not_switch() -> None:
    with _without_env("DEEPSEEK_API_KEY"):
        payload = get_provider_capabilities()
        flash = next(item for item in payload["llm"] if item["model"] == DEEPSEEK_FLASH)
        assert flash["configured"] is False
        assert flash["is_default"] is True
        default = default_for_stage("text_understanding")
        assert default["provider"] == "deepseek"
        assert default["model"] == DEEPSEEK_FLASH
        assert default["configured"] is False
        print("PASS: unconfigured DeepSeek stays the default preselect and does not silent-switch")


def test_text_vision_roles_do_not_mix() -> None:
    try:
        validate_stage_selection("story_bible", "deepseek", DEEPSEEK_VISION)
        raise AssertionError("vision model must not be accepted for text stages")
    except ModelConfigError as exc:
        assert exc.code == "ROLE_MISMATCH"
        assert "视觉" in str(exc)
    try:
        validate_stage_selection("vision_review", "deepseek", DEEPSEEK_FLASH)
        raise AssertionError("text model must not be accepted for vision stages")
    except ModelConfigError as exc:
        assert exc.code == "ROLE_MISMATCH"
        assert "文本" in str(exc)
    ok = validate_stage_selection("text_understanding", "deepseek", DEEPSEEK_FLASH)
    assert ok["supports_vision"] is False
    print("PASS: text and vision roles are not interchangeable")


def test_text_request_construction() -> None:
    prepared = build_text_request(
        provider="deepseek",
        model=DEEPSEEK_FLASH,
        messages=adaptation_messages("青茅山", SAMPLE, "cinematic", 5),
    )
    assert prepared.url.endswith("/chat/completions")
    assert prepared.body["model"] == DEEPSEEK_FLASH
    assert prepared.body["response_format"] == {"type": "json_object"}
    assert all(not isinstance(message.get("content"), list) for message in prepared.body["messages"])
    meta = json.dumps(prepared.public_metadata(), ensure_ascii=False)
    assert SAMPLE[:20] not in meta
    assert "sk-" not in meta
    print("PASS: text request construction uses Chat Completions JSON mode without images")


def test_json_parse_success_and_failure() -> None:
    parsed = parse_json_content('{"options":[{"title":"冲突优先"}]}')
    assert parsed["options"][0]["title"] == "冲突优先"
    try:
        parse_json_content("this is not json")
        raise AssertionError("invalid JSON must fail")
    except JsonParseError:
        pass
    fallback = [{"title": "本地", "rationale": "r", "protagonist_goal": "g", "conflict": "c", "ending_orientation": "e", "suggested_duration_seconds": 30, "suggested_shot_count": 4, "source_excerpt": SAMPLE[:12], "source_start": 0, "source_end": 12}]
    coerced = coerce_adaptation_options({"options": [{"title": "模型方案", "rationale": "因为冲突", "protagonist_goal": "拿下", "conflict": "争夺", "ending_orientation": "悬念", "source_excerpt": SAMPLE[:12]}]}, fallback, SAMPLE)
    assert coerced[0]["title"] == "模型方案"
    print("PASS: JSON output parse success and failure")


def test_live_strict_fails_without_authorization() -> None:
    project_id = _project()
    reset_chat_transport()
    try:
        set_generation_mode(project_id, "live_strict")
        try:
            start_adaptation_workflow(project_id)
            raise AssertionError("live_strict must fail when live calls are blocked")
        except AdaptationError as exc:
            assert exc.code == "LIVE_LLM_FAILED"
            assert "失败" in str(exc)
        with connect() as conn:
            options = conn.execute("SELECT COUNT(*) AS n FROM adaptation_options WHERE project_id = ?", (project_id,)).fetchone()["n"]
        assert options == 0
        print("PASS: live_strict fails the task and does not fake success")
    finally:
        _cleanup(project_id)


def test_local_fallback_is_explicit() -> None:
    project_id = _project()
    reset_chat_transport()
    try:
        set_generation_mode(project_id, "live_with_local_fallback")
        start_adaptation_workflow(project_id)
        project = get_project(project_id)
        assert project["adaptation_options"]
        assert all(item.get("used_local_fallback") == 1 for item in project["adaptation_options"])
        assert all(item.get("source") == "local_fallback" for item in project["adaptation_options"])
        events = get_recent_job_events(project_id)
        blob = json.dumps(events, ensure_ascii=False)
        assert "本地回退" in blob
        print("PASS: live_with_local_fallback records explicit local fallback")
    finally:
        _cleanup(project_id)


def test_live_mock_transport_maps_to_p4() -> None:
    project_id = _project()
    payload = {
        "options": [
            {
                "title": "模型冲突方案",
                "rationale": "抓住争夺",
                "protagonist_goal": "拿下传承",
                "conflict": "争夺传承",
                "ending_orientation": "山门未完",
                "suggested_duration_seconds": 30,
                "suggested_shot_count": 4,
                "source_excerpt": "方源走在青茅山的夜路上",
            }
        ]
    }
    set_chat_transport(_json_transport(payload))
    try:
        set_generation_mode(project_id, "live_strict")
        start_adaptation_workflow(project_id)
        project = get_project(project_id)
        assert project["adaptation_options"][0]["title"] == "模型冲突方案"
        assert project["adaptation_options"][0]["source"] == "live_llm"
        assert project["adaptation_options"][0]["used_local_fallback"] == 0
        assert captured_requests()[0].body["model"] == DEEPSEEK_FLASH
        print("PASS: mocked live LLM maps onto existing P4 option fields")
    finally:
        _cleanup(project_id)


def test_lineage_preserved_after_model_change() -> None:
    project_id = _project()
    try:
        start_adaptation_workflow(project_id)
        before = get_project(project_id)["adaptation_options"][0]
        old_model = before.get("model") or DEEPSEEK_FLASH
        save_stage_config(project_id, "adaptation_options", provider="deepseek", model=DEEPSEEK_PRO)
        after = get_project(project_id)
        kept = after["adaptation_options"][0]
        assert kept["id"] == before["id"]
        assert kept["title"] == before["title"]
        assert (kept.get("model") or old_model) == old_model
        assert "storyboard" in (after.get("stale_stages") or [])
        assert "bible" in (after.get("stale_stages") or [])
        print("PASS: old result lineage is kept; necessary downstream is marked stale")
    finally:
        _cleanup(project_id)


def test_vision_request_and_redaction() -> None:
    project_id = _project()
    other_id = _project("其他项目")
    try:
        asset_id = persist_binary_asset(
            project_id, "first-frame", "首帧", "desc", "prompt", PNG_BYTES, ".png", "test", "local-fixture",
        )
        other_asset = persist_binary_asset(
            other_id, "first-frame", "他人首帧", "desc", "prompt", PNG_BYTES, ".png", "test", "local-fixture",
        )
        with connect() as conn:
            path = conn.execute("SELECT file_path FROM assets WHERE id = ?", (asset_id,)).fetchone()["file_path"]
            other_path = conn.execute("SELECT file_path FROM assets WHERE id = ?", (other_asset,)).fetchone()["file_path"]
        prepared = build_vision_request(
            project_id=project_id,
            public_path=path,
            prompt="describe",
            provider="deepseek",
            model=DEEPSEEK_VISION,
            role="first_frame",
        )
        assert payload_contains_data_url(prepared.body)
        meta = prepared.public_metadata()
        assert meta["asset_id"] == asset_id
        assert meta["provider"] == "deepseek"
        assert meta["model"] == DEEPSEEK_VISION
        assert meta["transport_mode"] == "data_url"
        assert "base64" not in json.dumps(meta)
        assert "data:image" not in json.dumps(meta)
        try:
            build_vision_request(project_id=project_id, public_path=other_path, prompt="x", provider="deepseek", model=DEEPSEEK_VISION)
            raise AssertionError("cross-project assets must be rejected")
        except VisionAdapterError as exc:
            assert exc.code in {"ASSET_NOT_FOUND", "INVALID_ASSET_PATH"}
        try:
            build_vision_request(project_id=project_id, public_path="C:/tmp/outside.png", prompt="x", provider="deepseek", model=DEEPSEEK_VISION)
            raise AssertionError("external paths must be rejected")
        except VisionAdapterError:
            pass
        review = review_project_image(project_id, asset_id=asset_id, role="first_frame")
        blob = json.dumps(review.get("latest_vision_review") or {}, ensure_ascii=False)
        events = json.dumps(get_recent_job_events(project_id), ensure_ascii=False)
        with connect() as conn:
            stored = conn.execute("SELECT * FROM vision_reviews WHERE project_id = ?", (project_id,)).fetchone()
            db_blob = json.dumps(dict(stored), ensure_ascii=False)
            transfers = list(conn.execute("SELECT request_reference, metadata_json FROM media_transfers").fetchall())
        assert "data:image" not in blob
        assert "data:image" not in events
        assert "data:image" not in db_blob
        assert "base64," not in db_blob
        for row in transfers:
            assert row["request_reference"] != prepared.body["messages"][1]["content"][1]["image_url"]["url"]
            assert "data:image" not in (row["request_reference"] or "")
        print("PASS: vision Data URL stays in-memory; cross-project and external paths are rejected")
    finally:
        _cleanup(project_id)
        _cleanup(other_id)


def test_p1_video_matrix_not_regressed() -> None:
    plan = validate_video_generation(
        provider="minimax",
        model=None,
        video_mode="t2v",
        duration_seconds=6,
        aspect_ratio="16:9",
        first_frame_path=None,
        last_frame_path=None,
    )
    assert plan["provider"] == "minimax"
    ark = validate_video_generation(
        provider="ark",
        model=None,
        video_mode="i2v",
        duration_seconds=5,
        aspect_ratio="16:9",
        first_frame_path="/assets/demo/first.jpg",
        last_frame_path=None,
    )
    assert ark["provider"] == "ark"
    print("PASS: P1 video capability matrix still accepts MiniMax default and Ark switch")


def main() -> None:
    _guard_network()
    init_environment()
    init_db()
    os.environ.pop("VISIONCRAFT_ALLOW_LIVE_LLM", None)
    os.environ.pop("VISIONCRAFT_ALLOW_LIVE_VISION", None)
    test_default_is_preselect_not_lock()
    test_unconfigured_does_not_switch()
    test_text_vision_roles_do_not_mix()
    test_text_request_construction()
    test_json_parse_success_and_failure()
    test_live_strict_fails_without_authorization()
    test_local_fallback_is_explicit()
    test_live_mock_transport_maps_to_p4()
    test_lineage_preserved_after_model_change()
    test_vision_request_and_redaction()
    test_p1_video_matrix_not_regressed()
    print("PASS: P7-A stage model selection and adapters (no live network)")


if __name__ == "__main__":
    main()
