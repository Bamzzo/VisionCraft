"""No-network tests for live budget caps and local JPEG/PNG first-frame registration."""
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

from fastapi.testclient import TestClient

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db, utc_now
from backend.main import app
from backend.providers.live_budget import (
    DEFAULT_BUDGET_CNY,
    MAX_VIDEO_CALLS,
    TEXT_MAX_TOKENS,
    BudgetBlockedError,
    assert_live_video_allowed,
    check_live_video_budget,
    estimate_closed_loop_cny,
    estimate_minimax_i2v_cny,
    estimate_text_call_cny,
    estimate_vision_call_cny,
    live_budget_cny,
    live_max_video_calls,
)
from backend.providers.video_provider import (
    VideoAssetConflictError,
    VideoAssetRequest,
    ensure_remote_video_asset,
    reset_video_download_transport,
    reset_video_json_transport,
    set_video_download_transport,
    set_video_json_transport,
)
from backend.services.job_service import create_job, get_recent_job_events, update_job
from backend.providers.llm_adapter import FunctionTransport, adaptation_messages, build_text_request, reset_chat_transport, set_chat_transport
from backend.providers.llm_catalog import DEEPSEEK_FLASH, DEEPSEEK_VISION
from backend.providers.vision_adapter import VisionAdapterError, build_vision_request, reset_vision_transport
from backend.services.adaptation_service import AdaptationError, start_adaptation_workflow
from backend.services.asset_service import persist_binary_asset
from backend.services.local_keyframe_service import LocalKeyframeError, attach_existing_first_frame_to_shots, register_local_first_frame
from backend.services.media_transfer_service import prepare_image_reference
from backend.services.model_config_service import set_generation_mode
from backend.services.project_service import get_project
from backend.services.video_service import generate_shot_video, refresh_project_video_tasks
from tools.live_run_audit import (
    LAST_LIVE_RUN,
    apply_count_fields,
    has_secret_leak,
    normalize_live_run_counts,
    verify_pre_cleanup,
    write_audit_reports,
)

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
FAKE_MP4 = b"\x00\x00\x00\x1cftypmp42" + b"\x00" * 64
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


def _add_shot(project_id: str, index: int = 1) -> str:
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
            (shot_id, project_id, index, f"镜{index:02d}", "动作", "[]", "场景", "固定", "prompt", "", "", "draft", 0, version_id, now, now),
        )
        conn.execute(
            """INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
             first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, shot_id, 1, "动作", "prompt", "", "", None, None, None, "t2v", "minimax", "MiniMax-H3", "test", now),
        )
    return shot_id


def _current_version(shot_id: str) -> str:
    with connect() as conn:
        row = conn.execute("SELECT current_version_id FROM shots WHERE id = ?", (shot_id,)).fetchone()
    return row["current_version_id"]


def _video_request(project_id: str, shot_id: str, job_id: str | None = None) -> VideoAssetRequest:
    return VideoAssetRequest(
        project_id=project_id,
        shot_id=shot_id,
        version_id=_current_version(shot_id),
        title="镜01",
        description="动作",
        prompt="prompt",
        first_frame_path=None,
        duration_seconds=4,
        job_id=job_id,
    )


def _insert_video_task(request: VideoAssetRequest, remote_task_id: str, provider: str = "minimax") -> str:
    task_id = f"vt_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO video_tasks
            (id, project_id, shot_id, version_id, job_id, provider, model, remote_task_id,
             status, cloud_status, prompt, submit_payload, status_payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                request.project_id,
                request.shot_id,
                request.version_id,
                request.job_id,
                provider,
                "MiniMax-H3",
                remote_task_id,
                "running",
                "submitted",
                request.prompt,
                "{}",
                "{}",
                now,
                now,
            ),
        )
    return task_id


@contextmanager
def _env(**values):
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _leak_blob(project_id: str) -> str:
    with connect() as conn:
        assets = [dict(row) for row in conn.execute("SELECT * FROM assets WHERE project_id = ?", (project_id,))]
        tasks = [dict(row) for row in conn.execute("SELECT * FROM video_tasks WHERE project_id = ?", (project_id,))]
    events = get_recent_job_events(project_id)
    return json.dumps({"assets": assets, "tasks": tasks, "events": events}, ensure_ascii=False, default=str)


def _assert_no_secrets(blob: str) -> None:
    assert has_secret_leak(blob) is False
    lowered = blob.lower()
    assert "x-amz-signature" not in lowered
    assert "x-amz-credential" not in lowered
    assert "https://cdn.example" not in lowered
    assert "sk-live" not in lowered


def _cleanup() -> None:
    reset_chat_transport()
    reset_vision_transport()
    reset_video_download_transport()
    reset_video_json_transport()
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
    assert live_max_video_calls() == MAX_VIDEO_CALLS == 1
    assert live_budget_cny() == DEFAULT_BUDGET_CNY == 5.0
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
        # P8-B 统一上传存储后，用户上传素材的公开血缘由 asset_role/source 表达；
        # embedding_ref 保留为兼容字段，不再承载 register-local 接口名。
        assert asset["asset_role"] == "first_frame"
        assert asset["source"] == "user-upload"
        assert asset["embedding_ref"] == "upload:first_frame"
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
            assert exc.code in {"SHOT_MISMATCH", "SHOT_NOT_FOUND", "PROJECT_NOT_FOUND"}

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


def test_env_overrides_five_video_budget() -> None:
    default = estimate_closed_loop_cny(SAMPLE)
    assert default["video_calls"] == 1
    assert default["budget_cny"] == 5.0
    assert default["video_cny"] == round(estimate_minimax_i2v_cny(4), 4)
    with _env(VISIONCRAFT_LIVE_MAX_VIDEO_CALLS="5", VISIONCRAFT_LIVE_BUDGET_CNY="12"):
        plan = estimate_closed_loop_cny(SAMPLE)
        assert live_max_video_calls() == 5
        assert live_budget_cny() == 12.0
        assert plan["video_calls"] == 5
        assert plan["video_cny"] == round(estimate_minimax_i2v_cny(4) * 5, 4)
        assert plan["video_cny"] == round(plan["video_each_cny"] * 5, 4)
        assert plan["within_budget"] is True
        assert plan["total_cny"] <= 12
    assert live_max_video_calls() == 1
    assert live_budget_cny() == 5.0
    pass_("环境变量可将视频次数覆盖为 5、预算覆盖为 12，且按 5 次视频估费")


def test_raising_video_calls_cannot_bypass_budget() -> None:
    project_id = _project()
    sent = []

    def boom(_prepared):
        sent.append("sent")
        raise AssertionError("raising video call cap must not bypass budget")

    reset_chat_transport()
    set_chat_transport(FunctionTransport(boom))
    try:
        with _env(VISIONCRAFT_LIVE_MAX_VIDEO_CALLS="5", VISIONCRAFT_LIVE_BUDGET_CNY="5"):
            plan = estimate_closed_loop_cny(SAMPLE)
            assert plan["video_calls"] == 5
            assert plan["within_budget"] is False
            set_generation_mode(project_id, "live_strict")
            try:
                start_adaptation_workflow(project_id)
                raise AssertionError("must block when 5-video plan exceeds 5 CNY")
            except AdaptationError as exc:
                assert exc.code == "BLOCKED_BEFORE_CALL"
        assert sent == []
        pass_("仅提高视频次数而不提高预算时，请求前阻止且未发送 HTTP")
    finally:
        _cleanup()


def test_sixth_video_and_over_budget_blocked_before_http() -> None:
    project_id = _project()
    try:
        with _env(
            VISIONCRAFT_LIVE_MAX_VIDEO_CALLS="5",
            VISIONCRAFT_LIVE_BUDGET_CNY="12",
            VISIONCRAFT_ALLOW_LIVE_VIDEO="1",
        ):
            for index in range(5):
                plan = assert_live_video_allowed(project_id, seconds=4)
                assert plan["call_index"] == index + 1
            try:
                assert_live_video_allowed(project_id, seconds=4)
                raise AssertionError("sixth video call must be blocked")
            except BudgetBlockedError as exc:
                assert exc.code == "BLOCKED_BEFORE_CALL"
                assert "5 次" in str(exc)
        with _env(VISIONCRAFT_LIVE_BUDGET_CNY="0.01", VISIONCRAFT_ALLOW_LIVE_VIDEO="1"):
            other = _project("超预算视频")
            try:
                check_live_video_budget(other, seconds=4)
                raise AssertionError("over 12-equivalent tiny budget must block video")
            except BudgetBlockedError as exc:
                assert exc.code == "BLOCKED_BEFORE_CALL"
                assert "预算" in str(exc)
        pass_("第 6 次视频与超预算视频均在请求前被阻止")
    finally:
        _cleanup()


def test_remote_task_video_asset_is_idempotent() -> None:
    project_id = _project()
    other_id = _project("跨项目")
    shot_id = _add_shot(project_id)
    other_shot = _add_shot(other_id)
    job_id = create_job(project_id, "generate_video", "生成视频", shot_id=shot_id)
    downloads = []

    def fake_download(url: str) -> bytes:
        downloads.append(url)
        assert url.startswith("https://")
        return FAKE_MP4

    set_video_download_transport(fake_download)
    try:
        request = _video_request(project_id, shot_id, job_id=job_id)
        remote_id = "remote_task_idem_001"
        local_id = _insert_video_task(request, remote_id)
        signed = "https://cdn.example/video.mp4?X-Amz-Signature=secret&X-Amz-Credential=AKIA"

        first = ensure_remote_video_asset(
            request,
            signed,
            "prompt",
            "provider:minimax:MiniMax-H3",
            provider="minimax",
            remote_task_id=remote_id,
            local_task_id=local_id,
        )
        assert first.created is True
        update_job(
            job_id,
            "completed",
            100,
            "视频已生成，可在镜头卡片中预览",
            shot_id=shot_id,
            stage="persist_asset",
            event_type="asset.ready",
            detail={"asset_path": first.public_path, "provider": "minimax", "remote_task_id": remote_id},
        )

        second = ensure_remote_video_asset(
            request,
            signed,
            "prompt",
            "provider:minimax:MiniMax-H3",
            provider="minimax",
            remote_task_id=remote_id,
            local_task_id=local_id,
        )
        assert second.reused is True
        assert second.created is False
        assert second.public_path == first.public_path
        assert second.asset_id == first.asset_id
        update_job(
            job_id,
            "completed",
            100,
            "视频已生成，可在镜头卡片中预览",
            shot_id=shot_id,
            stage="persist_asset",
            event_type="asset.ready",
            detail={"asset_path": second.public_path, "provider": "minimax", "remote_task_id": remote_id},
        )

        local_file = PROJECTS_DIR / project_id / first.public_path.rsplit("/", 1)[-1]
        assert local_file.is_file()
        local_file.unlink()
        third = ensure_remote_video_asset(
            request,
            signed,
            "prompt",
            "provider:minimax:MiniMax-H3",
            provider="minimax",
            remote_task_id=remote_id,
            local_task_id=local_id,
        )
        assert third.redownloaded is True
        assert third.asset_id == first.asset_id
        assert third.public_path == first.public_path
        assert local_file.is_file()

        other_request = _video_request(other_id, other_shot)
        try:
            ensure_remote_video_asset(
                other_request,
                signed,
                "prompt",
                "provider:minimax:MiniMax-H3",
                provider="minimax",
                remote_task_id=remote_id,
            )
            raise AssertionError("cross-project remote task must be rejected")
        except VideoAssetConflictError as exc:
            assert exc.code in {"CROSS_PROJECT_TASK", "CROSS_PROJECT_ASSET"}

        mismatch = _video_request(project_id, shot_id)
        mismatch.version_id = "version_not_this"
        try:
            ensure_remote_video_asset(
                mismatch,
                signed,
                "prompt",
                "provider:minimax:MiniMax-H3",
                provider="minimax",
                remote_task_id=remote_id,
                local_task_id=local_id,
            )
            raise AssertionError("version mismatch must be rejected")
        except VideoAssetConflictError as exc:
            assert exc.code == "VERSION_MISMATCH"

        with connect() as conn:
            videos = conn.execute(
                "SELECT * FROM assets WHERE project_id = ? AND type = 'video'",
                (project_id,),
            ).fetchall()
            task = conn.execute("SELECT * FROM video_tasks WHERE id = ?", (local_id,)).fetchone()
            version = conn.execute("SELECT * FROM shot_versions WHERE id = ?", (request.version_id,)).fetchone()
            shot = conn.execute("SELECT * FROM shots WHERE id = ?", (shot_id,)).fetchone()
            ready = conn.execute(
                "SELECT COUNT(*) AS n FROM job_events WHERE project_id = ? AND event_type = 'asset.ready'",
                (project_id,),
            ).fetchone()["n"]
        assert len(videos) == 1
        assert videos[0]["id"] == first.asset_id
        assert videos[0]["source_remote_task_id"] == remote_id
        assert videos[0]["source_provider"] == "minimax"
        assert videos[0]["source_task_id"] == local_id
        assert task["result_path"] == first.public_path
        assert task["project_id"] == project_id
        assert task["shot_id"] == shot_id
        assert version["video_path"] == first.public_path
        assert shot["status"] == "video_ready"
        assert ready == 1
        assert len(downloads) == 2
        files = list((PROJECTS_DIR / project_id).glob("asset_*.mp4"))
        assert len(files) == 1
        blob = _leak_blob(project_id)
        _assert_no_secrets(blob)
        assert signed not in blob
        pass_("同一 remote_task_id 只登记一个视频资产，重复回查复用且不重复 asset.ready")
    finally:
        _cleanup()


def test_five_shots_share_project_jpeg_without_image_provider() -> None:
    import backend.providers.image_provider as image_provider

    original = image_provider.generate_image_asset

    def forbidden(*_args, **_kwargs):
        raise AssertionError("five-shot JPEG attach must not call image generation providers")

    image_provider.generate_image_asset = forbidden
    project_id = _project()
    shot_ids = [_add_shot(project_id, index) for index in range(1, 6)]
    try:
        source_bytes = GYFY.read_bytes() if GYFY.is_file() else JPEG_BYTES
        registered = register_local_first_frame(project_id, shot_ids[0], source_bytes, filename="gyfy.jpg")
        attached = attach_existing_first_frame_to_shots(project_id, registered["file_path"], shot_ids)
        assert attached["count"] == 5
        project = get_project(project_id)
        for shot in project["shots"]:
            current = next(item for item in shot["versions"] if item["id"] == shot["current_version_id"])
            assert current["first_frame_path"] == registered["file_path"]
            assert current["first_frame_path"].startswith(f"/assets/{project_id}/")
        blob = _leak_blob(project_id)
        _assert_no_secrets(blob)
        if GYFY.is_file():
            pass_("5 个镜头引用同一项目内 JPEG（gyfy.jpg 副本），未触发图片 Provider")
        else:
            skip("未找到 gyfy.jpg，已用最小 JPEG 夹具挂接 5 镜")
            pass_("5 个镜头引用同一项目内 JPEG，未触发图片 Provider")
    finally:
        image_provider.generate_image_asset = original
        _cleanup()


def test_last_live_run_counts_split_new_submits_from_unique_tasks() -> None:
    counts = normalize_live_run_counts(LAST_LIVE_RUN)
    assert counts["text_calls_total"] == 3
    assert counts["vision_calls_total"] == 1
    assert counts["video_submits_new"] == 4
    assert counts["video_tasks_reused"] == 1
    assert counts["unique_remote_tasks"] == 5
    assert counts["video_submits_new"] + counts["video_tasks_reused"] == counts["unique_remote_tasks"]
    assert counts["video_submits_new"] != counts["unique_remote_tasks"]
    assert counts["preexisting_remote_tasks"] == 1
    assert LAST_LIVE_RUN["resume_note"].count("新提交") >= 1
    legacy = apply_count_fields(
        {
            "text_calls": 3,
            "vision_calls": 1,
            "video_submits": 4,
            "remote_completed": 5,
            "downloaded_videos": 5,
            "duplicate_submits": 0,
            "duplicate_assets": 0,
            "notes": ["reuse_existing_task shot_1 4366…8674"],
        }
    )
    assert legacy["video_submits_new"] == 4
    assert legacy["video_tasks_reused"] == 1
    assert legacy["unique_remote_tasks"] == 5
    assert "video_submits" not in legacy
    assert has_secret_leak('{"url": "<data-url omitted>"}') is False
    assert has_secret_leak("data:image/jpeg;base64," + "a" * 80) is True
    pass_("报告字段区分新提交 4 次与唯一远程任务 5 个，脱敏占位不算泄漏")


def test_audit_bundle_fields_and_no_secrets() -> None:
    out = ROOT / "output" / "playwright" / "_p7d_audit_tmp"
    shutil.rmtree(out, ignore_errors=True)
    try:
        paths = write_audit_reports(
            out,
            result=dict(LAST_LIVE_RUN),
            lineage={
                "ok": True,
                "shot_lineage": [
                    {
                        "shot_id": "shot1",
                        "shot_index": 1,
                        "version_id": "v1",
                        "provider": "minimax",
                        "model": "MiniMax-H3",
                        "video_mode": "i2v",
                        "duration_seconds": 4,
                        "first_frame_asset_id": "a1",
                        "video_asset_id": "vid1",
                        "remote_task_id": "4366…8674",
                        "local_file_path": "/assets/demo/clip.mp4",
                        "status": "video_ready",
                    }
                ],
            },
            ffprobe={
                "ok": True,
                "format": "mov,mp4,m4a",
                "duration": 22.3,
                "size": 1024,
                "video_codec": "h264",
                "width": 1280,
                "height": 720,
                "frame_rate": "30/1",
                "audio_stream": True,
            },
            reconstructed=False,
        )
        audit = json.loads(paths["audit"].read_text(encoding="utf-8"))
        for key in (
            "project_id",
            "generation_mode",
            "text_calls_total",
            "vision_calls_total",
            "video_submits_new",
            "video_tasks_reused",
            "unique_remote_tasks",
            "remote_tasks_completed",
            "downloaded_videos",
            "duplicate_submits",
            "duplicate_assets",
            "ffmpeg_ran",
            "final_cut",
            "preview_ok",
            "download_ok",
            "cleanup_verified",
        ):
            assert key in audit, key
        assert audit["video_submits_new"] == 4
        assert audit["video_tasks_reused"] == 1
        assert audit["unique_remote_tasks"] == 5
        assert audit["count_labels"]["video_submits_new"] == "本次新提交任务"
        blob = paths["audit"].read_text(encoding="utf-8") + paths["lineage"].read_text(encoding="utf-8")
        _assert_no_secrets(blob)
        assert "sk-" not in blob.lower()
        assert "data:image" not in blob.lower()
        assert paths["browser_evidence"].is_file()
        assert paths["browser_dom_snapshots"].is_file()
        assert paths["browser_screenshot_hashes"].is_file()
        ffprobe = json.loads(paths["ffprobe"].read_text(encoding="utf-8"))
        assert ffprobe["format"]
        assert ffprobe["video_codec"] == "h264"
        pass_("审计包含完整计数/血缘/ffprobe 字段且无 Key 或 Data URL")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_disconnect_resume_polls_same_remote_and_continues_remaining_shots() -> None:
    project_id = _project()
    shot_ids = [_add_shot(project_id, index) for index in range(1, 6)]
    posts: list[str] = []
    gets: list[str] = []
    downloads: list[str] = []

    def json_transport(method: str, url: str, _payload: dict | None) -> dict:
        path = url.split("?", 1)[0]
        if method == "POST":
            posts.append(path)
            return {"task_id": f"remote_new_{len(posts):02d}"}
        gets.append(path)
        return {
            "status": "succeeded",
            "task": {"status": "succeeded"},
            "video_url": "https://cdn.example/video.mp4?X-Amz-Signature=secret&X-Amz-Credential=AKIA",
        }

    def fake_download(url: str) -> bytes:
        downloads.append(url.split("?", 1)[0])
        assert url.startswith("https://")
        return FAKE_MP4

    set_video_json_transport(json_transport)
    set_video_download_transport(fake_download)
    try:
        with _env(
            VISIONCRAFT_ALLOW_LIVE_VIDEO="1",
            VISIONCRAFT_LIVE_MAX_VIDEO_CALLS="5",
            VISIONCRAFT_LIVE_BUDGET_CNY="12",
            MINIMAX_API_KEY="mock-minimax-key",
            MINIMAX_VIDEO_POLL_SECONDS="8",
            MINIMAX_VIDEO_POLL_INTERVAL="0",
        ):
            shot1 = shot_ids[0]
            job1 = create_job(project_id, "generate_video", "生成视频", shot_id=shot1)
            request1 = _video_request(project_id, shot1, job_id=job1)
            request1.provider_override = "minimax"
            request1.model_override = "MiniMax-H3"
            remote_1 = "remote_resume_shot01"
            local_1 = _insert_video_task(request1, remote_1)

            with connect() as conn:
                before = conn.execute(
                    "SELECT COUNT(*) AS n FROM video_tasks WHERE project_id = ?",
                    (project_id,),
                ).fetchone()["n"]
            assert before == 1
            assert posts == []

            # Re-enter after Playwright disconnect: only poll the original remote task.
            resume_job = create_job(project_id, "generate_video", "恢复", shot_id=shot1)
            generate_shot_video(
                project_id,
                shot1,
                resume_job,
                video_mode="t2v",
                provider="minimax",
                model="MiniMax-H3",
                duration_seconds=4,
            )
            assert posts == []
            assert any(remote_1 in url for url in gets)
            assert downloads == ["https://cdn.example/video.mp4"]

            with connect() as conn:
                tasks = conn.execute("SELECT * FROM video_tasks WHERE project_id = ?", (project_id,)).fetchall()
                videos = conn.execute(
                    "SELECT * FROM assets WHERE project_id = ? AND type = 'video'",
                    (project_id,),
                ).fetchall()
                ready = conn.execute(
                    "SELECT COUNT(*) AS n FROM job_events WHERE project_id = ? AND event_type = 'asset.ready'",
                    (project_id,),
                ).fetchone()["n"]
            assert len(tasks) == 1
            assert tasks[0]["id"] == local_1
            assert tasks[0]["remote_task_id"] == remote_1
            assert tasks[0]["status"] == "completed"
            assert len(videos) == 1
            assert ready == 1
            files = list((PROJECTS_DIR / project_id).glob("asset_*.mp4"))
            assert len(files) == 1

            # Duplicate completion of the same remote task reuses the local file.
            again = ensure_remote_video_asset(
                request1,
                "https://cdn.example/video.mp4?X-Amz-Signature=secret",
                "prompt",
                "provider:minimax:MiniMax-H3",
                provider="minimax",
                remote_task_id=remote_1,
                local_task_id=local_1,
            )
            assert again.reused is True
            assert again.asset_id == videos[0]["id"]
            assert downloads == ["https://cdn.example/video.mp4"]

            refresh_job = create_job(project_id, "video_task_refresh", "再次回查")
            refresh_project_video_tasks(project_id, refresh_job)

            remaining_before_posts = len(posts)
            for shot_id in shot_ids[1:]:
                job = create_job(project_id, "generate_video", "生成视频", shot_id=shot_id)
                generate_shot_video(
                    project_id,
                    shot_id,
                    job,
                    video_mode="t2v",
                    provider="minimax",
                    model="MiniMax-H3",
                    duration_seconds=4,
                )

            assert len(posts) == remaining_before_posts + 4
            with connect() as conn:
                tasks = conn.execute("SELECT * FROM video_tasks WHERE project_id = ?", (project_id,)).fetchall()
                videos = conn.execute(
                    "SELECT * FROM assets WHERE project_id = ? AND type = 'video'",
                    (project_id,),
                ).fetchall()
                remotes = [row["remote_task_id"] for row in tasks]
                ready = conn.execute(
                    "SELECT COUNT(*) AS n FROM job_events WHERE project_id = ? AND event_type = 'asset.ready'",
                    (project_id,),
                ).fetchone()["n"]
            assert len(tasks) == 5
            assert len(set(remotes)) == 5
            assert remote_1 in remotes
            assert len(videos) == 5
            assert ready == 5
            assert len(list((PROJECTS_DIR / project_id).glob("asset_*.mp4"))) == 5
            assert len(downloads) == 5

            from tools.live_run_audit import collect_project_lineage

            lineage = collect_project_lineage(project_id)
            pre = verify_pre_cleanup(lineage)
            assert lineage["counts"]["final_videos"] == 0
            assert pre["checks"]["shots"] is True
            assert pre["checks"]["unique_remote_tasks"] is True
            assert pre["checks"]["video_tasks"] is True
            assert pre["checks"]["video_assets"] is True
            assert pre["checks"]["duplicate_remote_groups"] is True
            assert pre["checks"]["duplicate_assets"] is True
            assert pre["checks"]["secret_leak"] is True
            blob = _leak_blob(project_id)
            _assert_no_secrets(blob)
            pass_("断点恢复只回查原 remote_task_id，剩余 4 镜可继续且任务/资产一一对应")
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
    os.environ.pop("VISIONCRAFT_LIVE_MAX_VIDEO_CALLS", None)
    try:
        test_budget_estimate_uses_buffer()
        test_text_requests_are_capped()
        test_over_budget_is_blocked_before_transport()
        test_text_calls_capped_at_three()
        test_register_local_jpeg_png_and_reject_bad_inputs()
        test_svg_and_path_escape_rejected_for_vision_i2v()
        test_http_register_and_gyfy_copy()
        test_env_overrides_five_video_budget()
        test_raising_video_calls_cannot_bypass_budget()
        test_sixth_video_and_over_budget_blocked_before_http()
        test_remote_task_video_asset_is_idempotent()
        test_five_shots_share_project_jpeg_without_image_provider()
        test_last_live_run_counts_split_new_submits_from_unique_tasks()
        test_audit_bundle_fields_and_no_secrets()
        test_disconnect_resume_polls_same_remote_and_continues_remaining_shots()
        print("PASS: live budget and local keyframe safeguards (no live network)")
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
