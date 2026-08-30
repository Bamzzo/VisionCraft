import os
import shutil

PROVIDER_ALIASES = {
    "seedance": "ark",
    "volc": "ark",
    "volcengine": "ark",
    "wan": "dashscope",
    "alibaba": "dashscope",
    "aliyun": "dashscope",
    "dashscope_wan": "dashscope",
}

MODE_REQUIREMENTS = {
    "t2v": {"requires_first_frame": False, "requires_last_frame": False},
    "i2v": {"requires_first_frame": True, "requires_last_frame": False},
    "keyframes": {"requires_first_frame": True, "requires_last_frame": True},
}


class CapabilityError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def normalize_video_provider(provider: str | None) -> str | None:
    if not provider:
        return None
    key = provider.strip().lower()
    return PROVIDER_ALIASES.get(key, key)


def default_video_provider() -> str:
    return normalize_video_provider(os.getenv("VISIONCRAFT_VIDEO_PROVIDER", "minimax")) or "minimax"


def get_provider_capabilities() -> dict:
    deepseek_live = bool(os.getenv("DEEPSEEK_API_KEY"))
    siliconflow_live = bool(os.getenv("SILICONFLOW_API_KEY"))
    ark_image_live = bool(os.getenv("VOLC_IMAGE_API_KEY") or os.getenv("VOLC_API_KEY"))
    ark_video_live = bool(os.getenv("VOLC_VIDEO_API_KEY") or os.getenv("VOLC_API_KEY"))
    dashscope_video_live = bool(os.getenv("DASHSCOPE_API_KEY"))
    minimax_video_live = bool(os.getenv("MINIMAX_API_KEY"))
    video_providers = _video_provider_catalog(
        ark_video_live=ark_video_live,
        dashscope_video_live=dashscope_video_live,
        minimax_video_live=minimax_video_live,
        siliconflow_live=siliconflow_live,
    )
    return {
        "mode_requirements": MODE_REQUIREMENTS,
        "default_video_provider": default_video_provider(),
        "generation_modes": [
            {"id": "mock", "label": "本地确定性（Mock）", "is_default": True},
            {"id": "live_strict", "label": "严格真实（失败即失败）", "is_default": False},
            {"id": "live_with_local_fallback", "label": "真实优先，允许本地回退", "is_default": False},
        ],
        "llm_providers": [
            {
                "id": "deepseek",
                "label": "DeepSeek",
                "mode": "live-ready" if deepseek_live else "not-configured",
                "tasks": ["story_planning", "prompt_generation", "feedback_parsing", "vision"],
            },
            {
                "id": "siliconflow",
                "label": "SiliconFlow",
                "mode": "live-ready" if siliconflow_live else "not-configured",
                "tasks": ["story_planning", "prompt_generation"],
            },
        ],
        "llm": _llm_models_payload(),
        "stages": _stage_defaults_payload(),
        "image": [
            {
                "id": "ark_image",
                "label": "火山方舟图像",
                "mode": "live-ready" if ark_image_live else "not-configured",
                "supported_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"],
                "supported_resolutions": ["720p", "1080p"],
            },
            {
                "id": "siliconflow_image",
                "label": "SiliconFlow 图像",
                "mode": "live-ready" if siliconflow_live else "not-configured",
                "supported_ratios": ["16:9", "9:16", "1:1"],
                "supported_resolutions": ["720p"],
            },
        ],
        "video": video_providers,
    }


def get_video_provider_capability(provider: str | None) -> dict | None:
    canonical = normalize_video_provider(provider)
    if not canonical:
        return None
    for item in get_provider_capabilities()["video"]:
        if item["id"] == canonical or canonical in item.get("aliases", []):
            return item
    return None


def validate_video_generation(
    *,
    provider: str | None,
    model: str | None,
    video_mode: str,
    duration_seconds: int,
    aspect_ratio: str,
    first_frame_path: str | None,
    last_frame_path: str | None,
) -> dict:
    mode = (video_mode or "t2v").lower()
    if mode not in MODE_REQUIREMENTS:
        raise CapabilityError("UNSUPPORTED_VIDEO_MODE", f"不支持的视频模式：{video_mode}")

    requested_provider = normalize_video_provider(provider) or default_video_provider()
    capability = get_video_provider_capability(requested_provider)
    if not capability:
        raise CapabilityError("UNKNOWN_PROVIDER", f"未知视频 Provider：{provider or requested_provider}")

    model_capability = _resolve_model_capability(capability, model, mode)
    requirements = MODE_REQUIREMENTS[mode]
    if mode not in capability.get("supported_modes", []) or mode not in model_capability.get("supported_modes", []):
        raise CapabilityError(
            "UNSUPPORTED_MODE_FOR_MODEL",
            f"{capability['label']} / {model_capability['id']} 不支持 {mode} 模式。",
        )
    if aspect_ratio not in capability.get("supported_ratios", []):
        raise CapabilityError(
            "UNSUPPORTED_ASPECT_RATIO",
            f"{capability['label']} 不支持比例 {aspect_ratio}。可用：{'、'.join(capability.get('supported_ratios', []))}",
        )
    if int(duration_seconds) not in {int(item) for item in capability.get("supported_durations", [])}:
        raise CapabilityError(
            "UNSUPPORTED_DURATION",
            f"{capability['label']} 不支持时长 {duration_seconds}s。可用：{'、'.join(str(item) + 's' for item in capability.get('supported_durations', []))}",
        )
    if requirements["requires_first_frame"] and not first_frame_path:
        raise CapabilityError("MISSING_FIRST_FRAME", "缺少首帧，无法提交图生视频。请先选择或生成首帧。")
    if requirements["requires_last_frame"] and not last_frame_path:
        raise CapabilityError("MISSING_LAST_FRAME", "缺少尾帧，无法提交首尾帧模式。请先选择或生成尾帧。")

    return {
        "provider": capability["id"],
        "model": model_capability["id"],
        "video_mode": mode,
        "duration_seconds": int(duration_seconds),
        "aspect_ratio": aspect_ratio,
        "resolution": model_capability.get("default_resolution") or capability.get("default_resolution"),
        "provider_label": capability["label"],
        "model_label": model_capability.get("label") or model_capability["id"],
    }


def get_provider_diagnostics() -> dict:
    deepseek_key = bool(os.getenv("DEEPSEEK_API_KEY"))
    siliconflow_key = bool(os.getenv("SILICONFLOW_API_KEY"))
    image_provider = os.getenv("VISIONCRAFT_IMAGE_PROVIDER", "siliconflow")
    video_provider = os.getenv("VISIONCRAFT_VIDEO_PROVIDER", "minimax")
    ark_image_key = bool(os.getenv("VOLC_IMAGE_API_KEY") or os.getenv("VOLC_API_KEY"))
    ark_video_key = bool(os.getenv("VOLC_VIDEO_API_KEY") or os.getenv("VOLC_API_KEY"))
    dashscope_key = bool(os.getenv("DASHSCOPE_API_KEY"))
    minimax_key = bool(os.getenv("MINIMAX_API_KEY"))
    image_configured = (
        siliconflow_key
        if image_provider == "siliconflow"
        else ark_image_key
        if image_provider in {"ark", "volc"}
        else siliconflow_key or ark_image_key
    )
    canonical_video = normalize_video_provider(video_provider) or "minimax"
    video_configured = {
        "siliconflow": siliconflow_key,
        "ark": ark_video_key,
        "dashscope": dashscope_key,
        "minimax": minimax_key,
    }.get(canonical_video, siliconflow_key or ark_video_key)
    return {
        "llm": {
            "configured": deepseek_key or siliconflow_key,
            "provider": "deepseek" if deepseek_key else "siliconflow" if siliconflow_key else "mock",
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash") if deepseek_key else os.getenv("SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V3.2"),
        },
        "image": {
            "configured": image_configured,
            "provider": image_provider,
            "model": (os.getenv("VOLC_IMAGE_MODEL") or os.getenv("DOUBAO_IMAGE_ENDPOINT", "doubao-seedream-5-0-260128"))
            if image_provider in {"ark", "volc"}
            else os.getenv("SILICONFLOW_IMAGE_MODEL", "Qwen/Qwen-Image"),
            "size": os.getenv("VOLC_IMAGE_SIZE", "2K")
            if image_provider in {"ark", "volc"}
            else os.getenv("SILICONFLOW_IMAGE_SIZE", "1024x576"),
            "fallback": "local SVG placeholder",
        },
        "video": {
            "configured": bool(video_configured),
            "provider": canonical_video,
            "model": _default_model_for_provider(canonical_video),
            "size": os.getenv("SILICONFLOW_VIDEO_SIZE", "1280x720"),
            "poll_seconds": int(os.getenv("VOLC_VIDEO_POLL_SECONDS", os.getenv("SILICONFLOW_VIDEO_POLL_SECONDS", "180"))),
            "fallback": "disabled: live video generation must succeed",
            "available_providers": {
                "ark": ark_video_key,
                "dashscope": dashscope_key,
                "minimax": minimax_key,
                "siliconflow": siliconflow_key,
            },
        },
        "tools": {
            "ffmpeg": bool(shutil.which("ffmpeg")),
        },
    }


def _video_provider_catalog(**live_flags: bool) -> list[dict]:
    ark_model = os.getenv("VOLC_VIDEO_MODEL") or os.getenv("DOUBAO_VIDEO_ENDPOINT") or os.getenv("SEEDANCE_V2_ENDPOINT", "doubao-seedance-2-0-260128")
    dashscope_t2v = os.getenv("DASHSCOPE_T2V_MODEL", "wan2.7-t2v")
    dashscope_i2v = os.getenv("DASHSCOPE_I2V_MODEL", "wan2.7-i2v")
    minimax_model = os.getenv("MINIMAX_VIDEO_MODEL", "MiniMax-H3")
    siliconflow_model = os.getenv("SILICONFLOW_VIDEO_MODEL", "Wan-AI/Wan2.2-T2V-A14B")
    return [
        {
            "id": "ark",
            "label": "火山 Seedance",
            "aliases": ["seedance", "volc", "volcengine"],
            "mode": "live-ready" if live_flags["ark_video_live"] else "not-configured",
            "supported_modes": ["t2v", "i2v", "keyframes"],
            "supported_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"],
            "supported_durations": [5, 10],
            "supported_resolutions": ["720p", "1080p"],
            "default_resolution": os.getenv("VOLC_VIDEO_RESOLUTION", "720p"),
            "default_model": ark_model,
            "models": [
                {
                    "id": ark_model,
                    "label": "Seedance 2.0",
                    "supported_modes": ["t2v", "i2v", "keyframes"],
                    "default_resolution": os.getenv("VOLC_VIDEO_RESOLUTION", "720p"),
                }
            ],
        },
        {
            "id": "dashscope",
            "label": "阿里百炼 Wan",
            "aliases": ["wan", "alibaba", "dashscope_wan"],
            "mode": "live-ready" if live_flags["dashscope_video_live"] else "not-configured",
            "supported_modes": ["t2v", "i2v", "keyframes"],
            "supported_ratios": ["16:9", "9:16", "1:1"],
            "supported_durations": [2, 5, 10, 15],
            "supported_resolutions": ["720P", "1080P"],
            "default_resolution": os.getenv("DASHSCOPE_VIDEO_RESOLUTION", "720P"),
            "default_model": dashscope_i2v,
            "models": [
                {
                    "id": dashscope_t2v,
                    "label": "Wan 2.7 T2V",
                    "supported_modes": ["t2v"],
                    "default_resolution": os.getenv("DASHSCOPE_VIDEO_RESOLUTION", "720P"),
                },
                {
                    "id": dashscope_i2v,
                    "label": "Wan 2.7 I2V",
                    "supported_modes": ["i2v", "keyframes"],
                    "default_resolution": os.getenv("DASHSCOPE_VIDEO_RESOLUTION", "720P"),
                },
            ],
        },
        {
            "id": "minimax",
            "label": "MiniMax H3",
            "aliases": [],
            "mode": "live-ready" if live_flags["minimax_video_live"] else "not-configured",
            "supported_modes": ["t2v", "i2v", "keyframes"],
            "supported_ratios": ["16:9", "9:16", "1:1"],
            "supported_durations": [4, 6, 10, 15],
            "supported_resolutions": ["768P", "1080P"],
            "default_resolution": os.getenv("MINIMAX_VIDEO_RESOLUTION", "768P"),
            "default_model": minimax_model,
            "models": [
                {
                    "id": minimax_model,
                    "label": "MiniMax H3",
                    "supported_modes": ["t2v", "i2v", "keyframes"],
                    "default_resolution": os.getenv("MINIMAX_VIDEO_RESOLUTION", "768P"),
                }
            ],
        },
        {
            "id": "siliconflow",
            "label": "SiliconFlow Video",
            "aliases": ["siliconflow_video"],
            "mode": "live-ready" if live_flags["siliconflow_live"] else "not-configured",
            "supported_modes": ["t2v"],
            "supported_ratios": ["16:9", "9:16", "1:1"],
            "supported_durations": [5],
            "supported_resolutions": ["720p"],
            "default_resolution": "720p",
            "default_model": siliconflow_model,
            "models": [
                {
                    "id": siliconflow_model,
                    "label": "Wan 2.2 T2V",
                    "supported_modes": ["t2v"],
                    "default_resolution": "720p",
                }
            ],
        },
    ]


def _resolve_model_capability(capability: dict, model: str | None, video_mode: str) -> dict:
    models = capability.get("models") or []
    if model:
        for item in models:
            if item["id"] == model:
                return item
        raise CapabilityError("UNKNOWN_MODEL", f"{capability['label']} 不包含模型 {model}。")
    for item in models:
        if video_mode in item.get("supported_modes", []):
            return item
    default_id = capability.get("default_model")
    for item in models:
        if item["id"] == default_id:
            return item
    if models:
        return models[0]
    raise CapabilityError("UNKNOWN_MODEL", f"{capability['label']} 未声明可用模型。")


def _default_model_for_provider(provider: str) -> str:
    if provider == "ark":
        return os.getenv("VOLC_VIDEO_MODEL") or os.getenv("DOUBAO_VIDEO_ENDPOINT") or os.getenv("SEEDANCE_V2_ENDPOINT", "doubao-seedance-2-0-260128")
    if provider == "dashscope":
        return os.getenv("DASHSCOPE_I2V_MODEL", "wan2.7-i2v")
    if provider == "minimax":
        return os.getenv("MINIMAX_VIDEO_MODEL", "MiniMax-H3")
    return os.getenv("SILICONFLOW_VIDEO_MODEL", "Wan-AI/Wan2.2-T2V-A14B")


def _llm_models_payload() -> list[dict]:
    from .llm_catalog import llm_model_catalog

    return llm_model_catalog()


def _stage_defaults_payload() -> dict:
    from .llm_catalog import STAGE_LABELS, STAGE_ROLE, ALL_STAGES, default_for_stage

    return {
        stage: {
            "stage": stage,
            "role": STAGE_ROLE[stage],
            "label": STAGE_LABELS[stage],
            "default_provider": default_for_stage(stage)["provider"],
            "default_model": default_for_stage(stage)["model"],
        }
        for stage in ALL_STAGES
    }
