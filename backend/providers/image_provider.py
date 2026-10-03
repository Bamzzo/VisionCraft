from dataclasses import dataclass
import base64
import json
import os
import urllib.error
import urllib.request

from ..services.asset_service import create_placeholder_svg, persist_binary_asset
from .live_budget import (
    BudgetBlockedError,
    check_live_image_budget,
    live_image_authorized,
    record_live_image_use,
)


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

    Candidates are tried in order; the first one that has credentials **and** clears
    the live gate wins. When none qualifies the local SVG placeholder is written, so
    the mock/demo workflow never breaks.

    ⚠️ 2026-10-03 变更（两件）：
    ① 新增 `dashscope` 分支（`wan2.7-image`，0.2 元/张）。此前候选集只有
       `siliconflow`（无 key）与 `ark`（账号不可用），**产品实际出不了真图**。
    ② **给三家都补上闸门**。此前图像链既无计价也无闸门，而 `.env` 的 `ARK_API_KEY`
       会被 `init_environment()` setdefault 成 `VOLC_API_KEY`，于是 `ark` 分支
       **默认就在真发包**。现在统一走 `live_image_authorized()` +
       `check_live_image_budget()`，未授权时任何 provider 都不许开 HTTP。
       图像闸门**刻意不跟随** `VISIONCRAFT_ALLOW_LIVE_LLM`（理由见 live_budget）。
    ③ 名额**在出图成功之后**才计（`record_live_image_use`），不在候选链上逐家记。
       否则默认链里 ark 必然失败会把一次真图记成两张。
    """

    fallback_reason = "No live image provider configured"
    provider = os.getenv("VISIONCRAFT_IMAGE_PROVIDER", "siliconflow").strip().lower()
    providers = [provider] if provider in {"siliconflow", "ark", "volc", "dashscope"} else ["siliconflow", "ark", "dashscope"]
    if provider == "siliconflow":
        # 保留原有「siliconflow → ark」兜底语义，**并把 dashscope 接到链尾**：
        # 默认档下前三家都没配 key 时，dashscope 才会被尝试；且它还要单独过闸
        # （`VISIONCRAFT_ALLOW_LIVE_IMAGE`），所以默认行为与改前一致——仍落占位图。
        providers.extend(["ark", "dashscope"])

    for candidate in dict.fromkeys(providers):
        try:
            if candidate == "siliconflow" and os.getenv("SILICONFLOW_API_KEY"):
                generator = _generate_siliconflow_image
            elif candidate in {"ark", "volc"} and _ark_api_key():
                generator = _generate_ark_image
            elif candidate == "dashscope" and _dashscope_image_api_key():
                generator = _generate_dashscope_image
            else:
                continue
            # 图像链此前**没有任何闸门**（`live_budget` 里无 image 项），于是只要密钥在场
            # 就会真的发包——而 `.env` 的 `ARK_API_KEY` 经 `init_environment()` setdefault
            # 成 `VOLC_API_KEY`，`_ark_api_key()` 直接读得到，这条路径**默认就在真发包**。
            # 所以闸门必须**对三家一视同仁**，不能只盖新增的 dashscope
            # （与视频闸门当初「只挂在 MiniMax 分支」是同一个教训）。
            #
            # 未授权 → **跳过该候选**落回本地占位图，否则 mock/演示路径会被打断；
            # 已授权但超张数/超预算 → 必须上抛，**不能悄悄降级成占位图**
            # （那会把"你以为出了真图"和"实际只是占位图"混为一谈）。
            if not live_image_authorized():
                fallback_reason = f"真实图像调用尚未授权（{candidate}）"
                continue
            # 预检**不计数**：授权 → 每项目张数 → 预算。计数挪到出图成功之后
            # （`record_live_image_use`），因为这是候选链：默认档下 ark 账号已不可用
            # 必然失败再落 dashscope，开 HTTP 前计数会把一次真图记成两张。
            check_live_image_budget(request.project_id, count=1)
            asset_id = generator(request)
        except BudgetBlockedError:
            raise
        except Exception as exc:
            # Keep the workflow demo-safe, but record the reason so the UI can
            # distinguish real model output from a local placeholder.
            fallback_reason = _compact_error(exc)
            continue
        record_live_image_use(request.project_id, count=1)
        return asset_id

    return create_placeholder_svg(
        request.project_id,
        request.asset_type,
        request.name,
        f"{request.description}\nFallback reason: {fallback_reason}",
        request.prompt,
        request.accent,
        f"fallback:image:{fallback_reason}",
    )


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

    return persist_binary_asset(
        request.project_id, request.asset_type, request.name, request.description, request.prompt,
        content, suffix, "siliconflow", model,
    )


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

    return persist_binary_asset(
        request.project_id, request.asset_type, request.name, request.description, request.prompt,
        content, suffix, "ark", model,
    )


def _build_image_prompt(request: ImageAssetRequest) -> str:
    return (
        f"{request.prompt}. {request.description}. "
        "cinematic production still, clean composition, coherent anatomy, "
        "high quality, no text, no watermark, no UI overlay"
    )


# ---------------------------------------------------------------------------
# DashScope（阿里云百炼）图像链 —— 2026-10-03 新增
#
# 端点与视频链同源（`DASHSCOPE_API_HOST`），走 multimodal-generation 的**同步**接口：
# 一次请求直接返回结果（无异步任务、无轮询）。价格 0.2 元/张。
#
# ⚠️ 两个来自官方文档的硬约束，踩错会静默出错图：
# ① **尺寸必须用 `宽*高` 的像素写法**（如 `1280*720`）。若只写 `2K`，**没有图片输入时
#    输出是正方形**——16:9 的分镜首帧会变成方的。
#    `wan2.7-image` 的总像素需落在 [768*768, 2048*2048]，1280*720=921600 合规。
# ② **组图模式**（`enable_sequential=true`）一次可出多张主体一致的图，`n` 取值范围 1–12；
#    此时 `thinking_mode` / `color_palette` 不生效。这是"多镜首帧保持一致"的对症工具。
#
# 响应里图片 URL 在 `output.choices[].message.content[].image`，**有效期 24 小时**，
# 所以必须当场下载落盘（本项目一直如此做）。
# ---------------------------------------------------------------------------

DEFAULT_DASHSCOPE_IMAGE_SIZE = "1280*720"


def _dashscope_image_api_key() -> str:
    return os.getenv("DASHSCOPE_API_KEY") or ""


def _dashscope_image_endpoint() -> str:
    base_url = os.getenv("DASHSCOPE_API_HOST", "https://dashscope.aliyuncs.com").rstrip("/")
    return base_url + "/api/v1/services/aigc/multimodal-generation/generation"


def _dashscope_image_model() -> str:
    return os.getenv("DASHSCOPE_IMAGE_MODEL", "wan2.7-image")


def _dashscope_image_call(
    request: ImageAssetRequest,
    *,
    count: int,
    sequential: bool,
    input_images: list[str] | None = None,
    size: str | None = None,
) -> list[tuple[bytes, str]]:
    """调用百炼图像接口，返回 [(图片字节, 后缀)]，顺序与 provider 返回一致。"""
    api_key = _dashscope_image_api_key()
    if not api_key:
        raise RuntimeError("No DashScope image API key configured")
    model = _dashscope_image_model()
    content: list[dict] = [{"image": url} for url in (input_images or [])]
    content.append({"text": _build_image_prompt(request)})
    parameters: dict = {
        "size": size or os.getenv("DASHSCOPE_IMAGE_SIZE", DEFAULT_DASHSCOPE_IMAGE_SIZE),
        "n": int(count),
        "watermark": False,
        "enable_sequential": bool(sequential),
    }
    thinking = os.getenv("DASHSCOPE_IMAGE_THINKING", "").strip()
    if thinking:
        parameters["thinking_mode"] = thinking == "1"
    payload = {
        "model": model,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": parameters,
    }
    http_request = urllib.request.Request(
        _dashscope_image_endpoint(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(http_request, timeout=300) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DashScope image HTTP {exc.code}: {detail}") from exc

    urls = _dashscope_image_urls(body)
    if not urls:
        raise RuntimeError(
            "DashScope image returned no image data: "
            + json.dumps(body, ensure_ascii=False)[:400]
        )
    return [_download_image(url) for url in urls]


def _dashscope_image_urls(body: dict) -> list[str]:
    """取出响应里的**全部**图片 URL。

    文档结构是 `output.choices[].message.content[].image`。组图模式下 content 会含多项
    （也可能出现多个 choice），所以遍历全部 choice 的全部 content，**不假设只有第一项**。
    """
    urls: list[str] = []
    for choice in (body.get("output") or {}).get("choices") or []:
        message = (choice or {}).get("message") or {}
        for item in message.get("content") or []:
            if isinstance(item, dict) and item.get("image"):
                urls.append(str(item["image"]))
    return urls


def _download_image(url: str) -> tuple[bytes, str]:
    image_request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(image_request, timeout=300) as image_response:
        content = image_response.read()
        content_type = image_response.headers.get("Content-Type", "")
    suffix = ".png"
    if "jpeg" in content_type or "jpg" in content_type:
        suffix = ".jpg"
    elif "webp" in content_type:
        suffix = ".webp"
    return content, suffix


def _generate_dashscope_image(request: ImageAssetRequest) -> str:
    content, suffix = _dashscope_image_call(request, count=1, sequential=False)[0]
    return persist_binary_asset(
        request.project_id, request.asset_type, request.name, request.description, request.prompt,
        content, suffix, "dashscope", _dashscope_image_model(),
    )


def generate_image_series(request: ImageAssetRequest, *, count: int) -> list[str]:
    """组图模式：**一次调用**生成 `count` 张主体一致的图，逐张落盘为独立资产。

    对症场景：多镜首帧必须属于同一套视觉序列（同一人物数、同一明暗、同一景别），
    逐张独立生成做不到这一点——`enable_sequential` 才是为此设计的。
    返回 asset id 列表，顺序与 provider 返回顺序一致。
    """
    if not live_image_authorized():
        raise BudgetBlockedError(
            "真实图像调用尚未授权（dashscope），组图请求已阻止（BLOCKED_BEFORE_CALL）。"
        )
    # 组图是**单次请求出多张**：预检按请求张数做预算投影，落盘几张就记几张。
    check_live_image_budget(request.project_id, count=count)
    results = _dashscope_image_call(request, count=count, sequential=True)
    asset_ids: list[str] = []
    for index, (content, suffix) in enumerate(results, start=1):
        asset_ids.append(
            persist_binary_asset(
                request.project_id, request.asset_type, f"{request.name} #{index}",
                request.description, request.prompt, content, suffix,
                "dashscope", _dashscope_image_model(),
            )
        )
    record_live_image_use(request.project_id, count=len(asset_ids))
    return asset_ids


def _compact_error(error: Exception) -> str:
    message = " ".join(str(error).replace("\n", " ").split())
    return message[:280] or error.__class__.__name__


def _ark_api_key() -> str:
    return os.getenv("VOLC_IMAGE_API_KEY") or os.getenv("VOLC_API_KEY") or ""
