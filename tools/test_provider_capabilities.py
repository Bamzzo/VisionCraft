"""No-cost tests for provider capability contracts and shot-level generation constraints."""
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
    llm = payload["llm"]
    assert isinstance(llm, list) and llm
    flash = next(item for item in llm if item["model"] == "deepseek-v4-flash")
    pro = next(item for item in llm if item["model"] == "deepseek-v4-pro")
    vision = next(item for item in llm if item["model"] == "deepseek-v4-flash-vision-exp")
    for item in (flash, pro, vision):
        assert "provider" in item and item["provider"] == "deepseek"
        assert isinstance(item["configured"], bool)
        assert "roles" in item
        assert "supports_vision" in item
        assert "supports_json" in item
        assert "is_default" in item
        for key in item:
            lowered = str(key).lower()
            assert "api_key" not in lowered
            assert "authorization" not in lowered
            if isinstance(item[key], str):
                assert "sk-" not in item[key]
                assert "bearer " not in item[key].lower()
    assert flash["is_default"] is True
    assert flash["supports_vision"] is False
    assert flash["supports_json"] is True
    assert "text_generation" in flash["roles"]
    assert pro["is_default"] is False
    assert vision["supports_vision"] is True
    assert "vision" in vision["roles"]
    assert payload["stages"]["text_understanding"]["default_model"] == "deepseek-v4-flash"
    assert payload["stages"]["vision_review"]["default_model"] == "deepseek-v4-flash-vision-exp"
    assert payload["default_video_provider"] in {"minimax", "ark", "dashscope", "siliconflow"}
    modes = {item["id"]: item["label"] for item in payload["generation_modes"]}
    assert "不调用真实模型" in modes["mock"]
    assert "失败即失败" in modes["live_strict"]
    assert "允许本地回退" in modes["live_with_local_fallback"]
    access = payload["live_access"]
    assert "hint" in access
    blob = json.dumps(payload, ensure_ascii=False)
    for forbidden in ("DEEPSEEK_API_KEY", "MINIMAX_API_KEY", "VISIONCRAFT_ALLOW_LIVE"):
        assert forbidden not in blob
        assert forbidden not in access["hint"]
    print("PASS: DeepSeek text/vision capability fields and P1 video contract")


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


VIDEO_KEY_ENVS = (
    "SILICONFLOW_API_KEY",
    "VOLC_API_KEY",
    "VOLC_VIDEO_API_KEY",
    "DASHSCOPE_API_KEY",
    "MINIMAX_API_KEY",
)


def _env(values: dict[str, str | None]):
    """临时设置/清除环境变量（None = 清除），退出时还原。

    刻意**不调用 init_environment()**：它会 `load_dotenv` 把 `.env` 里的真实密钥
    重新填回被我清空的变量，用例就测不到"没有密钥"这一支了。
    因此这里也用 `VOLC_API_KEY` 而不是 `ARK_API_KEY` —— 后者要靠 `init_environment`
    才有映射，而 `_ark_api_key()` 读的就是前者。
    """
    import contextlib

    @contextlib.contextmanager
    def manager():
        previous = {key: os.environ.get(key) for key in values}
        try:
            for key, value in values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    return manager()


def test_live_access_recognises_every_video_provider() -> None:
    """诊断面板必须认 ark / dashscope 的密钥，不能只认 MiniMax。

    此前 `keys_present` 只填 deepseek/minimax、`video_ready` 只看 minimax：
    只配 ark 的机器会被报成「视频：未配置访问密钥」，而分发环照样会把请求发出去。
    **诊断说没有、实际有，比没有诊断更糟。**
    """
    blank = dict.fromkeys(VIDEO_KEY_ENVS)

    with _env({**blank, "VISIONCRAFT_ALLOW_LIVE_VIDEO": "1"}):
        empty = get_provider_capabilities()["live_access"]
        assert empty["video_ready"] is False, "一个视频密钥都没有时不应报视频可用"
        assert any("视频：未配置访问密钥" in item for item in empty["blocked_by"]), "应明确说明缺视频密钥"

    with _env({**blank, "VISIONCRAFT_ALLOW_LIVE_VIDEO": "1", "VOLC_API_KEY": "unit-test-ark-key"}):
        ark_only = get_provider_capabilities()["live_access"]
        assert ark_only["keys_present"]["ark"] is True, "ark 密钥必须被诊断面板认到"
        assert ark_only["keys_present"]["minimax"] is False, "没配 MiniMax 就不该说配了"
        assert ark_only["video_ready"] is True, "只配 ark 也应报视频可用——分发环真的会用它"
        assert not any("视频：未配置访问密钥" in item for item in ark_only["blocked_by"]), "不该再报缺视频密钥"
        blob = json.dumps(ark_only, ensure_ascii=False)
        for forbidden in ("VOLC_API_KEY", "ARK_API_KEY", "DASHSCOPE_API_KEY", "MINIMAX_API_KEY"):
            assert forbidden not in blob, f"诊断 payload 泄露了环境变量名：{forbidden}"

    with _env({**blank, "VISIONCRAFT_ALLOW_LIVE_VIDEO": "1", "DASHSCOPE_API_KEY": "unit-test-dashscope-key"}):
        dashscope_only = get_provider_capabilities()["live_access"]
        assert dashscope_only["keys_present"]["dashscope"] is True, "dashscope 密钥必须被诊断面板认到"
        assert dashscope_only["video_ready"] is True, "只配 dashscope 也应报视频可用"
        print("PASS: 视频可用性诊断覆盖全部 provider（不再只认 MiniMax），且不泄露环境变量名")


def main() -> None:
    init_environment()
    init_db()
    test_capability_contract()
    test_validate_frames_and_modes()
    test_prepare_forks_on_provider_switch()
    test_prepare_rejects_i2v_without_frame()
    test_live_access_recognises_every_video_provider()
    print("PASS: provider capability contract and shot-level constraints")


if __name__ == "__main__":
    main()
