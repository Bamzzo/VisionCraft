"""Text LLM request construction and injectable transport.

Live HTTP is blocked unless VISIONCRAFT_ALLOW_LIVE_LLM=1. Tests inject a
transport; they never open a network socket.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .live_budget import TEXT_MAX_TOKENS, THINKING_DISABLED, public_request_plan
from .llm_catalog import DEEPSEEK_CHAT_URL, DEEPSEEK_FLASH, live_llm_authorized

_JSON_FENCE_RE = re.compile(r"^```(?:json)?", re.I)


class ProviderError(RuntimeError):
    pass


class LiveCallNotAuthorized(ProviderError):
    def __init__(self, message: str = "真实 LLM 调用尚未授权。请确认 Provider、模型、次数、参数和预算后再开启。") -> None:
        super().__init__(message)


class JsonParseError(ProviderError):
    def __init__(self, message: str, snippet: str = "") -> None:
        super().__init__(message)
        self.snippet = snippet


class ChatTransport(Protocol):
    def send(self, prepared: "PreparedChatRequest") -> dict: ...


@dataclass
class PreparedChatRequest:
    provider: str
    model: str
    url: str
    body: dict
    timeout: int = 120
    metadata: dict = field(default_factory=dict)

    def public_metadata(self) -> dict:
        messages = self.body.get("messages") or []
        text_chars = 0
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                text_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_chars += len(str(block.get("text") or ""))
        thinking = self.body.get("thinking") or {}
        return {
            "provider": self.provider,
            "model": self.model,
            "endpoint": self.url,
            "message_count": len(messages),
            "prompt_chars": text_chars,
            "max_tokens": self.body.get("max_tokens"),
            "thinking": thinking.get("type") or "disabled",
            "response_format": (self.body.get("response_format") or {}).get("type"),
            "has_images": False,
            "kind": "text",
        }


_transport_override: ChatTransport | None = None
_sent_requests: list[PreparedChatRequest] = []


def set_chat_transport(transport: ChatTransport | None) -> None:
    global _transport_override
    _transport_override = transport


def reset_chat_transport() -> None:
    global _transport_override
    _transport_override = None
    _sent_requests.clear()


def captured_requests() -> list[PreparedChatRequest]:
    return list(_sent_requests)


def build_text_request(
    *,
    provider: str,
    model: str,
    messages: list[dict],
    temperature: float = 0.4,
    timeout: int = 120,
    extra_body: dict | None = None,
) -> PreparedChatRequest:
    if any(_message_has_image(item) for item in messages):
        raise ProviderError("文本适配器不能携带图片。请使用视觉适配器。")
    body = {
        "model": model or DEEPSEEK_FLASH,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "max_tokens": TEXT_MAX_TOKENS,
        "thinking": dict(THINKING_DISABLED),
    }
    if extra_body:
        extra = dict(extra_body)
        extra.pop("thinking", None)
        requested = extra.pop("max_tokens", TEXT_MAX_TOKENS)
        try:
            requested_tokens = int(requested)
        except (TypeError, ValueError):
            requested_tokens = TEXT_MAX_TOKENS
        body.update(extra)
        body["max_tokens"] = max(1, min(requested_tokens, TEXT_MAX_TOKENS))
        body["thinking"] = dict(THINKING_DISABLED)
    url = _chat_url(provider)
    prompt_chars = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            prompt_chars += len(content)
    plan = public_request_plan(
        provider=provider,
        model=body["model"],
        kind="text",
        prompt_chars=prompt_chars,
        max_tokens=body["max_tokens"],
    )
    return PreparedChatRequest(
        provider=provider,
        model=body["model"],
        url=url,
        body=body,
        timeout=timeout,
        metadata=plan,
    )


def complete_json(
    prepared: PreparedChatRequest,
    *,
    transport: ChatTransport | None = None,
) -> dict:
    raw = send_chat(prepared, transport=transport)
    content = _message_content(raw)
    return parse_json_content(content)


def send_chat(
    prepared: PreparedChatRequest,
    *,
    transport: ChatTransport | None = None,
) -> dict:
    chosen = transport or _transport_override or _default_transport()
    _sent_requests.append(prepared)
    return chosen.send(prepared)


def parse_json_content(content: str) -> dict:
    text = (content or "").strip()
    if text.startswith("```"):
        text = _JSON_FENCE_RE.sub("", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise JsonParseError("模型返回的 JSON 无法解析。", _snippet(text)) from exc
        else:
            raise JsonParseError("模型未返回 JSON 对象。", _snippet(text)) from None
    if not isinstance(parsed, dict):
        raise JsonParseError("模型 JSON 根节点必须是对象。", _snippet(text))
    return parsed


def adaptation_messages(title: str, source_text: str, style: str, duration_seconds: int) -> list[dict]:
    excerpt_budget = min(len(source_text), 4000)
    return [
        {
            "role": "system",
            "content": (
                "You are VisionCraft's adaptation planner. Return JSON only. "
                "Chinese fields must be specific. source_excerpt must be an exact substring of the source. "
                "Do not invent plot that is absent from the source."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "title": title,
                    "style": style,
                    "duration_seconds": duration_seconds,
                    "source_text": source_text[:excerpt_budget],
                    "json_schema": {
                        "options": [
                            {
                                "title": "Chinese option title",
                                "rationale": "Chinese rationale",
                                "protagonist_goal": "Chinese goal",
                                "conflict": "Chinese conflict",
                                "ending_orientation": "Chinese ending",
                                "suggested_duration_seconds": 30,
                                "suggested_shot_count": 5,
                                "source_excerpt": "exact source substring",
                            }
                        ]
                    },
                },
                ensure_ascii=False,
            ),
        },
    ]


def story_bible_messages(title: str, source_text: str, style: str, option: dict) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "You are VisionCraft's Story Bible editor. Return JSON only. "
                "Keep characters and scenes consistent with the selected adaptation option."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "title": title,
                    "style": style,
                    "option": {
                        "title": option.get("title"),
                        "conflict": option.get("conflict"),
                        "protagonist_goal": option.get("protagonist_goal"),
                        "ending_orientation": option.get("ending_orientation"),
                        "source_excerpt": option.get("source_excerpt"),
                    },
                    "source_text": (source_text or "")[:4000],
                    "json_schema": {
                        "logline": "Chinese logline",
                        "adaptation_summary": "Chinese summary",
                        "emotion_curve": "Chinese emotion curve",
                        "protagonist": "name",
                        "protagonist_goal": "goal",
                        "obstacle": "obstacle",
                        "visual_style": "style",
                        "consistency_constraints": "constraints",
                        "themes": ["theme"],
                        "style_tags": ["tag"],
                        "character_cards": [{"name": "", "identity": "", "appearance": "", "motivation": "", "invariant": ""}],
                        "scene_cards": [{"name": "", "environment": "", "time": "", "visuals": "", "invariant": ""}],
                    },
                },
                ensure_ascii=False,
            ),
        },
    ]


def storyboard_messages(title: str, source_text: str, style: str, option: dict, bible: dict) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "You are VisionCraft's storyboard designer. Return JSON only. "
                "Each shot.source_excerpt must be an exact substring of the source."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "title": title,
                    "style": style,
                    "shot_count": option.get("suggested_shot_count") or 5,
                    "duration_seconds": option.get("suggested_duration_seconds") or 30,
                    "option_title": option.get("title"),
                    "bible": {
                        "protagonist": bible.get("protagonist"),
                        "visual_style": bible.get("visual_style"),
                        "character_cards": (bible.get("character_cards") or [])[:6],
                        "scene_cards": (bible.get("scene_cards") or [])[:6],
                    },
                    "source_text": (source_text or "")[:4000],
                    "json_schema": {
                        "shots": [
                            {
                                "title": "Chinese title",
                                "narrative_purpose": "purpose",
                                "characters": ["name"],
                                "scene": "scene",
                                "action_text": "action",
                                "camera_motion": "English camera",
                                "duration_seconds": 5,
                                "visual_prompt": "English visual prompt",
                                "source_excerpt": "exact source substring",
                            }
                        ]
                    },
                },
                ensure_ascii=False,
            ),
        },
    ]


def coerce_adaptation_options(parsed: dict, fallback: list[dict], source_text: str) -> list[dict]:
    raw = parsed.get("options") if isinstance(parsed, dict) else None
    if not isinstance(raw, list) or not raw:
        raise JsonParseError("改编方案 JSON 缺少 options 数组。", _snippet(json.dumps(parsed, ensure_ascii=False)[:200]))
    result = []
    for index, item in enumerate(raw[:3], start=1):
        data = item if isinstance(item, dict) else {}
        seed = fallback[index - 1] if index - 1 < len(fallback) else (fallback[0] if fallback else {})
        excerpt = str(data.get("source_excerpt") or seed.get("source_excerpt") or "")
        if excerpt and excerpt not in source_text:
            excerpt = seed.get("source_excerpt") or ""
        result.append(
            {
                "option_index": index,
                "title": str(data.get("title") or seed.get("title") or f"改编方案 {index}"),
                "rationale": str(data.get("rationale") or seed.get("rationale") or ""),
                "protagonist_goal": str(data.get("protagonist_goal") or seed.get("protagonist_goal") or ""),
                "conflict": str(data.get("conflict") or seed.get("conflict") or ""),
                "ending_orientation": str(data.get("ending_orientation") or seed.get("ending_orientation") or ""),
                "suggested_duration_seconds": int(data.get("suggested_duration_seconds") or seed.get("suggested_duration_seconds") or 30),
                "suggested_shot_count": int(data.get("suggested_shot_count") or seed.get("suggested_shot_count") or 5),
                "source_excerpt": excerpt,
                "source_start": seed.get("source_start"),
                "source_end": seed.get("source_end"),
                "source": "live_llm",
            }
        )
    if not result:
        raise JsonParseError("改编方案 JSON 没有可用条目。")
    return result


def coerce_story_bible(parsed: dict, fallback: dict) -> dict:
    data = parsed if isinstance(parsed, dict) else {}
    merged = dict(fallback)
    for key in (
        "logline",
        "adaptation_summary",
        "summary",
        "worldview",
        "emotion_curve",
        "protagonist",
        "protagonist_goal",
        "obstacle",
        "visual_style",
        "consistency_constraints",
    ):
        if data.get(key):
            merged[key] = str(data[key])
    if isinstance(data.get("themes"), list) and data["themes"]:
        merged["themes"] = [str(item) for item in data["themes"]]
    if isinstance(data.get("style_tags"), list) and data["style_tags"]:
        merged["style_tags"] = [str(item) for item in data["style_tags"]]
    if isinstance(data.get("character_cards"), list) and data["character_cards"]:
        merged["character_cards"] = data["character_cards"]
    if isinstance(data.get("scene_cards"), list) and data["scene_cards"]:
        merged["scene_cards"] = data["scene_cards"]
    merged["source"] = "live_llm"
    return merged


def coerce_storyboard(parsed: dict, fallback: list[dict], source_text: str) -> list[dict]:
    raw = parsed.get("shots") if isinstance(parsed, dict) else None
    if not isinstance(raw, list) or not raw:
        raise JsonParseError("分镜 JSON 缺少 shots 数组。")
    result = []
    for index, item in enumerate(raw, start=1):
        data = item if isinstance(item, dict) else {}
        seed = fallback[index - 1] if index - 1 < len(fallback) else (fallback[-1] if fallback else {})
        excerpt = str(data.get("source_excerpt") or seed.get("source_excerpt") or "")
        if excerpt and excerpt not in source_text:
            excerpt = seed.get("source_excerpt") or ""
        characters = data.get("characters") if isinstance(data.get("characters"), list) else seed.get("characters") or []
        result.append(
            {
                "shot_index": index,
                "title": str(data.get("title") or seed.get("title") or f"镜头 {index}"),
                "narrative_purpose": str(data.get("narrative_purpose") or seed.get("narrative_purpose") or ""),
                "characters": [str(name) for name in characters],
                "scene": str(data.get("scene") or seed.get("scene") or ""),
                "action_text": str(data.get("action_text") or seed.get("action_text") or ""),
                "camera_motion": str(data.get("camera_motion") or seed.get("camera_motion") or ""),
                "duration_seconds": int(data.get("duration_seconds") or seed.get("duration_seconds") or 5),
                "visual_prompt": str(data.get("visual_prompt") or seed.get("visual_prompt") or ""),
                "bible_character": data.get("bible_character") or seed.get("bible_character"),
                "bible_scene": data.get("bible_scene") or seed.get("bible_scene"),
                "source_excerpt": excerpt,
                "source_start": seed.get("source_start"),
                "source_end": seed.get("source_end"),
            }
        )
    return result or fallback


class BlockedLiveTransport:
    def send(self, prepared: PreparedChatRequest) -> dict:
        raise LiveCallNotAuthorized()


class HttpChatTransport:
    def send(self, prepared: PreparedChatRequest) -> dict:
        if not live_llm_authorized():
            raise LiveCallNotAuthorized()
        api_key = os.getenv("DEEPSEEK_API_KEY") if prepared.provider == "deepseek" else os.getenv("SILICONFLOW_API_KEY")
        if not api_key:
            raise ProviderError("未配置该文本 Provider 的 API Key。")
        request = urllib.request.Request(
            prepared.url,
            data=json.dumps(prepared.body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=prepared.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = _safe_http_detail(exc)
            raise ProviderError(f"LLM HTTP {exc.code}: {detail}") from exc
        except LiveCallNotAuthorized:
            raise
        except Exception as exc:
            raise ProviderError("文本模型请求失败。") from exc


class FunctionTransport:
    def __init__(self, fn: Callable[[PreparedChatRequest], dict]) -> None:
        self.fn = fn

    def send(self, prepared: PreparedChatRequest) -> dict:
        return self.fn(prepared)


def _default_transport() -> ChatTransport:
    if _transport_override is not None:
        return _transport_override
    if live_llm_authorized():
        return HttpChatTransport()
    return BlockedLiveTransport()


def _chat_url(provider: str) -> str:
    if provider == "siliconflow":
        return os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/") + "/chat/completions"
    return os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/chat/completions"


def _message_has_image(message: dict) -> bool:
    content = message.get("content")
    if isinstance(content, list):
        return any(isinstance(block, dict) and block.get("type") in {"image_url", "image"} for block in content)
    return False


def _message_content(raw: dict) -> str:
    try:
        return str(raw["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("文本模型响应缺少 choices[0].message.content。") from exc


def _snippet(text: str, limit: int = 80) -> str:
    compact = " ".join((text or "").split())
    return compact[:limit]


def _safe_http_detail(exc: urllib.error.HTTPError) -> str:
    detail = exc.read().decode("utf-8", errors="replace")
    detail = re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]+", "Bearer <redacted>", detail)
    detail = re.sub(r"(?i)sk-[A-Za-z0-9._\-]+", "<redacted>", detail)
    detail = re.sub(r"data:[^,\s]+;base64,[A-Za-z0-9+/=]+", "<data-url-omitted>", detail)
    return detail[:240]
