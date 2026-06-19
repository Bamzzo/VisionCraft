import os
import shutil


def get_provider_capabilities() -> dict:
    deepseek_live = bool(os.getenv("DEEPSEEK_API_KEY"))
    siliconflow_live = bool(os.getenv("SILICONFLOW_API_KEY"))
    ark_image_live = bool(os.getenv("VOLC_IMAGE_API_KEY") or os.getenv("VOLC_API_KEY"))
    ark_video_live = bool(os.getenv("VOLC_VIDEO_API_KEY") or os.getenv("VOLC_API_KEY"))
    return {
        "llm": [
            {
                "id": "deepseek",
                "label": "DeepSeek",
                "mode": "live-ready" if deepseek_live else "not-configured",
                "tasks": ["story_planning", "prompt_generation", "feedback_parsing"],
            },
            {
                "id": "siliconflow",
                "label": "SiliconFlow",
                "mode": "live-ready" if siliconflow_live else "not-configured",
                "tasks": ["story_planning", "prompt_generation"],
            },
        ],
        "image": [
            {
                "id": "ark_image",
                "label": "Volcengine Ark Image",
                "mode": "live-ready" if ark_image_live else "not-configured",
                "supported_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"],
                "supported_resolutions": ["720p", "1080p"],
            },
            {
                "id": "siliconflow_image",
                "label": "SiliconFlow Image",
                "mode": "live-ready" if siliconflow_live else "not-configured",
                "supported_ratios": ["16:9", "9:16", "1:1"],
                "supported_resolutions": ["720p"],
            },
        ],
        "video": [
            {
                "id": "seedance",
                "label": "Volcengine Seedance",
                "mode": "live-ready" if ark_video_live else "not-configured",
                "supported_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"],
                "supported_durations": [5, 10],
                "supported_resolutions": ["720p", "1080p"],
            },
            {
                "id": "siliconflow_video",
                "label": "SiliconFlow Video",
                "mode": "live-ready" if siliconflow_live else "not-configured",
                "supported_ratios": ["16:9", "9:16", "1:1"],
                "supported_durations": [5],
                "supported_resolutions": ["720p"],
            },
        ],
    }


def get_provider_diagnostics() -> dict:
    deepseek_key = bool(os.getenv("DEEPSEEK_API_KEY"))
    siliconflow_key = bool(os.getenv("SILICONFLOW_API_KEY"))
    image_provider = os.getenv("VISIONCRAFT_IMAGE_PROVIDER", "siliconflow")
    video_provider = os.getenv("VISIONCRAFT_VIDEO_PROVIDER", "siliconflow")
    ark_image_key = bool(os.getenv("VOLC_IMAGE_API_KEY") or os.getenv("VOLC_API_KEY"))
    ark_video_key = bool(os.getenv("VOLC_VIDEO_API_KEY") or os.getenv("VOLC_API_KEY"))
    image_configured = (
        siliconflow_key
        if image_provider == "siliconflow"
        else ark_image_key
        if image_provider in {"ark", "volc"}
        else siliconflow_key or ark_image_key
    )
    video_configured = (
        siliconflow_key
        if video_provider == "siliconflow"
        else ark_video_key
        if video_provider in {"ark", "volc"}
        else siliconflow_key or ark_video_key
    )
    return {
        "llm": {
            "configured": deepseek_key or siliconflow_key,
            "provider": "deepseek" if deepseek_key else "siliconflow" if siliconflow_key else "mock",
            "model": os.getenv("DEEPSEEK_MODEL") if deepseek_key else os.getenv("SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V3.2"),
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
            "configured": video_configured,
            "provider": video_provider,
            "model": (os.getenv("VOLC_VIDEO_MODEL") or os.getenv("DOUBAO_VIDEO_ENDPOINT") or os.getenv("SEEDANCE_V2_ENDPOINT", "doubao-seedance-2-0-260128"))
            if video_provider in {"ark", "volc"}
            else os.getenv("SILICONFLOW_VIDEO_MODEL", "Wan-AI/Wan2.2-T2V-A14B"),
            "size": os.getenv("SILICONFLOW_VIDEO_SIZE", "1280x720"),
            "poll_seconds": int(os.getenv("VOLC_VIDEO_POLL_SECONDS", os.getenv("SILICONFLOW_VIDEO_POLL_SECONDS", "180"))),
            "fallback": "disabled: live video generation must succeed",
        },
        "tools": {
            "ffmpeg": bool(shutil.which("ffmpeg")),
        },
    }
