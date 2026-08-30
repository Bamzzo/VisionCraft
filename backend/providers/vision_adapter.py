"""Vision request construction. Text adapters must not assemble image fields."""
from __future__ import annotations

import json
import os
import re
import struct
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from ..database import connect
from ..services.media_transfer_service import MediaTransferError, prepare_image_reference
from .llm_adapter import JsonParseError, LiveCallNotAuthorized, ProviderError, parse_json_content
from .llm_catalog import DEEPSEEK_VISION, live_vision_authorized

_DATA_URL_RE = re.compile(r"data:[^,\s]+;base64,[A-Za-z0-9+/=]+", re.I)


class VisionAdapterError(ProviderError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass
class PreparedVisionRequest:
    provider: str
    model: str
    url: str
    body: dict
    timeout: int = 90
    metadata: dict = field(default_factory=dict)

    def public_metadata(self) -> dict:
        return dict(self.metadata)


_transport_override = None
_sent_requests: list[PreparedVisionRequest] = []


def set_vision_transport(transport) -> None:
    global _transport_override
    _transport_override = transport


def reset_vision_transport() -> None:
    global _transport_override
    _transport_override = None
    _sent_requests.clear()


def captured_vision_requests() -> list[PreparedVisionRequest]:
    return list(_sent_requests)


def build_vision_request(
    *,
    project_id: str,
    public_path: str,
    prompt: str,
    provider: str,
    model: str,
    role: str = "keyframe",
    timeout: int = 90,
) -> PreparedVisionRequest:
    if not public_path:
        raise VisionAdapterError("ASSET_REQUIRED", "视觉检查需要项目内图片资产。")
    try:
        media = prepare_image_reference(
            project_id,
            public_path,
            target_provider=provider,
            target_model=model,
            role=role,
        )
    except MediaTransferError as exc:
        raise VisionAdapterError(exc.code, _chinese_media_error(exc)) from exc
    if media is None:
        raise VisionAdapterError("ASSET_REQUIRED", "视觉检查需要项目内图片资产。")
    if media.transfer_mode == "data_url" and not str(media.url).startswith("data:"):
        raise VisionAdapterError("INVALID_TRANSPORT", "Data URL 传输模式生成了无效引用。")
    if media.url.startswith("/") or ":\\" in media.url or media.url.startswith("file:"):
        raise VisionAdapterError("LOCAL_PATH_FORBIDDEN", "不能把本地绝对路径发给远程视觉模型。")

    width, height = _image_size_from_asset(media.asset_id)
    if width is None or height is None:
        width, height = _png_size_from_data_url(media.url)

    user_content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": media.url}},
    ]
    body = {
        "model": model or DEEPSEEK_VISION,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are VisionCraft's visual inspector. Return JSON only. "
                    "Describe the image, characters, wardrobe, props, and quality issues."
                ),
            },
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    metadata = {
        "asset_id": media.asset_id,
        "asset_role": role,
        "provider": provider,
        "model": body["model"],
        "transport_mode": media.transfer_mode,
        "mime_type": media.mime_type,
        "width": width,
        "height": height,
        "byte_size": media.byte_size,
        "request_id": None,
    }
    _assert_metadata_safe(metadata)
    return PreparedVisionRequest(
        provider=provider,
        model=body["model"],
        url=_chat_url(provider),
        body=body,
        timeout=timeout,
        metadata=metadata,
    )


def complete_vision_json(prepared: PreparedVisionRequest, *, transport=None) -> dict:
    raw = send_vision(prepared, transport=transport)
    request_id = None
    if isinstance(raw, dict):
        request_id = raw.get("id")
        prepared.metadata["request_id"] = request_id
    content = _message_content(raw)
    return parse_json_content(content)


def send_vision(prepared: PreparedVisionRequest, *, transport=None) -> dict:
    chosen = transport if transport is not None else _transport_override if _transport_override is not None else _default_transport()
    _sent_requests.append(prepared)
    return chosen.send(prepared)


def vision_prompt_for_keyframe(role: str = "keyframe") -> str:
    return (
        "Inspect this keyframe. Return JSON with keys: description, characters, wardrobe, "
        f"props, quality_notes, consistency_risks. Image role: {role}."
    )


class BlockedVisionTransport:
    def send(self, prepared: PreparedVisionRequest) -> dict:
        raise LiveCallNotAuthorized("真实视觉模型调用尚未授权。请确认 Provider、模型、次数、参数和预算后再开启。")


class HttpVisionTransport:
    def send(self, prepared: PreparedVisionRequest) -> dict:
        if not live_vision_authorized():
            raise LiveCallNotAuthorized("真实视觉模型调用尚未授权。请确认 Provider、模型、次数、参数和预算后再开启。")
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ProviderError("未配置 DeepSeek API Key，无法调用视觉模型。")
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
            detail = exc.read().decode("utf-8", errors="replace")
            detail = _DATA_URL_RE.sub("<data-url-omitted>", detail)
            detail = re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]+", "Bearer <redacted>", detail)
            raise ProviderError(f"视觉模型 HTTP {exc.code}: {detail[:240]}") from exc
        except LiveCallNotAuthorized:
            raise
        except Exception as exc:
            raise ProviderError("视觉模型请求失败。") from exc


def payload_contains_data_url(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return "data:image" in text or "base64," in text


def _default_transport():
    if _transport_override is not None:
        return _transport_override
    if live_vision_authorized():
        return HttpVisionTransport()
    return BlockedVisionTransport()


def _chat_url(provider: str) -> str:
    if provider == "siliconflow":
        return os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/") + "/chat/completions"
    return os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/chat/completions"


def _message_content(raw: dict) -> str:
    try:
        return str(raw["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("视觉模型响应缺少 choices[0].message.content。") from exc


def _image_size_from_asset(asset_id: str) -> tuple[int | None, int | None]:
    with connect() as conn:
        row = conn.execute("SELECT width, height FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not row:
        return None, None
    return row["width"], row["height"]


def _png_size_from_data_url(url: str) -> tuple[int | None, int | None]:
    if not url.startswith("data:image/png"):
        return None, None
    try:
        import base64

        header, encoded = url.split(",", 1)
        content = base64.b64decode(encoded, validate=False)
    except Exception:
        return None, None
    if len(content) < 24 or content[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    width, height = struct.unpack(">II", content[16:24])
    return int(width), int(height)


def _assert_metadata_safe(metadata: dict) -> None:
    blob = json.dumps(metadata, ensure_ascii=False)
    if "data:image" in blob or "base64," in blob.lower() or "sk-" in blob.lower():
        raise VisionAdapterError("UNSAFE_METADATA", "视觉元数据不能包含 Data URL、Base64 或密钥。")


def _chinese_media_error(exc: MediaTransferError) -> str:
    mapping = {
        "ASSET_NOT_FOUND": "所选图片不属于当前项目。",
        "INVALID_ASSET_PATH": "图片路径超出当前项目，已拒绝。",
        "ASSET_FILE_MISSING": "项目内图片元数据存在，但本地文件缺失。",
        "IMAGE_TOO_LARGE": str(exc),
        "UNSUPPORTED_IMAGE_FORMAT": str(exc),
    }
    return mapping.get(exc.code, str(exc))
