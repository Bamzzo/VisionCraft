from dataclasses import dataclass
import base64
import json
import os
from pathlib import Path
import uuid
import urllib.error
import urllib.request

from ..database import connect, utc_now
from ..services.asset_service import create_placeholder_svg, project_asset_dir, public_asset_path


@dataclass
class ImageAssetRequest:
    project_id: str
    asset_type: str
    name: str
    description: str
    prompt: str
    accent: str


def generate_image_asset(request: ImageAssetRequest) -> str:
    """Generate and persist an image asset.

    Current implementation uses the local SVG mock renderer. Real providers
    should keep this function signature and return an `assets.id` value after
    storing the generated file under the project asset directory.
    """

    fallback_reason = "No live image provider configured"
    provider = os.getenv("VISIONCRAFT_IMAGE_PROVIDER", "siliconflow").lower()
    providers = [provider] if provider in {"siliconflow", "ark", "volc"} else ["siliconflow", "ark"]
    if provider == "siliconflow":
        providers.append("ark")

    for candidate in dict.fromkeys(providers):
        try:
            if candidate == "siliconflow" and os.getenv("SILICONFLOW_API_KEY"):
                return _generate_siliconflow_image(request)
            if candidate in {"ark", "volc"} and _ark_api_key():
                return _generate_ark_image(request)
        except Exception as exc:
            # Keep the workflow demo-safe, but record the reason so the UI can
            # distinguish real model output from a local placeholder.
            fallback_reason = _compact_error(exc)

    return create_placeholder_svg(
        request.project_id,
        request.asset_type,
        request.name,
        f"{request.description}\nFallback reason: {fallback_reason}",
        request.prompt,
        request.accent,
        f"fallback:image:{fallback_reason}",
    )


def save_binary_image(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _generate_siliconflow_image(request: ImageAssetRequest) -> str:
    api_key = os.environ["SILICONFLOW_API_KEY"]
    base_url = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
    model = os.getenv("SILICONFLOW_IMAGE_MODEL", "Qwen/Qwen-Image")
    image_size = os.getenv("SILICONFLOW_IMAGE_SIZE", "1024x576")
    payload = {
        "model": model,
        "prompt": _build_image_prompt(request),
        "image_size": image_size,
    }
    http_request = urllib.request.Request(
        base_url + "/images/generations",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(http_request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"SiliconFlow image HTTP {exc.code}: {detail}") from exc

    image_url = body["images"][0]["url"]
    image_request = urllib.request.Request(image_url, method="GET")
    with urllib.request.urlopen(image_request, timeout=120) as image_response:
        content = image_response.read()
        content_type = image_response.headers.get("Content-Type", "")

    suffix = ".png"
    if "jpeg" in content_type or "jpg" in content_type:
        suffix = ".jpg"
    elif "webp" in content_type:
        suffix = ".webp"

    asset_id = f"asset_{uuid.uuid4().hex[:10]}"
    filename = f"{asset_id}{suffix}"
    file_path = project_asset_dir(request.project_id) / filename
    save_binary_image(file_path, content)

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                request.project_id,
                request.asset_type,
                request.name,
                request.description,
                request.prompt,
                public_asset_path(request.project_id, filename),
                f"provider:siliconflow:{model}",
                utc_now(),
            ),
        )
    return asset_id


def _generate_ark_image(request: ImageAssetRequest) -> str:
    api_key = _ark_api_key()
    if not api_key:
        raise RuntimeError("No Ark image API key configured")
    base_url = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    model = os.getenv("VOLC_IMAGE_MODEL") or os.getenv("DOUBAO_IMAGE_ENDPOINT", "doubao-seedream-5-0-260128")
    size = os.getenv("VOLC_IMAGE_SIZE", "2K")
    payload = {
        "model": model,
        "prompt": _build_image_prompt(request),
        "size": size,
        "response_format": "b64_json",
        "watermark": False,
    }
    http_request = urllib.request.Request(
        base_url + "/images/generations",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(http_request, timeout=180) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ark image HTTP {exc.code}: {detail}") from exc

    item = (body.get("data") or [{}])[0]
    content = None
    suffix = ".png"
    if item.get("b64_json"):
        content = base64.b64decode(item["b64_json"])
    elif item.get("url"):
        image_request = urllib.request.Request(item["url"], method="GET")
        with urllib.request.urlopen(image_request, timeout=180) as image_response:
            content = image_response.read()
            content_type = image_response.headers.get("Content-Type", "")
        if "jpeg" in content_type or "jpg" in content_type:
            suffix = ".jpg"
        elif "webp" in content_type:
            suffix = ".webp"
    if not content:
        raise RuntimeError(f"Ark image returned no image data: {body}")

    asset_id = f"asset_{uuid.uuid4().hex[:10]}"
    filename = f"{asset_id}{suffix}"
    file_path = project_asset_dir(request.project_id) / filename
    save_binary_image(file_path, content)

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                request.project_id,
                request.asset_type,
                request.name,
                request.description,
                request.prompt,
                public_asset_path(request.project_id, filename),
                f"provider:ark:{model}",
                utc_now(),
            ),
        )
    return asset_id


def _build_image_prompt(request: ImageAssetRequest) -> str:
    return (
        f"{request.prompt}. {request.description}. "
        "cinematic production still, clean composition, coherent anatomy, "
        "high quality, no text, no watermark, no UI overlay"
    )


def _compact_error(error: Exception) -> str:
    message = " ".join(str(error).replace("\n", " ").split())
    return message[:280] or error.__class__.__name__


def _ark_api_key() -> str:
    return os.getenv("VOLC_IMAGE_API_KEY") or os.getenv("VOLC_API_KEY") or ""
