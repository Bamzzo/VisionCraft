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
# Hard ceiling so a typo cannot authorize unbounded MiniMax submits.
HARD_MAX_VIDEO_CALLS = 5

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

# 各家视频单价（元/秒）：provider -> 分辨率(小写) -> (有输入视频, 无输入视频)。
# 单价出处与核对日期见 docs/real-live-test-preflight.md「provider 覆盖与单价」。
# 未登记的分辨率回退到**该 provider 最贵的一档**，未登记的 provider 回退到**全表最贵的一档**：
# 这道闸门的职责是别超支，高估是安全方向，低估才是事故。
VIDEO_PRICE_CNY_PER_SECOND: dict[str, dict[str, tuple[float, float]]] = {
    # MiniMax H3 768P：0.50 元/秒。这是本项目的既有口径，测试钉着它，不要顺手改。
    "minimax": {"768p": (0.50, 0.50)},
    # 火山 Seedance 2.0（视频生成增强版算子价目）。
    "ark": {
        "480p": (0.562, 0.924),
        "720p": (1.208, 1.988),
        "1080p": (3.014, 4.958),
        "4k": (6.22, 10.108),
    },
    # 阿里 Wan 2.7 R2V：参考输入不额外计费，有/无输入同价。
    "dashscope": {"720p": (0.60, 0.60), "1080p": (1.00, 1.00)},
    # 兜底通道，未取到公开价，按 MiniMax 档保守取值。
    "siliconflow": {"1280x720": (0.50, 0.50), "720p": (0.50, 0.50)},
}

VIDEO_PRICE_SOURCE = {
    "minimax": "MiniMax H3 768P 官方价 0.50 元/秒",
    "ark": "火山引擎视频生成增强版 seedance_2.0 价目：720p 有输入 1.208、无输入 1.988 元/秒",
    "dashscope": "阿里云百炼 wan2.7-r2v 价目：720P 0.6、1080P 1.0 元/秒",
    "siliconflow": "未取到公开价，按 MiniMax 档保守取值（待核实）",
}

# 默认分辨率/模型必须与 video_provider.py 里各自的读取一致，否则清单会报出一个
# 实际不会使用的型号或档位，那种"看起来精确"的数字比不报更危险。
VIDEO_DEFAULT_RESOLUTION = {
    "ark": lambda: os.getenv("VOLC_VIDEO_RESOLUTION", "720p"),
    "dashscope": lambda: os.getenv("DASHSCOPE_VIDEO_RESOLUTION", "720P"),
    "minimax": lambda: os.getenv("MINIMAX_VIDEO_RESOLUTION", "768P"),
    "siliconflow": lambda: os.getenv("SILICONFLOW_VIDEO_SIZE", "1280x720"),
}
VIDEO_DEFAULT_MODEL = {
    "ark": lambda: os.getenv("VOLC_VIDEO_MODEL") or os.getenv("SEEDANCE_V2_ENDPOINT", "doubao-seedance-2-0-260128"),
    "dashscope": lambda: os.getenv("DASHSCOPE_R2V_MODEL", "wan2.7-r2v"),
    "minimax": lambda: os.getenv("MINIMAX_VIDEO_MODEL", "MiniMax-H3"),
    "siliconflow": lambda: os.getenv("SILICONFLOW_VIDEO_MODEL", "Wan-AI/Wan2.2-T2V-A14B"),
}
# 不带 provider 时的计价对象。保持 MiniMax 是为了不让既有口径与既有断言漂移。
DEFAULT_PRICED_VIDEO_PROVIDER = "minimax"


def video_price_per_second(provider: str | None, *, resolution: str | None = None, has_input_video: bool = True) -> tuple[float, str]:
    """返回 (单价元/秒, 出处说明)。未知的 provider / 分辨率取最贵档，宁可高估。"""
    key = (provider or DEFAULT_PRICED_VIDEO_PROVIDER).strip().lower()
    rows = VIDEO_PRICE_CNY_PER_SECOND.get(key)
    if not rows:
        dearest = max(price for table in VIDEO_PRICE_CNY_PER_SECOND.values() for pair in table.values() for price in pair)
        column = 0 if has_input_video else 1
        return dearest, f"未登记的 provider「{provider}」按全表最贵档保守取值"
    column = 0 if has_input_video else 1
    want = (resolution or "").strip().lower()
    if want and want in rows:
        price = rows[want][column]
        source = VIDEO_PRICE_SOURCE.get(key, "未记录出处")
        return price, f"{key} {want} {'有' if has_input_video else '无'}输入视频 {price} 元/秒（{source}）"
    dearest = max(price for pair in rows.values() for price in pair)
    source = VIDEO_PRICE_SOURCE.get(key, "未记录出处")
    return dearest, f"{key} 未登记分辨率「{resolution}」，按该 provider 最贵档 {dearest} 元/秒保守取值（{source}）"


def estimate_video_call_cny(provider: str | None = None, seconds: int = DEFAULT_VIDEO_SECONDS, *, resolution: str | None = None, has_input_video: bool = True) -> float:
    duration = max(DEFAULT_VIDEO_SECONDS, int(seconds or DEFAULT_VIDEO_SECONDS))
    price, _basis = video_price_per_second(provider, resolution=resolution, has_input_video=has_input_video)
    return price * duration


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


def live_max_video_calls() -> int:
    raw = os.getenv("VISIONCRAFT_LIVE_MAX_VIDEO_CALLS")
    if raw is None or str(raw).strip() == "":
        return MAX_VIDEO_CALLS
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return MAX_VIDEO_CALLS
    if value < 1:
        return MAX_VIDEO_CALLS
    return min(value, HARD_MAX_VIDEO_CALLS)


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


def estimate_closed_loop_cny(
    source_text: str = "",
    *,
    video_seconds: int | None = None,
    provider: str | None = None,
    resolution: str | None = None,
    model: str | None = None,
    has_input_video: bool = True,
) -> dict:
    """按 provider 报价的闭合估算。

    不传 provider 时仍然按 MiniMax 计价——既有口径与既有断言都钉在这个数上，
    「支持多家」不能变成「悄悄换了默认那一家的数」。
    """
    priced_provider = (provider or DEFAULT_PRICED_VIDEO_PROVIDER).strip().lower()
    prompt_chars = len(source_text or "") + SCHEMA_OVERHEAD_CHARS
    text_each = estimate_text_call_cny(prompt_chars)
    text_cny = text_each * MAX_TEXT_CALLS
    vision_cny = estimate_vision_call_cny()
    video_calls = live_max_video_calls()
    seconds = max(DEFAULT_VIDEO_SECONDS, int(video_seconds or DEFAULT_VIDEO_SECONDS))
    eff_resolution = resolution or VIDEO_DEFAULT_RESOLUTION.get(priced_provider, lambda: None)()
    unit_cny, price_basis = video_price_per_second(priced_provider, resolution=eff_resolution, has_input_video=has_input_video)
    video_each = unit_cny * seconds
    video_cny = video_each * video_calls
    total = text_cny + vision_cny + video_cny
    budget = live_budget_cny()
    remaining = round(budget - total, 4)
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
        "video_calls": video_calls,
        "video_each_cny": round(video_each, 4),
        "video_unit_cny": round(unit_cny, 4),
        "video_price_basis": price_basis,
        "video_provider": priced_provider,
        "video_model": model or VIDEO_DEFAULT_MODEL.get(priced_provider, lambda: priced_provider)(),
        "video_seconds": seconds,
        "video_resolution": eff_resolution or "未指定",
        "video_has_input": has_input_video,
        "video_cny": round(video_cny, 4),
        "buffer": COST_BUFFER,
        "fx_usd_cny": USD_CNY,
        "total_cny": round(total, 4),
        "budget_cny": budget,
        "remaining_cny": remaining,
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


def assert_closed_loop_within_budget(source_text: str = "", *, video_seconds: int | None = None) -> dict:
    plan = estimate_closed_loop_cny(source_text, video_seconds=video_seconds)
    if not plan["within_budget"]:
        raise BudgetBlockedError(
            f"预计费用 {plan['total_cny']} 元可能超过 {plan['budget_cny']} 元预算上限，已阻止真实调用（BLOCKED_BEFORE_CALL）。"
        )
    return plan


def live_usage_snapshot(
    project_id: str,
    source_text: str = "",
    *,
    video_seconds: int | None = None,
    provider: str | None = None,
    resolution: str | None = None,
    has_input_video: bool = True,
) -> dict:
    text_used = _load_counter(project_id, "live_text_call_count")
    vision_used = _load_counter(project_id, "live_vision_call_count")
    video_used = _load_counter(project_id, "live_video_call_count")
    plan = estimate_closed_loop_cny(
        source_text or _load_source_text(project_id),
        video_seconds=video_seconds,
        provider=provider,
        resolution=resolution,
        has_input_video=has_input_video,
    )
    return {
        "text_used": text_used,
        "text_max": MAX_TEXT_CALLS,
        "vision_used": vision_used,
        "vision_max": MAX_VISION_CALLS,
        "video_used": video_used,
        "video_max": live_max_video_calls(),
        "estimated_cny": plan["total_cny"],
        "budget_cny": plan["budget_cny"],
        "remaining_cny": plan["remaining_cny"],
        "within_budget": plan["within_budget"],
        "plan": plan,
    }


def _load_source_text(project_id: str) -> str:
    with connect() as conn:
        row = conn.execute("SELECT source_text FROM projects WHERE id = ?", (project_id,)).fetchone()
    return str(row["source_text"] or "") if row else ""


def _assert_live_usage(
    project_id: str,
    source_text: str = "",
    *,
    video_seconds: int | None = None,
    provider: str | None = None,
    resolution: str | None = None,
    has_input_video: bool = True,
) -> dict:
    snapshot = live_usage_snapshot(
        project_id,
        source_text,
        video_seconds=video_seconds,
        provider=provider,
        resolution=resolution,
        has_input_video=has_input_video,
    )
    if snapshot["text_used"] > MAX_TEXT_CALLS:
        raise BudgetBlockedError("文本阶段已超过真实调用上限，已阻止（BLOCKED_BEFORE_CALL）。")
    if snapshot["vision_used"] > MAX_VISION_CALLS:
        raise BudgetBlockedError("视觉检查已超过真实调用上限，已阻止（BLOCKED_BEFORE_CALL）。")
    if snapshot["video_used"] > snapshot["video_max"]:
        raise BudgetBlockedError("视频生成已超过真实调用上限，已阻止（BLOCKED_BEFORE_CALL）。")
    if not snapshot["within_budget"]:
        raise BudgetBlockedError(
            f"预计费用 {snapshot['estimated_cny']} 元可能超过 {snapshot['budget_cny']} 元预算上限，已阻止真实调用（BLOCKED_BEFORE_CALL）。"
        )
    return snapshot


def assert_live_text_allowed(project_id: str, stage: str, prompt_chars: int, source_text: str = "") -> dict:
    if stage not in TEXT_LIVE_STAGES:
        raise BudgetBlockedError(f"阶段 {stage} 不允许真实文本调用。文本阶段仅限改编、Story Bible 与分镜三次。")
    used = _load_counter(project_id, "live_text_call_count")
    if used >= MAX_TEXT_CALLS:
        raise BudgetBlockedError("文本阶段已达到 3 次真实调用上限，已阻止额外请求（BLOCKED_BEFORE_CALL）。")
    _assert_live_usage(project_id, source_text)
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
    _assert_live_usage(project_id, source_text)
    _increment_counter(project_id, "live_vision_call_count")
    return public_request_plan(
        provider="deepseek",
        model="deepseek-v4-flash-vision-exp",
        kind="vision",
        prompt_chars=prompt_chars,
        max_tokens=VISION_MAX_TOKENS,
        call_index=used + 1,
    )


def check_live_video_budget(
    project_id: str,
    *,
    seconds: int = DEFAULT_VIDEO_SECONDS,
    provider: str | None = None,
    model: str | None = None,
    resolution: str | None = None,
    has_input_video: bool = True,
) -> dict:
    """视频真实调用的唯一闸门：授权开关 → 每项目次数上限 → 预算。

    **所有视频 provider 共用这一道。** 此前它只在 MiniMax 分支里被调用，ark 与
    dashscope 是直接分发的——而那两家恰恰是切片 3 要花钱的地方，等于最该拦的地方没拦。
    """
    priced_provider = (provider or DEFAULT_PRICED_VIDEO_PROVIDER).strip().lower()
    if not live_video_authorized():
        raise BudgetBlockedError(
            f"真实视频调用尚未授权（{priced_provider}）。请确认 Provider、模型、次数、参数和预算后再开启。"
        )
    video_max = live_max_video_calls()
    used = _load_counter(project_id, "live_video_call_count")
    if used >= video_max:
        raise BudgetBlockedError(
            f"视频生成已达到 {video_max} 次真实调用上限，已阻止重复提交（BLOCKED_BEFORE_CALL）。"
        )
    eff_resolution = resolution or VIDEO_DEFAULT_RESOLUTION.get(priced_provider, lambda: None)()
    snapshot = _assert_live_usage(
        project_id,
        video_seconds=seconds,
        provider=priced_provider,
        resolution=eff_resolution,
        has_input_video=has_input_video,
    )
    cost = estimate_video_call_cny(priced_provider, seconds, resolution=eff_resolution, has_input_video=has_input_video)
    unit_cny, price_basis = video_price_per_second(priced_provider, resolution=eff_resolution, has_input_video=has_input_video)
    if cost > snapshot["budget_cny"]:
        raise BudgetBlockedError(
            f"{priced_provider} 单次视频预计 {cost:.2f} 元超过 {snapshot['budget_cny']:.2f} 元预算，已阻止（BLOCKED_BEFORE_CALL）。"
        )
    return {
        "provider": priced_provider,
        "model": model or VIDEO_DEFAULT_MODEL.get(priced_provider, lambda: priced_provider)(),
        "kind": "video",
        "seconds": max(DEFAULT_VIDEO_SECONDS, int(seconds or DEFAULT_VIDEO_SECONDS)),
        "resolution": eff_resolution or "未指定",
        "has_input_video": has_input_video,
        "unit_cny": round(unit_cny, 4),
        "price_basis": price_basis,
        "estimated_cny": round(cost, 4),
        "planned_video_cny": snapshot["plan"]["video_cny"],
        "planned_total_cny": snapshot["estimated_cny"],
        "budget_cny": snapshot["budget_cny"],
        "remaining_cny": snapshot["remaining_cny"],
        "call_index": used + 1,
        "video_max": video_max,
        "text_used": snapshot["text_used"],
        "vision_used": snapshot["vision_used"],
        "video_used": used,
    }


def assert_live_video_allowed(
    project_id: str,
    *,
    seconds: int = DEFAULT_VIDEO_SECONDS,
    provider: str | None = None,
    model: str | None = None,
    resolution: str | None = None,
    has_input_video: bool = True,
) -> dict:
    plan = check_live_video_budget(
        project_id,
        seconds=seconds,
        provider=provider,
        model=model,
        resolution=resolution,
        has_input_video=has_input_video,
    )
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
