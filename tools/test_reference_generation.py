"""No-cost tests for reference-image generation (slice 3).

Before this slice the reference images were stored and never read. Generation
only ever looked at first_frame_path / last_frame_path, so neither
characters.asset_id nor shot_versions.reference_frame_path reached a provider
payload. These cases pin the new contract:

1. a `reference` video mode exists and is registered, not implied;
2. every provider states honestly whether it can take reference images;
3. the payloads actually carry them, and honour each provider's own rules
   (Ark is mutually exclusive with first/last frame, DashScope r2v is not);
4. an unsupported provider fails loudly instead of quietly dropping the image.
"""
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
from backend.providers.video_provider import (
    VideoAssetRequest,
    _ark_content_items,
    _dashscope_media_items,
    _minimax_content_items,
)
from backend.services.anchor_service import collect_reference_images
from backend.services.asset_service import persist_binary_asset
from backend.services.media_transfer_service import MediaTransferError

# Valid 1x1 PNG. These cases check the transport contract, not image aesthetics.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)
PLACEHOLDER = "/assets/placeholder/frame.png"


def expect_capability_error(code: str, **kwargs) -> None:
    try:
        validate_video_generation(**kwargs)
    except CapabilityError as exc:
        assert exc.code == code, f"expected {code}, got {exc.code}: {exc}"
        return
    raise AssertionError(f"expected CapabilityError {code}")


def test_mode_is_registered() -> None:
    payload = get_provider_capabilities()
    requirements = payload["mode_requirements"]
    assert "reference" in requirements, "reference 模式没有登记，等于没有这个模式"
    assert requirements["reference"]["requires_reference"] is True, "reference 模式必须要求参考图"
    providers = {item["id"]: item for item in payload["video"]}
    assert "ark" not in providers, "ark 已退役（2026-09-29），不该再出现在能力表里"
    assert "reference" in providers["dashscope"]["supported_modes"], "wan2.7-r2v 支持参考图"
    assert "reference" not in providers["minimax"]["supported_modes"], "MiniMax 没有参考图参数，不能假装有"
    print("PASS: reference 模式已登记；dashscope 声明支持、minimax 诚实声明不支持")


def test_provider_routing() -> None:
    expect_capability_error(
        "UNSUPPORTED_MODE_FOR_MODEL",
        provider="minimax",
        model=None,
        video_mode="reference",
        duration_seconds=6,
        aspect_ratio="16:9",
        first_frame_path=None,
        last_frame_path=None,
        reference_paths=[PLACEHOLDER],
    )
    print("PASS: provider 不支持参考图时明确拒绝，而不是静默丢弃图片")

    expect_capability_error(
        "MISSING_REFERENCE_IMAGE",
        provider="dashscope",
        model=None,
        video_mode="reference",
        duration_seconds=5,
        aspect_ratio="16:9",
        first_frame_path=None,
        last_frame_path=None,
        reference_paths=[],
    )
    print("PASS: 参考图模式缺图时报 MISSING_REFERENCE_IMAGE")

    # 原先这里断言"ark 的 plan 声明参考图不与首帧并存"。ark 退役后，能力表里已没有
    # `reference_includes_first_frame=False` 的 provider（dashscope r2v 允许并存），
    # 所以改成钉**退役必须被拦住**：给一个已退役的名字却报"未知 provider"会误导，
    # 而适配器那条互斥规则仍有 test_payloads 直接钉着（退役的是投放面，不是这条规则）。
    expect_capability_error(
        "PROVIDER_RETIRED",
        provider="ark",
        model=None,
        video_mode="reference",
        duration_seconds=5,
        aspect_ratio="16:9",
        first_frame_path=None,
        last_frame_path=None,
        reference_paths=[PLACEHOLDER],
    )
    print("PASS: 退役的 ark 在参考图模式下被明确拦下（PROVIDER_RETIRED，而非未知 provider）")

    dashscope = validate_video_generation(
        provider="dashscope",
        model=None,
        video_mode="reference",
        duration_seconds=5,
        aspect_ratio="16:9",
        first_frame_path=None,
        last_frame_path=None,
        reference_paths=[PLACEHOLDER],
    )
    assert dashscope["model"] == "wan2.7-r2v", f"参考图模式应解析到 r2v，实际 {dashscope['model']}"
    assert dashscope["reference_includes_first_frame"] is True, "wan2.7-r2v 允许首帧与参考图并存"
    print("PASS: dashscope 参考图模式解析到 wan2.7-r2v 且允许首帧并存")


def test_payloads(project_id: str, frame_path: str, anchor_path: str) -> None:
    request = VideoAssetRequest(
        project_id=project_id,
        shot_id="shot_ref",
        version_id="version_ref",
        title="reference payload",
        description="reference payload",
        prompt="a shot that used to ignore its anchors",
        first_frame_path=frame_path,
        duration_seconds=5,
        video_mode="reference",
        reference_images=[{"kind": "character", "label": "小明", "file_path": anchor_path}],
    )

    # ark 适配器已退役但**刻意保留**（见 capabilities.RETIRED_VIDEO_PROVIDERS）：这条单测
    # 钉的是适配器自己的互斥规则，删了它规则就无人看管。退役的是投放面，不是这条规则。
    ark = _ark_content_items(request, "prompt")
    roles = [item.get("role") for item in ark]
    assert "reference_image" in roles, "Seedance 的参考图必须带 role=reference_image"
    assert "first_frame" not in roles, "Seedance 参考图与首帧互斥，两个都发会被云端拒绝"
    print("PASS: ark payload 带 reference_image 且不发首帧")

    dashscope = _dashscope_media_items(request, "wan2.7-r2v")
    types = [item.get("type") for item in dashscope]
    assert "reference_image" in types, "DashScope 的参考图必须带 type=reference_image"
    assert "first_frame" in types, "wan2.7-r2v 允许首帧与参考图并存，首帧不该被丢掉"
    print("PASS: dashscope payload 同时带 reference_image 与 first_frame")

    try:
        _minimax_content_items(request, "MiniMax-H3", "prompt")
    except MediaTransferError as exc:
        assert exc.code == "REFERENCE_NOT_SUPPORTED", f"expected REFERENCE_NOT_SUPPORTED, got {exc.code}"
        print("PASS: minimax payload 拒绝参考图而不是吞掉它")
    else:
        raise AssertionError("MiniMax 参考文献里没有参考图参数，这里不该悄悄成功")


def test_collect_reference_images(project_id: str, character_asset_id: str, scene_asset_id: str) -> None:
    assert collect_reference_images(project_id, shot_reference_path=None) == [], "没有任何参考图时应该返回空"

    now = utc_now()
    character_id = f"char_{uuid.uuid4().hex[:8]}"
    scene_id = f"scene_{uuid.uuid4().hex[:8]}"
    with connect() as conn:
        conn.execute(
            "INSERT INTO characters (id, project_id, name, role, description, visual_prompt, asset_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (character_id, project_id, "小明", "protagonist", "主体", "小明的外观", character_asset_id, now),
        )
        conn.execute(
            "INSERT INTO scenes (id, project_id, name, description, visual_prompt, asset_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (scene_id, project_id, "教室", "白天教室", "教室氛围", scene_asset_id, now),
        )

    shot_reference = collect_reference_images(project_id, shot_reference_path="/assets/placeholder/shot.png")
    kinds = [item["kind"] for item in shot_reference]
    assert kinds == ["shot_reference", "character", "scene"], f"顺序应为镜头参考图 → 角色 → 场景，实际 {kinds}"
    assert shot_reference[1]["label"] == "小明"
    print("PASS: 收集顺序为镜头参考图 → 角色锚点 → 场景锚点")

    only_anchors = collect_reference_images(project_id, shot_reference_path=None)
    assert [item["kind"] for item in only_anchors] == ["character", "scene"], "没有镜头参考图时只回锚点"
    print("PASS: 镜头没有参考图时只收集锚点")


def main() -> None:
    init_environment()
    init_db()
    project_id = f"refgen_test_{uuid.uuid4().hex[:10]}"
    with connect() as conn:
        now = utc_now()
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode, status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, "Reference generation test", "Test source", "test", "16:9", 5, "auto", "testing", "direct", now, now),
        )
    try:
        frame_asset = persist_binary_asset(
            project_id, "first-frame", "首帧", "一像素首帧", "frame prompt",
            PNG_BYTES, ".png", "test", "local-fixture",
        )
        anchor_asset = persist_binary_asset(
            project_id, "character-anchor", "角色锚点", "一像素锚点", "anchor prompt",
            PNG_BYTES, ".png", "test", "local-fixture",
        )
        scene_asset = persist_binary_asset(
            project_id, "scene-anchor", "场景锚点", "一像素场景", "scene prompt",
            PNG_BYTES, ".png", "test", "local-fixture",
        )
        with connect() as conn:
            paths = {
                row["id"]: row["file_path"]
                for row in conn.execute("SELECT id, file_path FROM assets WHERE project_id = ?", (project_id,)).fetchall()
            }

        test_mode_is_registered()
        test_provider_routing()
        test_payloads(project_id, paths[frame_asset], paths[anchor_asset])
        test_collect_reference_images(project_id, anchor_asset, scene_asset)
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)


if __name__ == "__main__":
    main()
