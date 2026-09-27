"""No-cost tests for the paid-call gate covering every video provider (slice 4).

Why this exists
---------------
The gate - authorisation switch, per-project call cap, budget ceiling - only ran
inside ``_generate_minimax_video``. ``generate_video_asset`` dispatched straight
to ark and dashscope, so a paid run against those two had **no local guard at
all**: authorised only by a human saying yes, with no call cap and no budget
check. That is exactly where slice 3 spends money.

The unit price was MiniMax-only as well (0.50 CNY/s). Ark's Seedance 2.0 costs
1.208 CNY/s at 720p when there is an input image, so one global number cannot
stand in for the others - it would under-estimate ark by ~2.4x.

What this pins
--------------
1. every provider is refused *before any network call* when authorisation is off;
2. the unit price is per provider and per resolution, and MiniMax's own figure
   does not drift while the table is added;
3. an unknown provider or resolution is priced at the dearest known tier -
   over-estimating is the safe direction for a guard;
4. the block reaches the caller as BLOCKED_BEFORE_CALL, not disguised as
   "all live video providers failed".

Prices are the ones published by each vendor; see
``docs/real-live-test-preflight.md`` for the source of each row.
"""
from __future__ import annotations

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
from backend.providers.live_budget import (
    VIDEO_PRICE_CNY_PER_SECOND,
    BudgetBlockedError,
    check_live_video_budget,
    estimate_closed_loop_cny,
    estimate_minimax_i2v_cny,
    estimate_video_call_cny,
    video_price_per_second,
)
from backend.providers.video_provider import (
    VideoAssetRequest,
    generate_video_asset,
    reset_video_json_transport,
    set_video_json_transport,
)

SAMPLE = "春秋蝉鸣少年归"
PROVIDERS = ("ark", "dashscope", "minimax", "siliconflow")
CREATED: list[str] = []


def pass_(msg: str) -> None:
    print(f"PASS: {msg}")


def _guard_network() -> None:
    import urllib.request

    def blocked(*_args, **_kwargs):
        raise AssertionError("video guard tests must not open network sockets")

    urllib.request.urlopen = blocked  # type: ignore[assignment]


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


def _project(title: str = "付费闸门") -> str:
    init_environment()
    init_db()
    project_id = f"gate_{uuid.uuid4().hex[:10]}"
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


def _video_request(project_id: str, shot_id: str, *, mode: str = "t2v") -> VideoAssetRequest:
    with connect() as conn:
        version_id = conn.execute("SELECT current_version_id FROM shots WHERE id = ?", (shot_id,)).fetchone()["current_version_id"]
    return VideoAssetRequest(
        project_id=project_id,
        shot_id=shot_id,
        version_id=version_id,
        title="镜01",
        description="动作",
        prompt="prompt",
        first_frame_path=None,
        duration_seconds=5,
        video_mode=mode,
    )


def _video_task_count(project_id: str) -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM video_tasks WHERE project_id = ?", (project_id,)).fetchone()["n"]


def _cleanup() -> None:
    reset_video_json_transport()
    for project_id in list(CREATED):
        with connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
    CREATED.clear()


def test_every_provider_needs_authorisation() -> None:
    project_id = _project()
    with _env(VISIONCRAFT_ALLOW_LIVE_VIDEO=None, VISIONCRAFT_ALLOW_LIVE_LLM=None):
        for provider in PROVIDERS:
            try:
                check_live_video_budget(project_id, seconds=5, provider=provider)
                raise AssertionError(f"{provider} 未授权时必须被拒绝")
            except BudgetBlockedError as exc:
                assert exc.code == "BLOCKED_BEFORE_CALL", exc.code
                assert "授权" in str(exc), f"{provider} 的拒绝必须说清是授权问题，实际：{exc}"
    pass_("四个视频 provider 在未授权时一律被拒绝，且原因是「未授权」而不是别的东西")
    _cleanup()


def test_unit_price_is_per_provider() -> None:
    ark = estimate_video_call_cny("ark", 5, resolution="720p", has_input_video=True)
    dashscope = estimate_video_call_cny("dashscope", 5, resolution="720P", has_input_video=True)
    minimax = estimate_video_call_cny("minimax", 5, resolution="768P")
    assert round(ark, 4) == round(1.208 * 5, 4), ark
    assert round(dashscope, 4) == round(0.6 * 5, 4), dashscope
    assert round(minimax, 4) == round(0.5 * 5, 4), minimax
    assert ark > minimax * 2, "ark 有输入视频价约为 MiniMax 的 2.4 倍，一个全局单价必然低估其中一家"
    assert round(minimax, 4) == round(estimate_minimax_i2v_cny(5), 4), "加价目表不能让 MiniMax 旧口径漂移"
    pass_("单价按 provider 与分辨率取值：ark 6.04 元 / dashscope 3.0 元 / MiniMax 2.5 元（5 秒）")
    _cleanup()


def test_unknown_provider_or_resolution_is_priced_at_the_dearest_tier() -> None:
    known_prices = [price for rows in VIDEO_PRICE_CNY_PER_SECOND.values() for pair in rows.values() for price in pair]
    unknown_provider, basis_provider = video_price_per_second("brand-new-provider", resolution="720p", has_input_video=True)
    unknown_resolution, basis_resolution = video_price_per_second("ark", resolution="4320p", has_input_video=True)
    assert unknown_provider >= max(known_prices), (unknown_provider, max(known_prices))
    assert unknown_resolution >= max(VIDEO_PRICE_CNY_PER_SECOND["ark"]["720p"]), unknown_resolution
    assert basis_provider and basis_resolution, "取价必须带出处，不能只给一个数字"
    with_input, _ = video_price_per_second("ark", resolution="720p", has_input_video=True)
    without_input, _ = video_price_per_second("ark", resolution="720p", has_input_video=False)
    assert without_input > with_input, "纯文生视频比图生视频贵，取价必须区分"
    pass_("未登记的 provider / 分辨率按最贵档计，且「无输入视频」比「有输入视频」贵")
    _cleanup()


def test_closed_loop_plan_can_price_any_provider() -> None:
    with _env(VISIONCRAFT_LIVE_MAX_VIDEO_CALLS="3", VISIONCRAFT_LIVE_BUDGET_CNY="30"):
        ark = estimate_closed_loop_cny(SAMPLE, video_seconds=5, provider="ark", resolution="720p")
        minimax = estimate_closed_loop_cny(SAMPLE, video_seconds=5)
    assert ark["video_provider"] == "ark", ark["video_provider"]
    assert round(ark["video_unit_cny"], 4) == 1.208, ark.get("video_unit_cny")
    assert round(ark["video_cny"], 4) == round(1.208 * 5 * 3, 4), ark["video_cny"]
    assert ark["video_price_basis"], "报清单要能带出单价出处"
    assert minimax["video_provider"] == "minimax" and round(minimax["video_unit_cny"], 4) == 0.5
    pass_("闭合估算可按 provider 报价并带出处，未指定时默认仍是 MiniMax")
    _cleanup()


def test_dispatch_blocks_before_network_for_ark_and_dashscope() -> None:
    posts: list[str] = []

    def json_transport(method: str, url: str, _payload: dict | None) -> dict:
        posts.append(url)
        return {"id": "task_should_not_exist"} if method == "POST" else {"status": "running"}

    set_video_json_transport(json_transport)
    try:
        with _env(VISIONCRAFT_ALLOW_LIVE_VIDEO=None, VISIONCRAFT_ALLOW_LIVE_LLM=None):
            for provider in ("ark", "dashscope"):
                project_id = _project(f"未授权 {provider}")
                shot_id = _add_shot(project_id)
                request = _video_request(project_id, shot_id)
                request.provider_override = provider
                try:
                    generate_video_asset(request)
                    raise AssertionError(f"{provider} 未授权时不得真正提交")
                except BudgetBlockedError as exc:
                    assert exc.code == "BLOCKED_BEFORE_CALL", exc.code
                assert _video_task_count(project_id) == 0, f"{provider} 被拦下时不该留下 video_tasks"
        assert posts == [], f"被闸门拦下的提交不该产生任何 HTTP，实际：{posts}"
        pass_("ark 与 dashscope 在分发环被同一道闸门拦下：零 HTTP、零 video_tasks")
        pass_("闸门拦截以 BLOCKED_BEFORE_CALL 上抛，没有被伪装成「所有 provider 都失败」")
    finally:
        _cleanup()


def test_authorised_call_reaches_provider_and_is_capped_per_project() -> None:
    posts: list[str] = []

    def json_transport(method: str, url: str, _payload: dict | None) -> dict:
        posts.append(url)
        return {"id": f"task_ark_{len(posts)}", "status": "submitted"} if method == "POST" else {"status": "running"}

    set_video_json_transport(json_transport)
    try:
        with _env(
            VISIONCRAFT_ALLOW_LIVE_VIDEO="1",
            VISIONCRAFT_LIVE_MAX_VIDEO_CALLS="1",
            VISIONCRAFT_LIVE_BUDGET_CNY="30",
            VISIONCRAFT_VIDEO_PROVIDER=None,
            ARK_API_KEY="mock-ark-key",
            VOLC_VIDEO_POLL_SECONDS="0",
        ):
            project_id = _project("已授权 ark")
            first, second = _add_shot(project_id, 1), _add_shot(project_id, 2)
            request = _video_request(project_id, first)
            request.provider_override = "ark"
            result = generate_video_asset(request)
            assert result.provider == "ark", result.provider
            assert len(posts) == 1, posts

            again = _video_request(project_id, second)
            again.provider_override = "ark"
            try:
                generate_video_asset(again)
                raise AssertionError("每项目上限为 1 时，第二次 ark 提交必须被拦下")
            except BudgetBlockedError as exc:
                assert exc.code == "BLOCKED_BEFORE_CALL", exc.code
                assert "1 次" in str(exc), f"拒绝文案要说明撞到的是次数上限：{exc}"
            assert len(posts) == 1, f"第二次提交不该发出 HTTP，实际：{posts}"
        pass_("授权后 ark 提交能打到 provider（正面控制：证明拦下来的是闸门而不是别的）")
        pass_("同一项目第二次提交被每项目次数上限拦下，且未产生第二个 HTTP")
    finally:
        _cleanup()


def test_budget_ceiling_applies_to_ark_not_only_minimax() -> None:
    project_id = _project("ark 超预算")
    with _env(VISIONCRAFT_ALLOW_LIVE_VIDEO="1", VISIONCRAFT_LIVE_BUDGET_CNY="5", VISIONCRAFT_LIVE_MAX_VIDEO_CALLS="1"):
        # 15 秒 ark（720p、有输入）= 18.12 元，远超 5 元预算；MiniMax 同期只要 7.5 元。
        try:
            check_live_video_budget(project_id, seconds=15, provider="ark", resolution="720p")
            raise AssertionError("ark 的超预算提交必须被拦下")
        except BudgetBlockedError as exc:
            assert exc.code == "BLOCKED_BEFORE_CALL", exc.code
            assert "预算" in str(exc), f"拒绝文案要说明撞到的是预算：{exc}"
    pass_("预算上限对 ark 同样生效，且文案区分「超预算」与「未授权」「超次数」")
    _cleanup()


def main() -> None:
    _guard_network()
    init_environment()
    init_db()
    os.environ.pop("VISIONCRAFT_ALLOW_LIVE_LLM", None)
    os.environ.pop("VISIONCRAFT_ALLOW_LIVE_VIDEO", None)
    os.environ.pop("VISIONCRAFT_LIVE_BUDGET_CNY", None)
    os.environ.pop("VISIONCRAFT_LIVE_MAX_VIDEO_CALLS", None)
    try:
        test_every_provider_needs_authorisation()
        test_unit_price_is_per_provider()
        test_unknown_provider_or_resolution_is_priced_at_the_dearest_tier()
        test_closed_loop_plan_can_price_any_provider()
        test_dispatch_blocks_before_network_for_ark_and_dashscope()
        test_authorised_call_reaches_provider_and_is_capped_per_project()
        test_budget_ceiling_applies_to_ark_not_only_minimax()
        print("PASS: paid-call gate covers every video provider (no live network)")
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
