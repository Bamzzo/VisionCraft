"""无费用测试：视觉锚点审核门（分镜确认 → 锚点门 → 批量生成）。

Phase 2 冻结的三道审核门是「改编范围/Story Bible、视觉锚点、试生成」，
但实现里只兑现了两道：`confirm_storyboard` 直接落 `production_ready`，
而 `assert_batch_generation_allowed` 是黑名单式放行 —— 确认分镜之后
批量生成立刻畅通。本文件把缺的那道门写成可执行的规格。

零外发：全程本地库 + 本地 PNG，断言 `live_*_call_count` 保持为 0。
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
    AdaptationError,
    anchor_review_readiness,
    assert_batch_generation_allowed,
    confirm_anchors,
    confirm_bible,
    confirm_scope,
    confirm_storyboard,
    list_adaptation_options,
    regenerate_stage,
    select_adaptation_option,
    start_adaptation_workflow,
)
from backend.services.asset_upload_service import upload_project_asset
from backend.services.checkpoint_service import (
    CheckpointError,
    NODE_FOR_STATUS,
    PAUSE_REASON,
    REVIEW_NODES,
    REVIEW_STATUSES,
)
from backend.services.project_service import get_project
from backend.services.workflow_control_service import pause_project, resume_project

GATE_STATUS = "awaiting_anchor_review"
GATE_NODE = "anchor_review"

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


def _project(title: str = "锚点门测试") -> str:
    init_environment()
    init_db()
    project_id = f"gategate_{uuid.uuid4().hex[:10]}"
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


def _at_gate(project_id: str) -> None:
    """把项目推进到锚点门口：Bible 已确认 + 分镜已确认。"""
    options = list_adaptation_options(project_id)
    if not options:
        start_adaptation_workflow(project_id)
        options = list_adaptation_options(project_id)
    assert options, "应该先有改编方案"
    select_adaptation_option(project_id, options[0]["id"])
    confirm_scope(project_id, options[0]["id"])
    confirm_bible(project_id)
    confirm_storyboard(project_id)
    assert get_project(project_id)["status"] == GATE_STATUS, (
        f"确认分镜后应停在锚点门，实际 {get_project(project_id)['status']}"
    )


def _attach(project_id: str, name: str, *, role: str = "character_anchor") -> str:
    return upload_project_asset(
        project_id, asset_role=role, content=_png(), filename="anchor.png", anchor_name=name
    )["asset"]["id"]


# --------------------------------------------------------------------------
# 门的核心行为
# --------------------------------------------------------------------------


def test_storyboard_confirm_enters_anchor_review() -> None:
    """分镜确认不再等于可批量生成：它应停在锚点门口，且制作镜头已经就位。"""
    project_id = _project()
    try:
        _at_gate(project_id)
        project = get_project(project_id)
        assert project["status"] == GATE_STATUS, project["status"]
        assert project.get("shots"), "进门时应已 promote 出制作镜头，门拦的是花钱不是建镜头"
        checkpoint = project.get("checkpoint") or {}
        assert checkpoint.get("node") == GATE_NODE, checkpoint
        print("PASS: 确认分镜后停在 awaiting_anchor_review，制作镜头已就位")
    finally:
        _cleanup(project_id)


def test_checkpoint_node_registered() -> None:
    """门必须是一等检查点，否则暂停/恢复与界面横幅都接不上。"""
    assert GATE_NODE in REVIEW_NODES, f"{GATE_NODE} 应在 REVIEW_NODES"
    assert GATE_STATUS in REVIEW_STATUSES, f"{GATE_STATUS} 应在 REVIEW_STATUSES"
    assert NODE_FOR_STATUS.get(GATE_STATUS) == GATE_NODE, NODE_FOR_STATUS.get(GATE_STATUS)
    assert PAUSE_REASON.get(GATE_NODE), "锚点门必须有人话的暂停理由"
    print("PASS: anchor_review 已登记为检查点节点（节点/状态/映射/暂停理由齐全）")
   


def test_batch_generation_blocked_at_gate() -> None:
    """这道门的存在意义：它必须真的拦住批量生成。"""
    project_id = _project()
    try:
        _at_gate(project_id)
        project = get_project(project_id)
        try:
            assert_batch_generation_allowed(project)
        except AdaptationError as exc:
            assert exc.code == "ANCHOR_REVIEW_PENDING", exc.code
        else:
            raise AssertionError("锚点门内不应放行批量生成")
        print("PASS: 锚点门内批量生成被拒（ANCHOR_REVIEW_PENDING）")
    finally:
        _cleanup(project_id)


def test_readiness_reports_missing_targets() -> None:
    """就绪度要能说清「还差谁」，否则界面上只是一句无用的拒绝。"""
    project_id = _project()
    try:
        _at_gate(project_id)
        readiness = anchor_review_readiness(project_id)
        assert readiness["required"] is True, readiness
        assert readiness["ready"] is False, readiness
        assert readiness["missing"], "未挂任何锚点时 missing 不应为空"
        assert readiness["attached"] == [], readiness
        assert readiness["total"] == len(readiness["missing"]), "一次都没挂时 missing 应等于全部目标"

        name = sorted({row["name"] for row in _characters(project_id)})[0]
        _attach(project_id, name)
        after = anchor_review_readiness(project_id)
        assert name in after["attached"], after
        assert name not in after["missing"], f"挂载后 {name} 不应仍在 missing：{after}"
        assert after["total"] == readiness["total"], "挂载不应改变目标总数"
        print(f"PASS: 就绪度报出待挂目标（{len(readiness['missing'])} 项），挂载后该项移出 missing")
    finally:
        _cleanup(project_id)


def test_confirm_requires_attached_anchor() -> None:
    """未挂锚点就确认必须被拒，且不能有副作用——状态停在原地。"""
    project_id = _project()
    try:
        _at_gate(project_id)
        try:
            confirm_anchors(project_id)
        except AdaptationError as exc:
            assert exc.code == "ANCHOR_NOT_ATTACHED", exc.code
        else:
            raise AssertionError("无锚点时确认应被拒绝")
        assert get_project(project_id)["status"] == GATE_STATUS, "拒绝后状态不得前进"
        try:
            assert_batch_generation_allowed(get_project(project_id))
        except AdaptationError:
            pass
        else:
            raise AssertionError("被拒之后批量生成仍应被拦")
        print("PASS: 锚点未就绪时确认被拒（ANCHOR_NOT_ATTACHED），状态与守卫都不变")
    finally:
        _cleanup(project_id)


def test_confirm_unlocks_production() -> None:
    """挂上锚点后确认：过门、进制作、批量生成放行——这是完整的一次放行。"""
    project_id = _project()
    try:
        _at_gate(project_id)
        name = _characters(project_id)[0]["name"]
        asset_id = _attach(project_id, name)

        confirm_anchors(project_id)

        project = get_project(project_id)
        assert project["status"] == "production_ready", project["status"]
        assert_batch_generation_allowed(project)
        with connect() as conn:
            row = conn.execute(
                "SELECT asset_id FROM characters WHERE project_id = ? AND name = ?", (project_id, name)
            ).fetchone()
        assert row["asset_id"] == asset_id, "过门时锚点外键必须还在"
        print("PASS: 挂锚点后确认过门，状态转 production_ready 且批量生成放行")
    finally:
        _cleanup(project_id)


def test_project_without_targets_passes_directly() -> None:
    """空镜项目没有可挂锚点的对象，不该被门锁死。"""
    project_id = _project()
    try:
        _at_gate(project_id)
        with connect() as conn:
            conn.execute("DELETE FROM characters WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM scenes WHERE project_id = ?", (project_id,))

        readiness = anchor_review_readiness(project_id)
        assert readiness["required"] is False, f"无角色/场景时不应要求锚点：{readiness}"

        confirm_anchors(project_id)
        assert get_project(project_id)["status"] == "production_ready"
        print("PASS: 无角色/场景的项目无需锚点即可过门")
    finally:
        _cleanup(project_id)


def test_explicit_skip_allows_passage() -> None:
    """允许显式跳过，但必须是调用方主动选择，而不是默认漏过去。"""
    project_id = _project()
    try:
        _at_gate(project_id)
        assert anchor_review_readiness(project_id)["ready"] is False
        confirm_anchors(project_id, allow_without_anchors=True)
        assert get_project(project_id)["status"] == "production_ready"
        print("PASS: 显式跳过（allow_without_anchors）才放行，默认路径仍然拦住")
    finally:
        _cleanup(project_id)


# --------------------------------------------------------------------------
# 幂等与边界：门内重复操作不能把状态机推乱
# --------------------------------------------------------------------------


def test_confirm_storyboard_idempotent_inside_gate() -> None:
    """在门口再确认一次分镜，不得重复创建制作镜头，也不得越过门。"""
    project_id = _project()
    try:
        _at_gate(project_id)
        before = len(get_project(project_id).get("shots") or [])
        with connect() as conn:
            versions_before = conn.execute(
                """SELECT COUNT(*) AS n FROM shot_versions
                   WHERE shot_id IN (SELECT id FROM shots WHERE project_id = ?)""",
                (project_id,),
            ).fetchone()["n"]

        confirm_storyboard(project_id)

        project = get_project(project_id)
        assert project["status"] == GATE_STATUS, f"门内重复确认不应越过门：{project['status']}"
        assert len(project.get("shots") or []) == before, "不应重复创建制作镜头"
        with connect() as conn:
            versions_after = conn.execute(
                """SELECT COUNT(*) AS n FROM shot_versions
                   WHERE shot_id IN (SELECT id FROM shots WHERE project_id = ?)""",
                (project_id,),
            ).fetchone()["n"]
        assert versions_after == versions_before, f"不应重复创建镜头版本：{versions_before} -> {versions_after}"
        print("PASS: 门内重复确认分镜是幂等的（不越门、不重复建镜头与版本）")
    finally:
        _cleanup(project_id)


def test_confirm_anchors_idempotent_after_pass() -> None:
    """过门后再确认一次，不得倒退，也不得把已确认状态弄脏。"""
    project_id = _project()
    try:
        _at_gate(project_id)
        name = _characters(project_id)[0]["name"]
        _attach(project_id, name)
        confirm_anchors(project_id)

        confirm_anchors(project_id)

        assert get_project(project_id)["status"] == "production_ready"
        assert_batch_generation_allowed(get_project(project_id))
        print("PASS: 过门后重复确认是幂等的（不倒退、守卫仍放行）")
    finally:
        _cleanup(project_id)


def test_anchor_attached_after_pass_does_not_reopen_gate() -> None:
    """过门之后再改锚点是正常的迭代，不该把项目退回门口。"""
    project_id = _project()
    try:
        _at_gate(project_id)
        characters = _characters(project_id)
        _attach(project_id, characters[0]["name"])
        confirm_anchors(project_id)
        assert get_project(project_id)["status"] == "production_ready"

        if len(characters) > 1:
            _attach(project_id, characters[1]["name"])

        assert get_project(project_id)["status"] == "production_ready", "已过门后改锚点不应退回门口"
        print("PASS: 过门后补挂锚点不重开门（门只管放行那一刻）")
    finally:
        _cleanup(project_id)


def test_pause_resume_at_anchor_review() -> None:
    """门口应可暂停；恢复语义与其余审核节点一致——确认并继续，锚点没挂就报错。"""
    project_id = _project()
    try:
        _at_gate(project_id)
        paused = pause_project(project_id)
        assert paused.get("checkpoint", {}).get("node") == GATE_NODE, paused

        try:
            resume_project(project_id)
        except CheckpointError as exc:
            assert exc.code == "ANCHOR_NOT_ATTACHED", exc.code
        else:
            raise AssertionError("锚点未挂时恢复应被拒绝，而不是悄悄放行")
        assert get_project(project_id)["status"] == GATE_STATUS, "恢复失败后仍应在门口"

        name = _characters(project_id)[0]["name"]
        _attach(project_id, name)
        resumed = resume_project(project_id)
        assert resumed.get("status") == "production_ready", resumed
        print("PASS: 锚点门可暂停；恢复语义与其余审核节点一致（未挂则拒、已挂则过）")
    finally:
        _cleanup(project_id)


def test_regenerate_storyboard_reenters_gate() -> None:
    """重做分镜后必须重新过门——门是「每一次批量生成前」的关卡，不是一次性标签。"""
    project_id = _project()
    try:
        _at_gate(project_id)
        name = _characters(project_id)[0]["name"]
        _attach(project_id, name)
        confirm_anchors(project_id)
        assert get_project(project_id)["status"] == "production_ready"

        regenerate_stage(project_id, "storyboard")
        assert get_project(project_id)["status"] == "awaiting_storyboard_review", "重做分镜应回到分镜审核"

        confirm_storyboard(project_id)
        assert get_project(project_id)["status"] == GATE_STATUS, "再次确认分镜应重新落到锚点门"
        try:
            assert_batch_generation_allowed(get_project(project_id))
        except AdaptationError as exc:
            assert exc.code == "ANCHOR_REVIEW_PENDING", exc.code
        else:
            raise AssertionError("重做分镜后再次确认，批量生成必须重新被拦")
        print("PASS: 重做分镜后重新过门，批量生成再次被拦")
    finally:
        _cleanup(project_id)


# --------------------------------------------------------------------------
# HTTP 契约与零外发
# --------------------------------------------------------------------------


def test_http_review_and_confirm_endpoints() -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    init_environment()
    client = TestClient(app)
    created = client.post(
        "/api/projects",
        json={"title": "锚点门 HTTP 测试", "source_text": SAMPLE, "duration_seconds": 5, "shot_count_mode": "auto"},
    )
    assert created.status_code == 200, created.text
    project_id = created.json()["id"]
    try:
        client.post(f"/api/projects/{project_id}/run")
        options = list_adaptation_options(project_id)
        assert options, "应该先有改编方案"
        select_adaptation_option(project_id, options[0]["id"])
        confirm_scope(project_id, options[0]["id"])
        confirm_bible(project_id)
        confirmed = client.post(f"/api/projects/{project_id}/adaptation/storyboard/confirm")
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == GATE_STATUS, confirmed.json().get("status")

        review = client.get(f"/api/projects/{project_id}/anchors/review")
        assert review.status_code == 200, review.text
        readiness = review.json()["review"]
        assert readiness["required"] is True, readiness
        assert readiness["ready"] is False, readiness

        blocked = client.post(f"/api/projects/{project_id}/adaptation/storyboard/confirm")
        assert blocked.status_code == 200, blocked.text
        assert blocked.json()["status"] == GATE_STATUS, blocked.json().get("status")

        name = _characters(project_id)[0]["name"]
        upload = client.post(
            f"/api/projects/{project_id}/assets/upload",
            data={"asset_role": "character_anchor", "anchor_name": name},
            files={"file": ("anchor.png", _png(), "image/png")},
        )
        assert upload.status_code == 200, upload.text

        rejected = client.post(f"/api/projects/{project_id}/anchors/confirm", json={})
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "production_ready", rejected.json().get("status")

        again = client.post(f"/api/projects/{project_id}/anchors/confirm", json={})
        assert again.status_code == 200 and again.json()["status"] == "production_ready", again.text
        print("PASS: HTTP 就绪查询→确认过门→重复确认幂等 全链路")
    finally:
        _cleanup(project_id)


def test_no_live_calls_made() -> None:
    project_id = _project("锚点门测试-零外发")
    try:
        _at_gate(project_id)
        _attach(project_id, _characters(project_id)[0]["name"])
        confirm_anchors(project_id)
        project = get_project(project_id)
        counts = (
            project.get("live_text_call_count", 0),
            project.get("live_vision_call_count", 0),
            project.get("live_video_call_count", 0),
        )
        assert counts == (0, 0, 0), f"锚点门链路产生了真实调用：{counts}"
        print("PASS: 锚点门链路全程无真实调用（live_*_call_count 全为 0）")
    finally:
        _cleanup(project_id)


def main() -> None:
    test_storyboard_confirm_enters_anchor_review()
    test_checkpoint_node_registered()
    test_batch_generation_blocked_at_gate()
    test_readiness_reports_missing_targets()
    test_confirm_requires_attached_anchor()
    test_confirm_unlocks_production()
    test_project_without_targets_passes_directly()
    test_explicit_skip_allows_passage()
    test_confirm_storyboard_idempotent_inside_gate()
    test_confirm_anchors_idempotent_after_pass()
    test_anchor_attached_after_pass_does_not_reopen_gate()
    test_pause_resume_at_anchor_review()
    test_regenerate_storyboard_reenters_gate()
    test_http_review_and_confirm_endpoints()
    test_no_live_calls_made()
    print("PASS: 视觉锚点审核门")


if __name__ == "__main__":
    main()
