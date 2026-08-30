"""Stage-aware model catalog.

Default models are first-use preselection only. They are never a lock-in:
the user can switch provider/model on every stage that supports selection.
FFmpeg is local assembly, not an LLM or media-generation model.
"""
from __future__ import annotations

import os

# capabilities is imported lazily to avoid a module cycle.

GENERATION_MODES = ("mock", "live_strict", "live_with_local_fallback")

STAGE_TEXT = (
    "text_understanding",
    "adaptation_options",
    "story_bible",
    "storyboard",
)
STAGE_VISION = ("vision_review",)
STAGE_IMAGE = ("keyframe_generation",)
STAGE_VIDEO = ("video_generation",)
ALL_STAGES = STAGE_TEXT + STAGE_VISION + STAGE_IMAGE + STAGE_VIDEO

STAGE_ROLE = {
    "text_understanding": "text_generation",
    "adaptation_options": "text_generation",
    "story_bible": "text_generation",
    "storyboard": "text_generation",
    "vision_review": "vision",
    "keyframe_generation": "image_generation",
    "video_generation": "video_generation",
}

STAGE_LABELS = {
    "text_understanding": "文本理解",
    "adaptation_options": "改编方案",
    "story_bible": "Story Bible",
    "storyboard": "分镜设计",
    "vision_review": "关键帧视觉检查",
    "keyframe_generation": "关键帧生成",
    "video_generation": "镜头视频",
}

# UI stage ids that become stale after a config change. Production media is never deleted.
STAGE_DOWNSTREAM_UI = {
    "text_understanding": ["text", "storyline", "bible", "storyboard"],
    "adaptation_options": ["text", "storyline", "bible", "storyboard"],
    "story_bible": ["bible", "storyboard"],
    "storyboard": ["storyboard"],
    "vision_review": [],
    "keyframe_generation": [],
    "video_generation": [],
}

DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_FLASH = "deepseek-v4-flash"
DEEPSEEK_PRO = "deepseek-v4-pro"
DEEPSEEK_VISION = "deepseek-v4-flash-vision-exp"

MAX_CHAT_BODY_BYTES = 48 * 1024 * 1024
MAX_IMAGE_URL_CHARS = 8192


class ModelConfigError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def deepseek_configured() -> bool:
    return bool(os.getenv("DEEPSEEK_API_KEY"))


def text_model_catalog() -> list[dict]:
    configured = deepseek_configured()
    return [
        _llm_entry(
            provider="deepseek",
            model=DEEPSEEK_FLASH,
            label="DeepSeek V4 Flash",
            roles=["text_generation"],
            supports_vision=False,
            supports_json=True,
            is_default=True,
            configured=configured,
            stages=list(STAGE_TEXT),
        ),
        _llm_entry(
            provider="deepseek",
            model=DEEPSEEK_PRO,
            label="DeepSeek V4 Pro",
            roles=["text_generation"],
            supports_vision=False,
            supports_json=True,
            is_default=False,
            configured=configured,
            stages=list(STAGE_TEXT),
        ),
    ]


def vision_model_catalog() -> list[dict]:
    configured = deepseek_configured()
    return [
        _llm_entry(
            provider="deepseek",
            model=DEEPSEEK_VISION,
            label="DeepSeek V4 Flash Vision Exp",
            roles=["vision"],
            supports_vision=True,
            supports_json=True,
            is_default=True,
            configured=configured,
            stages=list(STAGE_VISION),
        )
    ]


def image_model_catalog() -> list[dict]:
    ark_live = bool(os.getenv("VOLC_IMAGE_API_KEY") or os.getenv("VOLC_API_KEY"))
    silicon_live = bool(os.getenv("SILICONFLOW_API_KEY"))
    ark_model = os.getenv("VOLC_IMAGE_MODEL") or os.getenv("DOUBAO_IMAGE_ENDPOINT", "doubao-seedream-5-0-260128")
    silicon_model = os.getenv("SILICONFLOW_IMAGE_MODEL", "Qwen/Qwen-Image")
    return [
        {
            "provider": "ark",
            "model": ark_model,
            "label": "火山方舟图像",
            "roles": ["image_generation"],
            "supports_vision": False,
            "supports_json": False,
            "is_default": True,
            "configured": ark_live,
            "available_for_stage": list(STAGE_IMAGE),
        },
        {
            "provider": "siliconflow",
            "model": silicon_model,
            "label": "SiliconFlow 图像",
            "roles": ["image_generation"],
            "supports_vision": False,
            "supports_json": False,
            "is_default": False,
            "configured": silicon_live,
            "available_for_stage": list(STAGE_IMAGE),
        },
    ]


def llm_model_catalog() -> list[dict]:
    return text_model_catalog() + vision_model_catalog()


def default_for_stage(stage: str) -> dict:
    stage = _require_stage(stage)
    if stage in STAGE_TEXT:
        return next(item for item in text_model_catalog() if item["is_default"])
    if stage in STAGE_VISION:
        return next(item for item in vision_model_catalog() if item["is_default"])
    if stage in STAGE_IMAGE:
        images = image_model_catalog()
        return next((item for item in images if item["is_default"]), images[0] if images else _missing_image_default())
    video = _default_video_entry()
    return video


def models_for_stage(stage: str) -> list[dict]:
    stage = _require_stage(stage)
    if stage in STAGE_TEXT:
        return text_model_catalog()
    if stage in STAGE_VISION:
        return vision_model_catalog()
    if stage in STAGE_IMAGE:
        return image_model_catalog()
    return _video_model_entries()


def validate_stage_selection(stage: str, provider: str, model: str) -> dict:
    stage = _require_stage(stage)
    provider = (provider or "").strip()
    model = (model or "").strip()
    if not provider or not model:
        raise ModelConfigError("MODEL_REQUIRED", "请选择 Provider 和模型。")
    role = STAGE_ROLE[stage]
    for item in models_for_stage(stage):
        if item["provider"] == provider and item["model"] == model:
            if role not in item["roles"]:
                raise ModelConfigError(
                    "ROLE_MISMATCH",
                    f"{STAGE_LABELS[stage]}不能使用该模型角色。请选择{ _role_label(role) }。",
                )
            if role == "text_generation" and item.get("supports_vision"):
                raise ModelConfigError("ROLE_MISMATCH", "视觉模型不能用于文本阶段。")
            if role == "vision" and not item.get("supports_vision"):
                raise ModelConfigError("ROLE_MISMATCH", "文本模型不能用于视觉检查阶段。")
            return item
    if stage in STAGE_TEXT and _is_vision_model(provider, model):
        raise ModelConfigError("ROLE_MISMATCH", "视觉模型不能用于文本阶段。")
    if stage in STAGE_VISION and not _is_vision_model(provider, model):
        raise ModelConfigError("ROLE_MISMATCH", "文本模型不能用于视觉检查阶段。")
    raise ModelConfigError(
        "UNKNOWN_MODEL",
        f"{STAGE_LABELS[stage]}不支持 {provider} / {model}。",
    )


def stage_defaults_payload() -> dict:
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


def live_llm_authorized() -> bool:
    return os.getenv("VISIONCRAFT_ALLOW_LIVE_LLM") == "1"


def live_vision_authorized() -> bool:
    return os.getenv("VISIONCRAFT_ALLOW_LIVE_VISION") == "1" or live_llm_authorized()


def _llm_entry(**kwargs) -> dict:
    stages = kwargs.pop("stages")
    return {
        **kwargs,
        "available_for_stage": stages,
    }


def _require_stage(stage: str) -> str:
    key = (stage or "").strip()
    if key not in ALL_STAGES:
        raise ModelConfigError("UNKNOWN_STAGE", f"未知制作阶段：{stage or '（空）'}。")
    return key


def _is_vision_model(provider: str, model: str) -> bool:
    return any(item["provider"] == provider and item["model"] == model for item in vision_model_catalog())


def _role_label(role: str) -> str:
    return {
        "text_generation": "文本模型",
        "vision": "视觉模型",
        "image_generation": "图片生成模型",
        "video_generation": "视频生成模型",
    }.get(role, role)


def _missing_image_default() -> dict:
    return {
        "provider": "ark",
        "model": os.getenv("VOLC_IMAGE_MODEL") or os.getenv("DOUBAO_IMAGE_ENDPOINT", "doubao-seedream-5-0-260128"),
        "label": "火山方舟图像",
        "roles": ["image_generation"],
        "supports_vision": False,
        "supports_json": False,
        "is_default": True,
        "configured": False,
        "available_for_stage": list(STAGE_IMAGE),
    }


def _default_video_entry() -> dict:
    from .capabilities import normalize_video_provider

    provider = normalize_video_provider(os.getenv("VISIONCRAFT_VIDEO_PROVIDER", "minimax")) or "minimax"
    model_by_provider = {
        "ark": os.getenv("VOLC_VIDEO_MODEL") or os.getenv("DOUBAO_VIDEO_ENDPOINT") or os.getenv("SEEDANCE_V2_ENDPOINT", "doubao-seedance-2-0-260128"),
        "dashscope": os.getenv("DASHSCOPE_I2V_MODEL", "wan2.7-i2v"),
        "minimax": os.getenv("MINIMAX_VIDEO_MODEL", "MiniMax-H3"),
        "siliconflow": os.getenv("SILICONFLOW_VIDEO_MODEL", "Wan-AI/Wan2.2-T2V-A14B"),
    }
    configured = {
        "ark": bool(os.getenv("VOLC_VIDEO_API_KEY") or os.getenv("VOLC_API_KEY")),
        "dashscope": bool(os.getenv("DASHSCOPE_API_KEY")),
        "minimax": bool(os.getenv("MINIMAX_API_KEY")),
        "siliconflow": bool(os.getenv("SILICONFLOW_API_KEY")),
    }.get(provider, False)
    labels = {
        "ark": "火山 Seedance",
        "dashscope": "阿里百炼 Wan",
        "minimax": "MiniMax H3",
        "siliconflow": "SiliconFlow Video",
    }
    return {
        "provider": provider,
        "model": model_by_provider.get(provider, model_by_provider["minimax"]),
        "label": labels.get(provider, provider),
        "roles": ["video_generation"],
        "supports_vision": False,
        "supports_json": False,
        "is_default": True,
        "configured": configured,
        "available_for_stage": list(STAGE_VIDEO),
    }


def _video_model_entries() -> list[dict]:
    from .capabilities import get_provider_capabilities, normalize_video_provider

    caps = get_provider_capabilities()
    default_provider = normalize_video_provider(caps.get("default_video_provider")) or "minimax"
    items = []
    for row in caps.get("video") or []:
        for model in row.get("models") or []:
            items.append(
                {
                    "provider": row["id"],
                    "model": model["id"],
                    "label": f"{row.get('label') or row['id']} / {model.get('label') or model['id']}",
                    "roles": ["video_generation"],
                    "supports_vision": False,
                    "supports_json": False,
                    "is_default": row["id"] == default_provider and model["id"] == row.get("default_model"),
                    "configured": row.get("mode") == "live-ready",
                    "available_for_stage": list(STAGE_VIDEO),
                    "supported_modes": model.get("supported_modes") or [],
                }
            )
    return items
