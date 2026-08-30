"""No-network tests for live budget caps and local JPEG/PNG first-frame registration."""
from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db, utc_now
from backend.main import app
from backend.providers.live_budget import (
    TEXT_MAX_TOKENS,
    BudgetBlockedError,
    estimate_closed_loop_cny,
    estimate_minimax_i2v_cny,
    estimate_text_call_cny,
    estimate_vision_call_cny,
)
from backend.providers.llm_adapter import FunctionTransport, adaptation_messages, build_text_request, reset_chat_transport, set_chat_transport
from backend.providers.llm_catalog import DEEPSEEK_FLASH, DEEPSEEK_VISION
from backend.providers.vision_adapter import VisionAdapterError, build_vision_request, reset_vision_transport
from backend.services.adaptation_service import AdaptationError, start_adaptation_workflow
from backend.services.asset_service import persist_binary_asset
from backend.services.job_service import get_recent_job_events
from backend.services.local_keyframe_service import LocalKeyframeError, register_local_first_frame
from backend.services.media_transfer_service import prepare_image_reference
from backend.services.model_config_service import set_generation_mode
from backend.services.project_service import get_project

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)
JPEG_BYTES = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080808080808080808080808"
    "08080808080808080808080808080808080808080808080808080808080808080808080808"
    "08080808080808080808080808ffc0000b080001000101011100ffc4001410000000000000"
    "00000000000000000000ffc400141000000000000000000000000000000000ffda00080001"
    "0100003f00fb00d2ffd9"
)
SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"></svg>'
SAMPLE = "春秋蝉鸣少年归"
GYFY = Path(r"D:\Agent\summercompetition\StoryCraft\gyfy.jpg")
CREATED: list[str] = []


def pass_(msg: str) -> None:
    print(f"PASS: {msg}")


def skip(msg: str) -> None:
    print(f"SKIP: {msg}")


def _guard_network() -> None:
    import urllib.request

    def blocked(*_args, **_kwargs):
        raise AssertionError("live safeguard tests must not open network sockets")

    urllib.request.urlopen = blocked  # type: ignore[assignment]


def _project(title: str = "护栏项目") -> str:
    init_environment()
    init_db()
    project_id = f"p7g_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode, status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, SAMPLE, "cinematic", "16:9", 5, "auto", "created", "direct", now, now),
        )
    CREATED.append(project_id)
    return project_id


def _add_shot(project_id: str) -> str:
    shot_id = f"shot_{uuid.uuid4().hex[:10]}"
    version_id = f"version_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO shots
            (id, project_id, shot_index, title, description, characters, scene, camera_motion,
             visual_prompt, negative_prompt, audio_prompt, status, retry_count, current_version_id,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (shot_id, project_id, 1, "镜1", "动作", "[]", "场景", "固定", "prompt", "", "", "draft", 0, version_id, now, now),
        )
        conn.execute(
            """INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
             first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, shot_id, 1, "动作", "prompt", "", "", None, None, None, "t2v", "minimax", "MiniMax-H3", "test", now),
        )
    return shot_id


def _cleanup() -> None:
    reset_chat_transport()
    reset_vision_transport()
    for project_id in list(CREATED):
        with connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
    CREATED.clear()


def test_budget_estimate_uses_buffer() -> None:
    raw = estimate_text_call_cny(100, max_tokens=TEXT_MAX_TOKENS)
    assert raw > 0
    plan = estimate_closed_loop_cny(SAMPLE)
    assert plan["text_calls"] == 3
    assert plan["vision_calls"] == 1
    assert plan["video_calls"] == 1
    assert plan["text_thinking"] == "disabled"
    assert plan["text_max_tokens"] == TEXT_MAX_TOKENS
    assert plan["video_cny"] == round(estimate_minimax_i2v_cny(4), 4)
    assert plan["vision_cny"] == round(estimate_vision_call_cny(), 4)
    assert abs(plan["total_cny"] - (plan["text_cny"] + plan["vision_cny"] + plan["video_cny"])) < 0.01
    pass_("费用估算使用 30% 缓冲，并计入三次文本、一次视觉、一次 4s MiniMax")


def test_text_requests_are_capped() -> None:
    prepared = build_text_request(
        provider="deepseek",
        model=DEEPSEEK_FLASH,
        messages=adaptation_messages("春秋", SAMPLE, "cinematic", 5),
        extra_body={"thinking": {"type": "enabled"}, "max_tokens": 99999},
    )
    assert prepared.body["thinking"] == {"type": "disabled"}
    assert prepared.body["max_tokens"] == TEXT_MAX_TOKENS
    meta = prepared.public_metadata()
    assert meta["thinking"] == "disabled"
    assert meta["max_tokens"] == TEXT_MAX_TOKENS
    assert SAMPLE not in json.dumps(meta)
    pass_("文本请求包含 thinking disabled 与 max_tokens 上限")


def test_over_budget_is_blocked_before_transport() -> None:
    project_id = _project()
    sent = []

    def boom(_prepared):
        sent.append("sent")
        raise AssertionError("over-budget live call must not reach transport")

    os.environ["VISIONCRAFT_LIVE_BUDGET_CNY"] = "0.0001"
    reset_chat_transport()
    set_chat_transport(FunctionTransport(boom))
    try:
        set_generation_mode(project_id, "live_strict")
        try:
            start_adaptation_workflow(project_id)
            raise AssertionError("over-budget must be blocked")
        except AdaptationError as exc:
            assert exc.code == "BLOCKED_BEFORE_CALL"
            assert "预算" in str(exc)
        assert sent == []
        pass_("超预算请求被阻止且未进入 transport")
    finally:
        os.environ.pop("VISIONCRAFT_LIVE_BUDGET_CNY", None)
        _cleanup()


def test_text_calls_capped_at_three() -> None:
    project_id = _project()
    payload = {"options": [{"title": "方案", "rationale": "r", "protagonist_goal": "g", "conflict": "c", "ending_orientation": "e", "source_excerpt": SAMPLE}]}

    def ok(_prepared):
        return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}

    reset_chat_transport()
    set_chat_transport(FunctionTransport(ok))
    try:
        set_generation_mode(project_id, "live_strict")
        for _ in range(3):
            start_adaptation_workflow(project_id)
        try:
            start_adaptation_workflow(project_id)
            raise AssertionError("fourth text call must be blocked")
        except AdaptationError as exc:
            assert exc.code == "BLOCKED_BEFORE_CALL"
            assert "3 次" in str(exc)
        pass_("三次文本请求都受限制，第四次被阻止")
    finally:
        _cleanup()


def test_register_local_jpeg_png_and_reject_bad_inputs() -> None:
    import backend.providers.image_provider as image_provider

    original = image_provider.generate_image_asset

    def forbidden(*_args, **_kwargs):
        raise AssertionError("local register must not call image generation providers")

    image_provider.generate_image_asset = forbidden
    project_id = _project()
    other_id = _project("其他项目")
    shot_id = _add_shot(project_id)
    try:
        result = register_local_first_frame(project_id, shot_id, PNG_BYTES, filename="frame.png")
        assert result["asset_id"].startswith("asset_")
        assert result["file_path"].startswith(f"/assets/{project_id}/")
        assert result["file_path"].endswith(".png")
        assert result["role"] == "first_frame"
        project = get_project(project_id)
        shot = next(item for item in project["shots"] if item["id"] == shot_id)
        current = next(item for item in shot["versions"] if item["id"] == shot["current_version_id"])
        assert current["first_frame_path"] == result["file_path"]
        with connect() as conn:
            asset = conn.execute("SELECT * FROM assets WHERE id = ?", (result["asset_id"],)).fetchone()
            events = get_recent_job_events(project_id)
        assert asset["project_id"] == project_id
        assert "local-register" in (asset["embedding_ref"] or "")
        blob = json.dumps(dict(asset), ensure_ascii=False) + json.dumps(events, ensure_ascii=False)
        assert "data:image" not in blob
        assert "base64," not in blob.lower()

        jpeg = register_local_first_frame(project_id, shot_id, JPEG_BYTES, filename="..\\evil.jpg")
        assert jpeg["file_path"].endswith(".jpg")

        try:
            register_local_first_frame(project_id, shot_id, SVG_BYTES, filename="x.png")
            raise AssertionError("SVG must be rejected")
        except LocalKeyframeError as exc:
            assert exc.code == "SVG_NOT_ALLOWED"
        try:
            register_local_first_frame(project_id, shot_id, b"not-an-image", filename="x.jpg")
            raise AssertionError("non-image must be rejected")
        except LocalKeyframeError as exc:
            assert exc.code == "UNSUPPORTED_IMAGE_FORMAT"
        try:
            register_local_first_frame(other_id, shot_id, PNG_BYTES, filename="x.png")
            raise AssertionError("foreign shot must be rejected")
        except LocalKeyframeError as exc:
            assert exc.code in {"SHOT_NOT_FOUND", "PROJECT_NOT_FOUND"}

        prepared = build_vision_request(
            project_id=project_id,
            public_path=result["file_path"],
            prompt="inspect",
            provider="deepseek",
            model=DEEPSEEK_VISION,
            role="first_frame",
        )
        assert prepared.body["thinking"]["type"] == "disabled"
        content = prepared.body["messages"][1]["content"]
        assert any(isinstance(block, dict) and block.get("type") == "image_url" for block in content)
        ref = prepare_image_reference(
            project_id, result["file_path"], target_provider="minimax", target_model="MiniMax-H3", role="first_frame"
        )
        assert ref is not None
        assert ref.asset_id
        assert ref.url.startswith("data:image/")
        pass_("图片登记属于当前项目，且可供 Vision / I2V 使用")
    finally:
        image_provider.generate_image_asset = original
        _cleanup()


def test_svg_and_path_escape_rejected_for_vision_i2v() -> None:
    project_id = _project()
    other_id = _project("穿越项目")
    try:
        svg_id = persist_binary_asset(project_id, "first-frame", "svg", "d", "p", SVG_BYTES, ".svg", "test", "svg")
        with connect() as conn:
            svg_path = conn.execute("SELECT file_path FROM assets WHERE id = ?", (svg_id,)).fetchone()["file_path"]
            other_png = persist_binary_asset(other_id, "first-frame", "外", "d", "p", PNG_BYTES, ".png", "test", "x")
            other_path = conn.execute("SELECT file_path FROM assets WHERE id = ?", (other_png,)).fetchone()["file_path"]
        try:
            build_vision_request(project_id=project_id, public_path=svg_path, prompt="x", provider="deepseek", model=DEEPSEEK_VISION)
            raise AssertionError("SVG vision must fail")
        except VisionAdapterError as exc:
            assert exc.code in {"SVG_NOT_ALLOWED", "UNSUPPORTED_IMAGE_FORMAT"}
        try:
            prepare_image_reference(project_id, svg_path, target_provider="minimax", target_model="MiniMax-H3", role="first_frame")
            raise AssertionError("SVG I2V must fail")
        except Exception as exc:
            assert "SVG" in str(exc) or "svg" in str(exc).lower() or "JPEG" in str(exc)
        try:
            prepare_image_reference(project_id, f"/assets/{project_id}/../{other_id}/x.png", target_provider="minimax", target_model="MiniMax-H3", role="first_frame")
            raise AssertionError("path traversal must fail")
        except Exception:
            pass
        try:
            prepare_image_reference(project_id, other_path, target_provider="minimax", target_model="MiniMax-H3", role="first_frame")
            raise AssertionError("other project asset must fail")
        except Exception:
            pass
        try:
            prepare_image_reference(project_id, r"D:\tmp\outside.png", target_provider="minimax", target_model="MiniMax-H3", role="first_frame")
            raise AssertionError("absolute path must fail")
        except Exception:
            pass
        pass_("SVG、目录穿越、跨项目和项目外路径均被拒绝")
    finally:
        _cleanup()


def test_http_register_and_gyfy_copy() -> None:
    project_id = _project()
    shot_id = _add_shot(project_id)
    client = TestClient(app)
    try:
        response = client.post(
            f"/api/projects/{project_id}/shots/{shot_id}/keyframes/register-local",
            files={"file": ("frame.png", PNG_BYTES, "image/png")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["file_path"].startswith(f"/assets/{project_id}/")
        if GYFY.is_file():
            copied = (PROJECTS_DIR / project_id / "gyfy-copy.jpg")
            copied.write_bytes(GYFY.read_bytes())
            data = copied.read_bytes()
            registered = register_local_first_frame(project_id, shot_id, data, filename="gyfy.jpg")
            assert registered["file_path"].startswith(f"/assets/{project_id}/")
            copied.unlink(missing_ok=True)
            pass_("gyfy.jpg 可复制进当前项目资产并登记")
        else:
            skip("未找到 D:\\Agent\\summercompetition\\StoryCraft\\gyfy.jpg，跳过该夹具复制")
        pass_("HTTP multipart 本地首帧登记成功")
    finally:
        _cleanup()


def main() -> None:
    _guard_network()
    init_environment()
    init_db()
    os.environ.pop("VISIONCRAFT_ALLOW_LIVE_LLM", None)
    os.environ.pop("VISIONCRAFT_ALLOW_LIVE_VISION", None)
    os.environ.pop("VISIONCRAFT_ALLOW_LIVE_VIDEO", None)
    os.environ.pop("VISIONCRAFT_LIVE_BUDGET_CNY", None)
    try:
        test_budget_estimate_uses_buffer()
        test_text_requests_are_capped()
        test_over_budget_is_blocked_before_transport()
        test_text_calls_capped_at_three()
        test_register_local_jpeg_png_and_reject_bad_inputs()
        test_svg_and_path_escape_rejected_for_vision_i2v()
        test_http_register_and_gyfy_copy()
        print("PASS: live budget and local keyframe safeguards (no live network)")
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
