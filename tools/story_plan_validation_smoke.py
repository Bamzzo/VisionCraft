from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.providers import llm_provider
from backend.providers.llm_provider import LLMConfig
from backend.schemas import ProjectCreate


def _payload() -> ProjectCreate:
    return ProjectCreate(
        title="校验测试",
        source_text="雨夜里，一封旧信把两个人重新带回同一座城。",
        style="cinematic realism",
        aspect_ratio="16:9",
        duration_seconds=5,
        shot_count_mode="manual",
        requested_shot_count=2,
    )


def _valid_plan() -> dict:
    return {
        "summary": "一封旧信引出重逢。",
        "worldview": "潮湿城市中的现实主义影像。",
        "style_tags": ["cinematic", "rain"],
        "themes": ["重逢", "选择"],
        "characters": [
            {
                "name": "收信人",
                "role": "protagonist",
                "description": "收到旧信后重新做出选择的人。",
                "visual_prompt": "restrained protagonist in rainy city",
            }
        ],
        "scenes": [
            {
                "name": "雨巷",
                "description": "潮湿安静的街巷。",
                "visual_prompt": "rainy alley, wet stone, cinematic light",
            }
        ],
        "shots": [
            {
                "title": "旧信",
                "description": "收信人在雨声中打开旧信。",
                "characters": ["收信人"],
                "scene": "雨巷",
                "camera_motion": "slow push in",
                "visual_prompt": "rainy alley, letter in hand, cinematic realism",
                "negative_prompt": "low quality, watermark",
                "audio_prompt": "rain ambience",
            },
            {
                "title": "回望",
                "description": "收信人停步回望街口。",
                "characters": ["收信人"],
                "scene": "雨巷",
                "camera_motion": "locked-off medium shot",
                "visual_prompt": "quiet rainy alley, emotional pause",
                "negative_prompt": "low quality, watermark",
                "audio_prompt": "soft rain and distant traffic",
            },
        ],
    }


def _short_plan() -> dict:
    plan = _valid_plan()
    plan["shots"] = plan["shots"][:1]
    return plan


def main() -> None:
    os.environ["PLAN_VALIDATION_MAX_RETRIES"] = "2"
    payload = _payload()
    config = LLMConfig(api_key="test", base_url="http://test", model="test")
    original = llm_provider._call_chat_json
    try:
        calls: list[int] = []

        def retry_then_valid(*args, **kwargs):
            calls.append(1)
            return _short_plan() if len(calls) == 1 else _valid_plan()

        llm_provider._call_chat_json = retry_then_valid
        repaired = llm_provider._call_storyboard_llm(config, payload, 2, payload.source_text, "direct")
        assert repaired["_validation"]["status"] == "validated"
        assert repaired["_validation"]["attempts"] == 2
        assert len(repaired["shots"]) == 2
        print("retry_then_valid ok attempts=2")

        def always_short(*args, **kwargs):
            return _short_plan()

        llm_provider._call_chat_json = always_short
        coerced = llm_provider._call_storyboard_llm(config, payload, 2, payload.source_text, "direct")
        assert coerced["_validation"]["status"] == "coerced_after_validation_failure"
        assert coerced["_validation"]["attempts"] == 3
        assert len(coerced["shots"]) == 2
        print("coerce_after_validation_failure ok attempts=3")
    finally:
        llm_provider._call_chat_json = original


if __name__ == "__main__":
    main()
