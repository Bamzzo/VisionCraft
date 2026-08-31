"""无费用测试：live 分镜必须遵守手动 requested_shot_count。不打开 LIVE 开关，不发真实 HTTP。"""
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

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db, utc_now
from backend.providers.llm_adapter import (
    FunctionTransport,
    captured_requests,
    coerce_storyboard,
    reset_chat_transport,
    set_chat_transport,
)
from backend.services.adaptation_service import (
    confirm_bible,
    confirm_scope,
    confirm_storyboard,
    list_adaptation_options,
    start_adaptation_workflow,
)
from backend.services.model_config_service import set_generation_mode
from backend.services.project_service import get_project, resolve_storyboard_shot_count
from backend.schemas import ProjectCreate

SAMPLE = (
    "方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。"
    "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。"
    "但是族中长老已经设下阻碍，他只能选择冒险一搏。"
    "最终他停在山门前，留下未说完的话。"
)
EXCERPT = "方源走在青茅山的夜路上"
PREFIX = "scount_"
CREATED: list[str] = []


def _guard_network() -> None:
    import urllib.request

    def blocked(*_args, **_kwargs):
        raise AssertionError("shot-count tests must not open network sockets")

    urllib.request.urlopen = blocked  # type: ignore[assignment]


def _clear_live_flags() -> None:
    for key in list(os.environ):
        if key.startswith("VISIONCRAFT_ALLOW_LIVE"):
            os.environ.pop(key, None)


def _cleanup(project_id: str) -> None:
    reset_chat_transport()
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)


def _shot(index: int, title: str) -> dict:
    return {
        "title": title,
        "narrative_purpose": f"目的{index}",
        "characters": ["方源"],
        "scene": "青茅山",
        "action_text": f"动作{index}",
        "camera_motion": "固定",
        "duration_seconds": 5,
        "visual_prompt": "cinematic clean realism",
        "source_excerpt": EXCERPT,
    }


def _staged_transport(storyboard_shots: list[dict], *, suggested_shot_count: int = 5) -> FunctionTransport:
    options = {
        "options": [
            {
                "title": "模型冲突方案",
                "rationale": "抓住争夺",
                "protagonist_goal": "拿下传承",
                "conflict": "争夺传承",
                "ending_orientation": "山门未完",
                "suggested_duration_seconds": 30,
                "suggested_shot_count": suggested_shot_count,
                "source_excerpt": EXCERPT,
            }
        ]
    }
    bible = {
        "logline": "少年归途",
        "adaptation_summary": "短句改编",
        "summary": "摘要",
        "worldview": "写实",
        "emotion_curve": "压抑到决断",
        "protagonist": "方源",
        "protagonist_goal": "拿下春秋蝉",
        "obstacle": "长老阻碍",
        "visual_style": "cinematic",
        "consistency_constraints": "服装一致",
        "themes": ["选择"],
        "style_tags": ["cinematic"],
        "character_cards": [{"name": "方源", "identity": "少年", "appearance": "", "motivation": "", "invariant": ""}],
        "scene_cards": [{"name": "青茅山", "environment": "夜路", "time": "夜", "visuals": "", "invariant": ""}],
    }

    def send(prepared):
        user = json.loads(prepared.body["messages"][-1]["content"])
        schema = user.get("json_schema") or {}
        if "shots" in schema:
            payload = {"shots": storyboard_shots}
        elif "logline" in schema:
            payload = bible
        else:
            payload = options
        return {"id": "chatcmpl_local", "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}

    return FunctionTransport(send)


def _project(*, mode: str, requested: int | None, generation_mode: str = "live_strict") -> str:
    init_environment()
    init_db()
    project_id = f"{PREFIX}{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode, requested_shot_count,
             status, routing_mode, generation_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "镜头数约束",
                SAMPLE,
                "cinematic clean realism",
                "16:9",
                5,
                mode,
                requested,
                "created",
                "direct",
                generation_mode,
                now,
                now,
            ),
        )
    CREATED.append(project_id)
    return project_id


def _run_live_storyboard(project_id: str, shots: list[dict], *, suggested: int = 5) -> dict:
    reset_chat_transport()
    set_generation_mode(project_id, "live_strict")
    set_chat_transport(_staged_transport(shots, suggested_shot_count=suggested))
    start_adaptation_workflow(project_id)
    option = list_adaptation_options(project_id)[0]
    confirm_scope(project_id, option["id"])
    confirm_bible(project_id)
    return get_project(project_id)


def _storyboard_request_shot_count() -> int | None:
    for prepared in captured_requests():
        user = prepared.body["messages"][-1]["content"]
        try:
            payload = json.loads(user)
        except json.JSONDecodeError:
            continue
        if "shot_count" in payload and "shots" in (payload.get("json_schema") or {}):
            return int(payload["shot_count"])
    return None


def test_resolve_manual_wins_over_suggestion() -> None:
    project = {"shot_count_mode": "manual", "requested_shot_count": 2, "source_text": SAMPLE}
    assert resolve_storyboard_shot_count(project, {"suggested_shot_count": 5}) == 2
    auto = {"shot_count_mode": "auto", "requested_shot_count": 2, "source_text": SAMPLE}
    assert resolve_storyboard_shot_count(auto, {"suggested_shot_count": 5}) == 5
    payload = ProjectCreate(title="t", source_text=SAMPLE, shot_count_mode="manual", requested_shot_count=3)
    from backend.services.project_service import compute_shot_count

    assert compute_shot_count(payload) == 3
    print("PASS: 手动镜头数优先于 suggested_shot_count；auto 才用模型建议")


def test_coerce_trim_and_pad() -> None:
    fallback = [_shot(1, "备")]
    five = {"shots": [_shot(i, f"模型镜{i}") for i in range(1, 6)]}
    trimmed = coerce_storyboard(five, fallback, SAMPLE, shot_count=2)
    assert [item["title"] for item in trimmed] == ["模型镜1", "模型镜2"]
    assert len(trimmed) == 2
    one = {"shots": [_shot(1, "仅一镜")]}
    padded = coerce_storyboard(one, fallback, SAMPLE, shot_count=2)
    assert len(padded) == 2
    assert padded[0]["title"] == "仅一镜"
    assert padded[0]["source_type"] == "auto_draft"
    assert padded[1]["source_type"] == "count_normalized"
    assert padded[1]["count_normalized"] is True
    print("PASS: 模型 5 镜裁为 2；模型 1 镜按安全规则补齐为 2 并标记 count_normalized")


def test_live_strict_manual_two() -> None:
    project_id = _project(mode="manual", requested=2)
    try:
        project = _run_live_storyboard(project_id, [_shot(i, f"模型镜{i}") for i in range(1, 6)], suggested=5)
        drafts = project["storyboard_drafts"]
        assert len(drafts) == 2, drafts
        assert _storyboard_request_shot_count() == 2
        assert all(item.get("generation_mode") == "live_strict" for item in drafts)
        assert all(int(item.get("used_local_fallback") or 0) == 0 for item in drafts)
        confirm_storyboard(project_id)
        shots = get_project(project_id)["shots"]
        assert len(shots) == 2
        print("PASS: live_strict 手动 2 镜，模型返回 5 镜后裁为 2 镜")
    finally:
        _cleanup(project_id)


def test_live_strict_manual_three() -> None:
    project_id = _project(mode="manual", requested=3)
    try:
        project = _run_live_storyboard(project_id, [_shot(i, f"三镜{i}") for i in range(1, 4)], suggested=5)
        assert len(project["storyboard_drafts"]) == 3
        assert _storyboard_request_shot_count() == 3
        print("PASS: live_strict 手动 3 镜，最终严格 3 镜")
    finally:
        _cleanup(project_id)


def test_live_strict_manual_pad_from_one() -> None:
    project_id = _project(mode="manual", requested=2)
    try:
        project = _run_live_storyboard(project_id, [_shot(1, "仅一镜")], suggested=5)
        drafts = sorted(project["storyboard_drafts"], key=lambda item: item["shot_index"])
        assert len(drafts) == 2
        types = [item["source_type"] for item in drafts]
        assert types[0] == "auto_draft"
        assert types[1] == "count_normalized"
        assert drafts[0].get("generation_mode") == "live_strict"
        assert drafts[1].get("generation_mode") == "live_strict"
        print("PASS: 模型返回 1 镜时按现有安全规则补齐到手动 2 镜，补齐镜标记 count_normalized，保留 live 血缘")
    finally:
        _cleanup(project_id)


def test_auto_uses_model_suggestion() -> None:
    project_id = _project(mode="auto", requested=None)
    try:
        project = _run_live_storyboard(project_id, [_shot(i, f"自动{i}") for i in range(1, 6)], suggested=5)
        assert len(project["storyboard_drafts"]) == 5
        assert _storyboard_request_shot_count() == 5
        print("PASS: auto 模式使用模型建议镜头数 5")
    finally:
        _cleanup(project_id)


def test_mock_auto_unchanged() -> None:
    project_id = _project(mode="auto", requested=None, generation_mode="mock")
    try:
        reset_chat_transport()
        start_adaptation_workflow(project_id)
        option = list_adaptation_options(project_id)[0]
        confirm_scope(project_id, option["id"])
        confirm_bible(project_id)
        drafts = get_project(project_id)["storyboard_drafts"]
        assert 4 <= len(drafts) <= 8
        print("PASS: mock 自动档仍为 4～8 镜，无回归")
    finally:
        _cleanup(project_id)


def test_mock_manual_two() -> None:
    project_id = _project(mode="manual", requested=2, generation_mode="mock")
    try:
        reset_chat_transport()
        start_adaptation_workflow(project_id)
        option = list_adaptation_options(project_id)[0]
        confirm_scope(project_id, option["id"])
        confirm_bible(project_id)
        drafts = get_project(project_id)["storyboard_drafts"]
        assert len(drafts) == 2
        print("PASS: mock 手动 2 镜最终也是 2 镜")
    finally:
        _cleanup(project_id)


def main() -> None:
    _clear_live_flags()
    _guard_network()
    test_resolve_manual_wins_over_suggestion()
    test_coerce_trim_and_pad()
    test_live_strict_manual_two()
    test_live_strict_manual_three()
    test_live_strict_manual_pad_from_one()
    test_auto_uses_model_suggestion()
    test_mock_auto_unchanged()
    test_mock_manual_two()
    print("INFO: real_network=否 cost_cny=0")
    print("PASS: storyboard shot-count contract")


if __name__ == "__main__":
    try:
        main()
    finally:
        reset_chat_transport()
        for project_id in list(CREATED):
            try:
                _cleanup(project_id)
            except Exception:
                pass
