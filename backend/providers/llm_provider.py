import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..schemas import ProjectCreate


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str


class _PlanBase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    @field_validator("*", mode="before")
    @classmethod
    def _strip_string_fields(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class StoryCharacterModel(_PlanBase):
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    description: str = Field(min_length=1)
    visual_prompt: str = Field(min_length=1)


class StorySceneModel(_PlanBase):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    visual_prompt: str = Field(min_length=1)


class StoryShotModel(_PlanBase):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    characters: list[str] = Field(min_length=1)
    scene: str = Field(min_length=1)
    camera_motion: str = Field(min_length=1)
    visual_prompt: str = Field(min_length=1)
    negative_prompt: str = Field(min_length=1)
    audio_prompt: str = Field(min_length=1)

    @field_validator("characters")
    @classmethod
    def _clean_characters(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        if not cleaned:
            raise ValueError("characters must contain at least one non-empty name")
        return cleaned


class StoryPlanModel(_PlanBase):
    summary: str = Field(min_length=1)
    worldview: str = Field(min_length=1)
    style_tags: list[str] = Field(min_length=1)
    themes: list[str] = Field(min_length=1)
    characters: list[StoryCharacterModel] = Field(min_length=1)
    scenes: list[StorySceneModel] = Field(min_length=1)
    shots: list[StoryShotModel] = Field(min_length=1)

    @field_validator("style_tags", "themes")
    @classmethod
    def _clean_string_list(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        if not cleaned:
            raise ValueError("list must contain at least one non-empty string")
        return cleaned


def live_llm_available() -> bool:
    return bool(os.getenv("DEEPSEEK_API_KEY") or os.getenv("SILICONFLOW_API_KEY"))


def generate_story_plan(payload: ProjectCreate, shot_count: int) -> dict:
    config = _llm_config()
    route = _routing_mode(payload.source_text)
    # 短文本直接规划；长文本先分块摘要，再合成故事圣经和分镜。
    if route == "direct":
        return _call_storyboard_llm(config, payload, shot_count, payload.source_text, "direct")
    return _call_map_reduce_storyboard(config, payload, shot_count, route)


def rewrite_video_prompt_for_safety(shot_payload: dict[str, Any], error_context: str = "") -> dict:
    try:
        config = _llm_config()
    except ProviderError:
        return _fallback_safe_video_rewrite(shot_payload, error_context)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a video prompt safety adapter for VisionCraft. Rewrite a failed video generation prompt "
                "into a safer original cinematic prompt. Keep the narrative beat, camera direction, mood, and "
                "character continuity. Remove copyrighted titles, famous works, lyrics, celebrity likeness, exact "
                "calligraphy/text reproduction, brand names, and sensitive or policy-risky phrasing. Return JSON only."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "error_context": error_context,
                    "shot": {
                        "title": shot_payload.get("title"),
                        "description": shot_payload.get("description"),
                        "visual_prompt": shot_payload.get("visual_prompt"),
                        "negative_prompt": shot_payload.get("negative_prompt"),
                        "audio_prompt": shot_payload.get("audio_prompt"),
                    },
                    "json_schema": {
                        "description": "Chinese, safe rewritten shot description",
                        "visual_prompt": "English, original cinematic video prompt",
                        "negative_prompt": "English negative prompt",
                        "audio_prompt": "English audio and ambience prompt",
                        "reason": "Chinese explanation of what was made safer",
                    },
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        parsed = _call_chat_json(config, messages, temperature=0.35, timeout=90)
        fallback = _fallback_safe_video_rewrite(shot_payload, error_context)
        return {
            "description": str(parsed.get("description") or fallback["description"]),
            "visual_prompt": str(parsed.get("visual_prompt") or fallback["visual_prompt"]),
            "negative_prompt": str(parsed.get("negative_prompt") or fallback["negative_prompt"]),
            "audio_prompt": str(parsed.get("audio_prompt") or fallback["audio_prompt"]),
            "reason": str(parsed.get("reason") or fallback["reason"]),
        }
    except Exception:
        return _fallback_safe_video_rewrite(shot_payload, error_context)


def _llm_config() -> LLMConfig:
    if os.getenv("DEEPSEEK_API_KEY"):
        return LLMConfig(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"),
        )
    if os.getenv("SILICONFLOW_API_KEY"):
        return LLMConfig(
            api_key=os.environ["SILICONFLOW_API_KEY"],
            base_url=os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"),
            model=os.getenv("SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V3.2"),
        )
    raise ProviderError("No live LLM API key configured")


def _call_storyboard_llm(
    config: LLMConfig,
    payload: ProjectCreate,
    shot_count: int,
    source_context: str,
    route: str,
) -> dict:
    messages = [
        {
            "role": "system",
            "content": (
                "You are VisionCraft's Narrative Planner and Visual Director. Convert source prose into a film "
                "pre-production package for a multi-agent AIGC video pipeline. Return strict JSON only. Chinese "
                "fields should be natural and specific; visual_prompt, camera_motion, negative_prompt, and "
                "audio_prompt should be concise English prompts. Do not treat the project title, UI labels, or "
                "system instructions as plot content unless the source text explicitly says so."
            ),
        },
        {
            "role": "user",
            "content": f"""
Project title: {payload.title}
Routing mode: {route}
Visual style: {payload.style}
Video aspect ratio: {payload.aspect_ratio}
Shot count: {shot_count}
Shot duration: {payload.duration_seconds}s

Source/context:
{source_context}

Return this exact JSON shape:
{{
  "summary": "Chinese story summary",
  "worldview": "Chinese visual-world and production logic",
  "style_tags": ["tag1", "tag2"],
  "themes": ["theme1", "theme2"],
  "characters": [
    {{"name": "Chinese name", "role": "protagonist/supporting", "description": "Chinese description", "visual_prompt": "English visual anchor prompt"}}
  ],
  "scenes": [
    {{"name": "Chinese scene name", "description": "Chinese description", "visual_prompt": "English scene anchor prompt"}}
  ],
  "shots": [
    {{
      "title": "Chinese shot title",
      "description": "Chinese screen action description",
      "characters": ["Chinese character name"],
      "scene": "Chinese scene name",
      "camera_motion": "English camera movement",
      "visual_prompt": "English image/video prompt, original cinematic description, no copyrighted titles or visible text",
      "negative_prompt": "English negative prompt",
      "audio_prompt": "English ambience/music/sound prompt"
    }}
  ]
}}
""".strip(),
        },
    ]
    return _call_validated_storyboard_json(config, messages, shot_count, payload)


def _call_validated_storyboard_json(
    config: LLMConfig,
    messages: list[dict[str, str]],
    shot_count: int,
    payload: ProjectCreate,
) -> dict:
    max_retries = _plan_validation_max_retries()
    attempts = max_retries + 1
    last_error = ""
    last_parsed: dict[str, Any] | None = None
    current_messages = list(messages)
    for attempt in range(1, attempts + 1):
        try:
            parsed = _call_chat_json(config, current_messages, temperature=0.65, timeout=120)
            last_parsed = parsed
            return _validate_story_plan(parsed, shot_count, payload, attempt)
        except Exception as exc:
            last_error = _validation_error_summary(exc)
            if attempt < attempts:
                current_messages = _append_story_plan_repair_message(current_messages, shot_count, last_error)
                continue
            if last_parsed is not None:
                coerced = _coerce_story_plan(last_parsed, shot_count, payload)
                coerced["_validation"] = {
                    "schema": "StoryPlanModel",
                    "pydantic": "v2",
                    "status": "coerced_after_validation_failure",
                    "attempts": attempt,
                    "last_error": last_error,
                }
                return coerced
            raise ProviderError(f"LLM story plan JSON validation failed: {last_error}") from exc
    raise ProviderError(f"LLM story plan JSON validation failed: {last_error}")


def _validate_story_plan(plan: dict[str, Any], shot_count: int, payload: ProjectCreate, attempt: int) -> dict:
    validated = StoryPlanModel.model_validate(plan or {})
    if len(validated.shots) != shot_count:
        raise ValueError(f"shots length {len(validated.shots)} does not match expected shot_count {shot_count}")
    result = _coerce_story_plan(validated.model_dump(), shot_count, payload)
    result["_validation"] = {
        "schema": "StoryPlanModel",
        "pydantic": "v2",
        "status": "validated",
        "attempts": attempt,
        "last_error": "",
    }
    return result


def _append_story_plan_repair_message(
    messages: list[dict[str, str]],
    shot_count: int,
    error_summary: str,
) -> list[dict[str, str]]:
    repair_payload = {
        "task": "Repair the previous story plan JSON. Return JSON only.",
        "expected_shot_count": shot_count,
        "validation_error": error_summary[:800],
        "rules": [
            "Keep the exact top-level keys from the requested schema.",
            "Fill every required string field with non-empty content.",
            "Return exactly expected_shot_count shots.",
            "Each shot.characters must be a non-empty array of character names.",
            "Do not add markdown fences or explanatory text.",
        ],
    }
    return messages + [{"role": "user", "content": json.dumps(repair_payload, ensure_ascii=False)}]


def _validation_error_summary(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        messages = []
        for error in exc.errors()[:8]:
            location = ".".join(str(part) for part in error.get("loc", ()))
            messages.append(f"{location}: {error.get('msg')}")
        return "; ".join(messages)[:800]
    return str(exc)[:800]


def _plan_validation_max_retries() -> int:
    try:
        return max(0, min(5, int(os.getenv("PLAN_VALIDATION_MAX_RETRIES", "2"))))
    except ValueError:
        return 2


def _call_map_reduce_storyboard(config: LLMConfig, payload: ProjectCreate, shot_count: int, route: str) -> dict:
    chunks = _chunk_source_text(payload.source_text)
    if not chunks:
        return _call_storyboard_llm(config, payload, shot_count, payload.source_text, route)

    anchor_indices = _anchor_chunk_indices(len(chunks), route)
    summaries = []
    for index, chunk in enumerate(chunks):
    # 长篇文本只精选锚点分块调用模型，其余分块用本地摘要保持覆盖。
        if index in anchor_indices:
            summaries.append(_summarize_chunk_with_llm(config, payload, index + 1, len(chunks), chunk))
        else:
            summaries.append(_local_chunk_summary(index + 1, len(chunks), chunk))

    compact_context = {
        "route": route,
        "source_length": len(payload.source_text),
        "chunk_count": len(chunks),
        "summaries": summaries,
    }
    context_text = (
        "Map-Reduce story bible input. Use the summaries as the authoritative source. "
        "For epic/rag route, preserve the main arc and let later RAG memory recover local details.\n"
        + json.dumps(compact_context, ensure_ascii=False)
    )
    return _call_storyboard_llm(config, payload, shot_count, context_text, route)


def _summarize_chunk_with_llm(config: LLMConfig, payload: ProjectCreate, index: int, total: int, chunk: str) -> dict:
    messages = [
        {
            "role": "system",
            "content": (
                "Summarize one source chunk for a film adaptation pipeline. Return JSON only. Extract narrative beats, "
                "characters, places, visual motifs, and continuity constraints. Avoid inventing events."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "project_title": payload.title,
                    "chunk_index": index,
                    "chunk_total": total,
                    "source_chunk": chunk,
                    "json_schema": {
                        "chunk_index": index,
                        "summary": "Chinese concise summary",
                        "key_events": ["Chinese event"],
                        "characters": ["Chinese character or role"],
                        "scenes": ["Chinese place"],
                        "visual_motifs": ["English or Chinese visual motif"],
                    },
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        parsed = _call_chat_json(config, messages, temperature=0.35, timeout=90)
        return {
            "chunk_index": index,
            "summary": str(parsed.get("summary") or ""),
            "key_events": _as_list(parsed.get("key_events")),
            "characters": _as_list(parsed.get("characters")),
            "scenes": _as_list(parsed.get("scenes")),
            "visual_motifs": _as_list(parsed.get("visual_motifs")),
        }
    except Exception:
        return _local_chunk_summary(index, total, chunk)


def _call_chat_json(config: LLMConfig, messages: list[dict[str, str]], temperature: float, timeout: int) -> dict:
    data = {
        "model": config.model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    url = config.base_url.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ProviderError(f"LLM HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc

    parsed = json.loads(body)
    content = parsed["choices"][0]["message"]["content"]
    return _parse_json_content(content)


def _parse_json_content(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _coerce_story_plan(plan: dict[str, Any], shot_count: int, payload: ProjectCreate) -> dict:
    fallback_character = {
        "name": "主角",
        "role": "protagonist",
        "description": "推动故事行动和情绪变化的核心人物。",
        "visual_prompt": f"main character, consistent costume, {payload.style}, cinematic realism",
    }
    fallback_scene = {
        "name": "主要场景",
        "description": "承载核心情节的主要空间。",
        "visual_prompt": f"main story location, {payload.style}, cinematic lighting",
    }
    plan = dict(plan or {})
    plan["summary"] = str(plan.get("summary") or f"《{payload.title}》被改编为连续的电影分镜。")
    plan["worldview"] = str(plan.get("worldview") or f"视觉风格采用 {payload.style}，强调人物一致性和叙事连贯。")
    plan["style_tags"] = _as_list(plan.get("style_tags")) or [payload.style, "cinematic", "consistent visual anchors"]
    plan["themes"] = _as_list(plan.get("themes")) or ["选择", "记忆", "成长"]
    plan["characters"] = [_coerce_character(item, fallback_character) for item in _as_list(plan.get("characters"))] or [
        fallback_character
    ]
    plan["scenes"] = [_coerce_scene(item, fallback_scene) for item in _as_list(plan.get("scenes"))] or [fallback_scene]

    raw_shots = [_coerce_shot(item, index, payload, plan) for index, item in enumerate(_as_list(plan.get("shots")), start=1)]
    while len(raw_shots) < shot_count:
        raw_shots.append(_fallback_shot(len(raw_shots) + 1, shot_count, payload, plan))
    plan["shots"] = raw_shots[:shot_count]
    return plan


def _coerce_character(item: Any, fallback: dict) -> dict:
    data = item if isinstance(item, dict) else {}
    return {
        "name": str(data.get("name") or fallback["name"]),
        "role": str(data.get("role") or fallback["role"]),
        "description": str(data.get("description") or fallback["description"]),
        "visual_prompt": str(data.get("visual_prompt") or fallback["visual_prompt"]),
    }


def _coerce_scene(item: Any, fallback: dict) -> dict:
    data = item if isinstance(item, dict) else {}
    return {
        "name": str(data.get("name") or fallback["name"]),
        "description": str(data.get("description") or fallback["description"]),
        "visual_prompt": str(data.get("visual_prompt") or fallback["visual_prompt"]),
    }


def _coerce_shot(item: Any, index: int, payload: ProjectCreate, plan: dict) -> dict:
    data = item if isinstance(item, dict) else {}
    character_names = [character["name"] for character in plan["characters"][:1]]
    scene_name = plan["scenes"][(index - 1) % len(plan["scenes"])]["name"]
    return {
        "title": str(data.get("title") or f"镜头 {index}"),
        "description": str(data.get("description") or f"第 {index} 个叙事节点推进人物行动和情绪变化。"),
        "characters": _as_list(data.get("characters")) or character_names,
        "scene": str(data.get("scene") or scene_name),
        "camera_motion": str(data.get("camera_motion") or "slow cinematic push in"),
        "visual_prompt": str(
            data.get("visual_prompt")
            or f"{payload.style}, shot {index}, {scene_name}, consistent character design, original cinematic scene"
        ),
        "negative_prompt": str(
            data.get("negative_prompt")
            or "low quality, inconsistent face, broken hands, visible text, watermark, logo"
        ),
        "audio_prompt": str(data.get("audio_prompt") or "subtle cinematic ambience, natural room tone"),
    }


def _fallback_shot(index: int, total: int, payload: ProjectCreate, plan: dict) -> dict:
    characters = [character["name"] for character in plan["characters"][:1]]
    scene = plan["scenes"][(index - 1) % len(plan["scenes"])]["name"]
    phase = ["开端", "推进", "转折", "抉择", "余韵"][(min(index, 5) - 1)]
    return {
        "title": f"{phase} {index}",
        "description": f"第 {index}/{total} 个镜头承接原文情绪，推进人物行动并保持视觉连续。",
        "characters": characters,
        "scene": scene,
        "camera_motion": "controlled cinematic camera movement",
        "visual_prompt": f"{payload.style}, original cinematic scene, {scene}, coherent action, consistent character anchors",
        "negative_prompt": "low quality, inconsistent face, broken anatomy, visible text, watermark, logo",
        "audio_prompt": "subtle ambience, controlled emotional rhythm, cinematic sound bed",
    }


def _chunk_source_text(text: str, size: int = 2600, overlap: int = 260) -> list[str]:
    compact = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if not compact:
        return []
    chunks = []
    step = max(1, size - overlap)
    for start in range(0, len(compact), step):
        chunk = compact[start : start + size]
        if chunk:
            chunks.append(chunk)
    return chunks


def _anchor_chunk_indices(total: int, route: str) -> set[int]:
    if total <= 12:
        return set(range(total))
    anchors = {0, total - 1, total // 2}
    live_budget = 10 if route == "rag" else 14
    for index in range(total):
        if len(anchors) >= live_budget:
            break
        anchors.add(index)
    return anchors


def _local_chunk_summary(index: int, total: int, chunk: str) -> dict:
    compact = " ".join(chunk.split())
    return {
        "chunk_index": index,
        "summary": f"第 {index}/{total} 段原文摘要：{compact[:220]}",
        "key_events": [compact[:120]] if compact else [],
        "characters": [],
        "scenes": [],
        "visual_motifs": [],
    }


def _fallback_safe_video_rewrite(shot_payload: dict[str, Any], error_context: str = "") -> dict:
    # LLM 改写失败时使用本地兜底，去掉常见风险词并保留镜头动作。
    description = _strip_policy_risky_text(str(shot_payload.get("description") or shot_payload.get("title") or "当前镜头"))
    visual_seed = _strip_policy_risky_text(str(shot_payload.get("visual_prompt") or "cinematic scene"))
    negative = str(shot_payload.get("negative_prompt") or "")
    return {
        "description": f"原创化安全改写：{description[:180]}",
        "visual_prompt": (
            "original cinematic scene, unnamed characters, expressive body language, atmospheric environment, "
            f"{visual_seed[:420]}, no visible text, fully original details"
        ),
        "negative_prompt": (
            f"{negative}, copyrighted characters, famous artwork, celebrity likeness, exact lyrics, visible text, "
            "calligraphy reproduction, logos, watermark, sensitive content"
        ).strip(", "),
        "audio_prompt": str(shot_payload.get("audio_prompt") or "subtle cinematic ambience, natural sound design"),
        "reason": f"已去除可能触发版权/内容安全的命名实体和文字复现风险。{error_context[:160]}",
    }


def _strip_policy_risky_text(text: str) -> str:
    text = re.sub(r"《[^》]{1,80}》", "某个记忆载体", text)
    text = re.sub(r"“[^”]{1,80}”", "含蓄的情绪线索", text)
    text = re.sub(r'"[^"]{1,80}"', "subtle emotional clue", text)
    risky_terms = [
        "兰亭序",
        "真迹",
        "摹本",
        "朱砂",
        "歌词",
        "明星",
        "名画",
        "版权",
        "IP",
        "品牌",
        "商标",
        "exact famous calligraphy reproduction",
        "famous calligraphy",
        "famous artwork",
        "named artwork",
        "copyrighted",
        "celebrity",
        "lyrics",
        "logo",
        "brand",
        "trademark",
        "red seal",
    ]
    for term in risky_terms:
        text = re.sub(re.escape(term), "original visual element", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _routing_mode(text: str) -> str:
    length = len(text)
    if length < 5000:
        return "direct"
    if length < 30000:
        return "chunk"
    return "rag"


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]
