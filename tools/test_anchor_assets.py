"""无费用测试：角色/场景视觉锚点（挂载、替换、卸载、与镜头参考图的边界）。

零外发：全程只用本地 PNG 与本地数据库，断言 `live_*_call_count` 保持为 0。
"""
from __future__ import annotations

import os
import shutil
import struct
import sys
import uuid
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db, utc_now
from backend.services.adaptation_service import (
    confirm_bible,
    confirm_scope,
    confirm_storyboard,
    list_adaptation_options,
    regenerate_stage,
    select_adaptation_option,
    start_adaptation_workflow,
)
from backend.services.anchor_service import (
    AnchorError,
    attach_anchor,
    detach_anchor,
    list_anchors,
)
from backend.services.asset_upload_service import AssetUploadError, upload_project_asset
from backend.services.project_service import get_project

SAMPLE = (
    "方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。"
    "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。"
    "但是族中长老已经设下阻碍，他只能选择冒险一搏。"
    "最终他停在山门前，留下未说完的话。"
)


def _png(width: int = 4, height: int = 4) -> bytes:
    """最简合法 PNG，避免为测试引入图像库。"""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x20\x60\xa0" * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _cleanup(project_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    target = PROJECTS_DIR / project_id
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def _project(title: str = "锚点测试") -> str:
    init_environment()
    init_db()
    project_id = f"anchor_test_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode, status, routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, SAMPLE, "cinematic clean realism", "16:9", 5, "auto", "created", "direct", now, now),
        )
    return project_id


def _characters(project_id: str) -> list:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM characters WHERE project_id = ? ORDER BY created_at", (project_id,)
        ).fetchall()


def _scenes(project_id: str) -> list:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM scenes WHERE project_id = ? ORDER BY created_at", (project_id,)
        ).fetchall()


def _bible_reviewed(project_id: str) -> None:
    """走到 Bible 已确认：这一步才会把 Bible 卡片同步进 characters/scenes 表。"""
    options = list_adaptation_options(project_id)
    if not options:
        start_adaptation_workflow(project_id)
        options = list_adaptation_options(project_id)
    assert options, "应该先有改编方案"
    select_adaptation_option(project_id, options[0]["id"])
    confirm_scope(project_id, options[0]["id"])
    confirm_bible(project_id)


def _attach(project_id: str, role: str, name: str, filename: str = "anchor.png") -> str:
    return upload_project_asset(
        project_id, asset_role=role, content=_png(), filename=filename, anchor_name=name
    )["asset"]["id"]


def test_attach_by_name_sets_fk_and_lists_path() -> None:
    project_id = _project()
    try:
        _bible_reviewed(project_id)
        characters = _characters(project_id)
        assert characters, "Bible 确认后应同步出角色行"
        name = characters[0]["name"]
        assert not characters[0]["asset_id"], "初始状态不应有锚点"

        asset_id = _attach(project_id, "character_anchor", name)

        assert _characters(project_id)[0]["asset_id"] == asset_id, "挂载后 characters.asset_id 应指向该素材"
        mine = [item for item in list_anchors(project_id) if item["kind"] == "character" and item["name"] == name]
        assert mine and mine[0]["asset_id"] == asset_id, "list_anchors 应返回该角色"
        assert (mine[0]["file_path"] or "").startswith("/assets/"), "应返回可预览路径"
        print("PASS: 上传锚点图片即写入 characters.asset_id，并可列路径")
    finally:
        _cleanup(project_id)


def test_attach_replaces_previous_anchor() -> None:
    project_id = _project()
    try:
        _bible_reviewed(project_id)
        name = _characters(project_id)[0]["name"]
        first = _attach(project_id, "character_anchor", name, "a.png")
        second = _attach(project_id, "character_anchor", name, "b.png")
        assert first != second
        assert _characters(project_id)[0]["asset_id"] == second, "重复挂载应替换为新素材"
        print("PASS: 重复挂载同一角色替换为新锚点，不留双份引用")
    finally:
        _cleanup(project_id)


def test_scene_anchor_uses_its_own_table() -> None:
    project_id = _project()
    try:
        _bible_reviewed(project_id)
        scenes = _scenes(project_id)
        assert scenes, "Bible 确认后应同步出场景行"
        _attach(project_id, "scene_anchor", scenes[0]["name"], "scene.png")
        after = _scenes(project_id)[0]
        assert after["asset_id"], "场景锚点应写入 scenes.asset_id"
        assert all(not row["asset_id"] for row in _characters(project_id)), "场景锚点不得串写到角色表"
        print("PASS: 场景锚点写入 scenes 表，不串到 characters")
    finally:
        _cleanup(project_id)


def test_unknown_and_foreign_targets_rejected() -> None:
    project_id = _project()
    other_id = _project("锚点测试-另一个项目")
    try:
        _bible_reviewed(project_id)
        _bible_reviewed(other_id)
        name = _characters(project_id)[0]["name"]
        asset_id = _attach(project_id, "character_anchor", name)
        foreign_row = _characters(other_id)[0]

        try:
            attach_anchor(project_id, kind="character", target="不存在的角色", asset_id=asset_id)
        except AnchorError as exc:
            assert exc.code == "TARGET_NOT_FOUND", exc.code
        else:
            raise AssertionError("不存在的角色应被拒绝")

        try:
            attach_anchor(project_id, kind="character", target=foreign_row["id"], asset_id=asset_id)
        except AnchorError as exc:
            assert exc.code == "TARGET_MISMATCH", exc.code
        else:
            raise AssertionError("跨项目角色应被拒绝")

        try:
            attach_anchor(project_id, kind="soundtrack", target=name, asset_id=asset_id)
        except AnchorError as exc:
            assert exc.code == "INVALID_KIND", exc.code
        else:
            raise AssertionError("非法锚点类型应被拒绝")

        assert _characters(project_id)[0]["asset_id"] == asset_id, "被拒绝的调用不得改动原锚点"
        print("PASS: 未知角色、跨项目角色、非法类型都被明确拒绝且原锚点不变")
    finally:
        _cleanup(project_id)
        _cleanup(other_id)


def test_foreign_asset_and_non_image_asset_rejected() -> None:
    project_id = _project()
    other_id = _project("锚点测试-资产来源")
    try:
        _bible_reviewed(project_id)
        _bible_reviewed(other_id)
        name = _characters(project_id)[0]["name"]
        own_asset = _attach(project_id, "character_anchor", name)
        foreign_asset = _attach(other_id, "character_anchor", _characters(other_id)[0]["name"], "b.png")

        try:
            attach_anchor(project_id, kind="character", target=name, asset_id=foreign_asset)
        except AnchorError as exc:
            assert exc.code == "ASSET_MISMATCH", exc.code
        else:
            raise AssertionError("跨项目素材应被拒绝")

        with connect() as conn:
            conn.execute(
                """INSERT INTO assets (id, project_id, type, name, description, prompt, file_path, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("asset_fakevideo", project_id, "video", "假视频", "", "", "/assets/x/y.mp4", utc_now()),
            )
        try:
            attach_anchor(project_id, kind="character", target=name, asset_id="asset_fakevideo")
        except AnchorError as exc:
            assert exc.code == "ASSET_NOT_IMAGE", exc.code
        else:
            raise AssertionError("非图片素材应被拒绝")

        assert _characters(project_id)[0]["asset_id"] == own_asset, "被拒绝的挂载不得改动已有锚点"
        print("PASS: 跨项目素材与非图片素材都被拒绝，且原锚点未被改动")
    finally:
        _cleanup(project_id)
        _cleanup(other_id)


def test_detach_keeps_asset_row() -> None:
    project_id = _project()
    try:
        _bible_reviewed(project_id)
        name = _characters(project_id)[0]["name"]
        asset_id = _attach(project_id, "character_anchor", name)
        result = detach_anchor(project_id, kind="character", target=name)
        assert result["asset_id"] is None
        assert result["previous_asset_id"] == asset_id
        assert not _characters(project_id)[0]["asset_id"], "卸载后外键应为空"
        with connect() as conn:
            still = conn.execute("SELECT id FROM assets WHERE id = ?", (asset_id,)).fetchone()
        assert still, "卸载锚点不得删除素材记录"
        print("PASS: 卸载锚点只清外键，素材记录保留")
    finally:
        _cleanup(project_id)


def test_anchor_survives_bible_regeneration() -> None:
    """Bible 重生成会重跑卡片同步；锚点必须活下来，否则用户每改一次文案就白挂一次。"""
    project_id = _project()
    try:
        _bible_reviewed(project_id)
        name = _characters(project_id)[0]["name"]
        asset_id = _attach(project_id, "character_anchor", name)
        before = len(_characters(project_id))

        regenerate_stage(project_id, "bible")

        characters = _characters(project_id)
        assert len(characters) == before, f"重生成不应复制出重复角色行（{before} -> {len(characters)}）"
        hit = [row for row in characters if row["name"] == name]
        assert hit and hit[0]["asset_id"] == asset_id, "Bible 重生成后锚点必须保留"
        print("PASS: Bible 重生成后角色锚点仍在（不重复建行、不丢外键）")
    finally:
        _cleanup(project_id)


def test_shot_reference_image_does_not_touch_anchor() -> None:
    """镜头参考图与角色锚点是两条链路：挂参考图不应顺带写角色表，反之亦然。"""
    project_id = _project()
    try:
        _bible_reviewed(project_id)
        confirm_storyboard(project_id)
        shots = get_project(project_id).get("shots") or []
        assert shots, "确认分镜后应有制作镜头"

        upload_project_asset(
            project_id, asset_role="reference_image", content=_png(), filename="ref.png", shot_id=shots[0]["id"]
        )
        assert all(not row["asset_id"] for row in _characters(project_id)), "挂镜头参考图不得写入角色锚点"

        name = _characters(project_id)[0]["name"]
        _attach(project_id, "character_anchor", name)

        with connect() as conn:
            draft = conn.execute(
                "SELECT * FROM shot_drafts WHERE project_id = ? AND shot_id = ?", (project_id, shots[0]["id"])
            ).fetchone()
        assert draft is None or not (draft["reference_frame_path"] or "").endswith("anchor.png"), (
            "挂角色锚点不得改写镜头参考图"
        )
        assert get_project(project_id)["status"] == "production_ready", "锚点操作不应改变项目状态机"
        print("PASS: 镜头参考图与角色锚点互不串写，且锚点操作不推进状态机")
    finally:
        _cleanup(project_id)


def test_upload_validation_errors() -> None:
    project_id = _project()
    try:
        _bible_reviewed(project_id)
        name = _characters(project_id)[0]["name"]

        try:
            upload_project_asset(project_id, asset_role="character_anchor", content=_png(), filename="a.png")
        except AssetUploadError as exc:
            assert exc.code == "ANCHOR_TARGET_REQUIRED", exc.code
        else:
            raise AssertionError("缺 anchor_name 应被拒绝")

        confirm_storyboard(project_id)
        shot_id = (get_project(project_id).get("shots") or [])[0]["id"]
        try:
            upload_project_asset(
                project_id,
                asset_role="character_anchor",
                content=_png(),
                filename="a.png",
                anchor_name=name,
                shot_id=shot_id,
            )
        except AssetUploadError as exc:
            assert exc.code == "INVALID_ROLE", exc.code
        else:
            raise AssertionError("锚点挂到镜头应被拒绝")
        print("PASS: 缺目标、把锚点挂到镜头两种误用都被拒绝")
    finally:
        _cleanup(project_id)


def test_upload_failure_leaves_no_orphan_asset() -> None:
    """锚点挂不上时整批回滚：不留一张没人引用的孤儿素材。"""
    project_id = _project()
    try:
        _bible_reviewed(project_id)
        before = len(list_anchors(project_id))
        with connect() as conn:
            before_assets = conn.execute(
                "SELECT COUNT(*) AS n FROM assets WHERE project_id = ?", (project_id,)
            ).fetchone()["n"]

        try:
            upload_project_asset(
                project_id,
                asset_role="character_anchor",
                content=_png(),
                filename="orphan.png",
                anchor_name="并不存在的角色",
            )
        except AssetUploadError as exc:
            assert exc.code == "TARGET_NOT_FOUND", exc.code
        else:
            raise AssertionError("挂到不存在的角色应失败")

        with connect() as conn:
            after_assets = conn.execute(
                "SELECT COUNT(*) AS n FROM assets WHERE project_id = ?", (project_id,)
            ).fetchone()["n"]
        assert after_assets == before_assets, f"失败后素材数应回到 {before_assets}，实际 {after_assets}"
        assert len(list_anchors(project_id)) == before
        print("PASS: 挂载失败整批回滚，不产生孤儿素材")
    finally:
        _cleanup(project_id)


def test_http_anchor_endpoints() -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    init_environment()
    client = TestClient(app)
    created = client.post(
        "/api/projects",
        json={"title": "锚点 HTTP 测试", "source_text": SAMPLE, "duration_seconds": 5, "shot_count_mode": "auto"},
    )
    assert created.status_code == 200, created.text
    project_id = created.json()["id"]
    try:
        client.post(f"/api/projects/{project_id}/run")
        _bible_reviewed(project_id)
        name = _characters(project_id)[0]["name"]

        uploaded = client.post(
            f"/api/projects/{project_id}/assets/upload",
            data={"asset_role": "character_anchor", "anchor_name": name},
            files={"file": ("anchor.png", _png(), "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        anchor = uploaded.json()["anchor"]
        assert anchor and anchor["asset_id"], "上传响应应带回锚点信息"

        listed = client.get(f"/api/projects/{project_id}/anchors")
        assert listed.status_code == 200, listed.text
        assert any(item["asset_id"] == anchor["asset_id"] for item in listed.json()["items"])

        removed = client.delete(f"/api/projects/{project_id}/anchors/character/{name}")
        assert removed.status_code == 200, removed.text
        assert removed.json()["anchor"]["asset_id"] is None
        assert not _characters(project_id)[0]["asset_id"]

        bad = client.post(
            f"/api/projects/{project_id}/anchors",
            json={"kind": "character", "target": name, "asset_id": "asset_nope"},
        )
        assert bad.status_code == 404, bad.text
        print("PASS: HTTP 上传挂载→列出→卸载→错误码 404 全链路")
    finally:
        _cleanup(project_id)


def test_no_live_calls_made() -> None:
    project_id = _project("锚点测试-零外发")
    try:
        _bible_reviewed(project_id)
        name = _characters(project_id)[0]["name"]
        _attach(project_id, "character_anchor", name)
        detach_anchor(project_id, kind="character", target=name)
        project = get_project(project_id)
        counts = (
            project.get("live_text_call_count", 0),
            project.get("live_vision_call_count", 0),
            project.get("live_video_call_count", 0),
        )
        assert counts == (0, 0, 0), f"锚点链路产生了真实调用：{counts}"
        print("PASS: 锚点链路全程无真实调用（live_*_call_count 全为 0）")
    finally:
        _cleanup(project_id)


def main() -> None:
    test_attach_by_name_sets_fk_and_lists_path()
    test_attach_replaces_previous_anchor()
    test_scene_anchor_uses_its_own_table()
    test_unknown_and_foreign_targets_rejected()
    test_foreign_asset_and_non_image_asset_rejected()
    test_detach_keeps_asset_row()
    test_anchor_survives_bible_regeneration()
    test_shot_reference_image_does_not_touch_anchor()
    test_upload_validation_errors()
    test_upload_failure_leaves_no_orphan_asset()
    test_http_anchor_endpoints()
    test_no_live_calls_made()
    print("PASS: 角色/场景视觉锚点链路")


if __name__ == "__main__":
    main()
