"""Local cost estimation and live-call caps. Never opens a network socket."""
from __future__ import annotations

import math
import os

from ..database import connect, utc_now
from .llm_catalog import ModelConfigError, live_llm_authorized

TEXT_MAX_TOKENS = 4096
VISION_MAX_TOKENS = 2048
THINKING_DISABLED = {"type": "disabled"}
TEXT_LIVE_STAGES = ("adaptation_options", "story_bible", "storyboard")
MAX_TEXT_CALLS = 3
MAX_VISION_CALLS = 1
MAX_VIDEO_CALLS = 1

# Conservative FX so USD list prices are not under-converted into the 5 CNY cap.
USD_CNY = 7.5
COST_BUFFER = 1.30
FLASH_INPUT_USD_PER_MILLION = 0.44  # DeepSeek peak cache-miss
FLASH_OUTPUT_USD_PER_MILLION = 1.32
VISION_IMAGE_TOKENS = 384
SCHEMA_OVERHEAD_CHARS = 4000
MINIMAX_H3_768P_CNY_PER_SECOND = 0.50
DEFAULT_VIDEO_SECONDS = 4
DEFAULT_BUDGET_CNY = 5.0


class BudgetBlockedError(ModelConfigError):
    def __init__(self, message: str) -> None:
        super().__init__("BLOCKED_BEFORE_CALL", message)


def live_budget_cny() -> float:
    raw = os.getenv("VISIONCRAFT_LIVE_BUDGET_CNY", str(DEFAULT_BUDGET_CNY))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_BUDGET_CNY
    return value if value > 0 else DEFAULT_BUDGET_CNY


def live_video_authorized() -> bool:
    return os.getenv("VISIONCRAFT_ALLOW_LIVE_VIDEO") == "1" or live_llm_authorized()


def estimate_tokens_from_chars(char_count: int) -> int:
    """Conservative mixed CJK/JSON estimate: 1 character ≈ 1 token."""
    return max(1, int(char_count or 0))


def apply_cost_buffer(tokens: int) -> int:
    return int(math.ceil(max(1, int(tokens)) * COST_BUFFER))


def estimate_deepseek_cny(input_tokens: int, output_tokens: int) -> float:
    usd = (input_tokens / 1_000_000) * FLASH_INPUT_USD_PER_MILLION + (output_tokens / 1_000_000) * FLASH_OUTPUT_USD_PER_MILLION
    return usd * USD_CNY


def estimate_text_call_cny(prompt_chars: int, *, max_tokens: int = TEXT_MAX_TOKENS) -> float:
    inp = apply_cost_buffer(estimate_tokens_from_chars(prompt_chars))
    out = apply_cost_buffer(max_tokens)
    return estimate_deepseek_cny(inp, out)


def estimate_vision_call_cny(prompt_chars: int = 400) -> float:
    inp = apply_cost_buffer(estimate_tokens_from_chars(prompt_chars) + VISION_IMAGE_TOKENS)
    out = apply_cost_buffer(VISION_MAX_TOKENS)
    return estimate_deepseek_cny(inp, out)


def estimate_minimax_i2v_cny(seconds: int = DEFAULT_VIDEO_SECONDS) -> float:
    duration = max(DEFAULT_VIDEO_SECONDS, int(seconds or DEFAULT_VIDEO_SECONDS))
    return MINIMAX_H3_768P_CNY_PER_SECOND * duration


def estimate_closed_loop_cny(source_text: str = "") -> dict:
    prompt_chars = len(source_text or "") + SCHEMA_OVERHEAD_CHARS
    text_each = estimate_text_call_cny(prompt_chars)
    text_cny = text_each * MAX_TEXT_CALLS
    vision_cny = estimate_vision_call_cny()
    video_cny = estimate_minimax_i2v_cny(DEFAULT_VIDEO_SECONDS)
    total = text_cny + vision_cny + video_cny
    budget = live_budget_cny()
    return {
        "text_calls": MAX_TEXT_CALLS,
        "text_model": "deepseek-v4-flash",
        "text_max_tokens": TEXT_MAX_TOKENS,
        "text_thinking": "disabled",
        "text_cny": round(text_cny, 4),
        "vision_calls": MAX_VISION_CALLS,
        "vision_model": "deepseek-v4-flash-vision-exp",
        "vision_max_tokens": VISION_MAX_TOKENS,
        "vision_thinking": "disabled",
        "vision_cny": round(vision_cny, 4),
        "video_calls": MAX_VIDEO_CALLS,
        "video_provider": "minimax",
        "video_model": "MiniMax-H3",
        "video_seconds": DEFAULT_VIDEO_SECONDS,
        "video_resolution": "768P",
        "video_cny": round(video_cny, 4),
        "buffer": COST_BUFFER,
        "fx_usd_cny": USD_CNY,
        "total_cny": round(total, 4),
        "budget_cny": budget,
        "within_budget": total <= budget,
    }


def public_request_plan(*, provider: str, model: str, kind: str, prompt_chars: int, max_tokens: int, call_index: int | None = None) -> dict:
    return {
        "provider": provider,
        "model": model,
        "kind": kind,
        "thinking": "disabled",
        "max_tokens": max_tokens,
        "prompt_chars": int(prompt_chars or 0),
        "call_index": call_index,
        "estimated_cny": round(
            estimate_text_call_cny(prompt_chars, max_tokens=max_tokens) if kind == "text" else estimate_vision_call_cny(prompt_chars),
            4,
        ),
    }


def assert_closed_loop_within_budget(source_text: str = "") -> dict:
    plan = estimate_closed_loop_cny(source_text)
    if not plan["within_budget"]:
        raise BudgetBlockedError(
            f"预计费用 {plan['total_cny']} 元可能超过 {plan['budget_cny']} 元预算上限，已阻止真实调用（BLOCKED_BEFORE_CALL）。"
        )
    return plan


def assert_live_text_allowed(project_id: str, stage: str, prompt_chars: int, source_text: str = "") -> dict:
    if stage not in TEXT_LIVE_STAGES:
        raise BudgetBlockedError(f"阶段 {stage} 不允许真实文本调用。文本阶段仅限改编、Story Bible 与分镜三次。")
    used = _load_counter(project_id, "live_text_call_count")
    if used >= MAX_TEXT_CALLS:
        raise BudgetBlockedError("文本阶段已达到 3 次真实调用上限，已阻止额外请求（BLOCKED_BEFORE_CALL）。")
    assert_closed_loop_within_budget(source_text)
    this_cost = estimate_text_call_cny(prompt_chars)
    if this_cost > live_budget_cny():
        raise BudgetBlockedError(
            f"单次文本调用预计 {round(this_cost, 4)} 元超过预算，已阻止（BLOCKED_BEFORE_CALL）。"
        )
    count = _increment_counter(project_id, "live_text_call_count")
    return public_request_plan(
        provider="deepseek",
        model="deepseek-v4-flash",
        kind="text",
        prompt_chars=prompt_chars,
        max_tokens=TEXT_MAX_TOKENS,
        call_index=count,
    )


def assert_live_vision_allowed(project_id: str, prompt_chars: int = 400, source_text: str = "") -> dict:
    used = _load_counter(project_id, "live_vision_call_count")
    if used >= MAX_VISION_CALLS:
        raise BudgetBlockedError("视觉检查已达到 1 次真实调用上限，已阻止（BLOCKED_BEFORE_CALL）。")
    assert_closed_loop_within_budget(source_text)
    _increment_counter(project_id, "live_vision_call_count")
    return public_request_plan(
        provider="deepseek",
        model="deepseek-v4-flash-vision-exp",
        kind="vision",
        prompt_chars=prompt_chars,
        max_tokens=VISION_MAX_TOKENS,
        call_index=used + 1,
    )


def check_live_video_budget(project_id: str, *, seconds: int = DEFAULT_VIDEO_SECONDS) -> dict:
    if not live_video_authorized():
        raise BudgetBlockedError("真实视频调用尚未授权。请确认 Provider、模型、次数、参数和预算后再开启。")
    used = _load_counter(project_id, "live_video_call_count")
    if used >= MAX_VIDEO_CALLS:
        raise BudgetBlockedError("视频生成已达到 1 次真实调用上限，已阻止重复提交（BLOCKED_BEFORE_CALL）。")
    cost = estimate_minimax_i2v_cny(seconds)
    if cost > live_budget_cny():
        raise BudgetBlockedError(
            f"MiniMax I2V 预计 {cost:.2f} 元超过 {live_budget_cny():.2f} 元预算，已阻止（BLOCKED_BEFORE_CALL）。"
        )
    return {
        "provider": "minimax",
        "model": "MiniMax-H3",
        "kind": "video",
        "seconds": max(DEFAULT_VIDEO_SECONDS, int(seconds or DEFAULT_VIDEO_SECONDS)),
        "resolution": "768P",
        "estimated_cny": round(cost, 4),
        "call_index": used + 1,
    }


def assert_live_video_allowed(project_id: str, *, seconds: int = DEFAULT_VIDEO_SECONDS) -> dict:
    plan = check_live_video_budget(project_id, seconds=seconds)
    plan["call_index"] = _increment_counter(project_id, "live_video_call_count")
    return plan


def _load_counter(project_id: str, column: str) -> int:
    with connect() as conn:
        row = conn.execute(f"SELECT {column} FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise BudgetBlockedError("项目不存在。")
    return int(row[column] or 0)


def _increment_counter(project_id: str, column: str) -> int:
    with connect() as conn:
        conn.execute(
            f"UPDATE projects SET {column} = COALESCE({column}, 0) + 1, updated_at = ? WHERE id = ?",
            (utc_now(), project_id),
        )
        row = conn.execute(f"SELECT {column} FROM projects WHERE id = ?", (project_id,)).fetchone()
    return int(row[column] or 0)
