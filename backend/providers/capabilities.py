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

# 已退役的视频 provider（2026-09-29，竹木决定）。
#
# 缘由不是代码缺陷：火山方舟的 Seedance 2.0 需要**账号余额 ≥ 200 元**才能开通，本账号
# （2107602280）开通不了，实测报 `404 ModelNotOpen`（预检 §13.10/§13.11）。而 ark 的四种
# 模式与 dashscope 完全重复覆盖，dashscope 已全部真实验收通过 —— 退役它**不产生任何能力空洞**。
#
# 退役的含义（三处一起改，缺一个就会"广告了却做不到"）：
#   1. 能力表不再列出它 ⇒ 前端 `<select>` 直接由 `state.capabilities.video` 渲染，选项随之消失；
#   2. 隐式兜底链不再包含它 ⇒ 默认 siliconflow 的机器不会再把请求丢给一家用不了的；
#   3. 诊断面板不再把它算作"视频可用" ⇒ 只配了 ark 密钥的机器如实报「未配置访问密钥」，
#      而不是报可用、再由 provider 层 404。
#
# **保留**：适配器实现、单价表里 ark 的档位（它是"未登记取最贵档"的保守基准，删了会放松预算
# 护栏）、以及显式 `provider_override="ark"` 的分发路径（`test_video_provider_guard.py` 用它
# 打桩验证"闸门在网络之前拦下"）。这些都不是投放面，不构成"广告"。
#
# 恢复方式：账号门槛满足后，删掉下面这一条即可（适配器与测试都还在）。
RETIRED_VIDEO_PROVIDERS: dict[str, str] = {
    "ark": "火山方舟 Seedance 2.0 需账号余额 ≥ 200 元才能开通，本账号无法开通；其四种模式与 dashscope 完全重复，已退役。",
}

MODE_REQUIREMENTS = {
    "t2v": {"requires_first_frame": False, "requires_last_frame": False},
    "i2v": {"requires_first_frame": True, "requires_last_frame": False},
    "keyframes": {"requires_first_frame": True, "requires_last_frame": True},
    # 参考图模式：不吃首尾帧，但必须至少有一张参考图，否则等于退化成 t2v。
    "reference": {"requires_first_frame": False, "requires_last_frame": False, "requires_reference": True},
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


def retired_video_provider_reason(provider: str | None) -> str | None:
    """已知但已退役的 provider => 原因；否则 None。

    单独开成一个函数，是为了让"退役"只有一个定义处：能力表、诊断、校验都读这里，
    不会出现"表里没有、校验却放行"这类自相矛盾。
    """
    canonical = normalize_video_provider(provider)
    if not canonical:
        return None
    return RETIRED_VIDEO_PROVIDERS.get(canonical)


def get_provider_capabilities() -> dict:
    deepseek_live = bool(os.getenv("DEEPSEEK_API_KEY"))
    siliconflow_live = bool(os.getenv("SILICONFLOW_API_KEY"))
    ark_image_live = bool(os.getenv("VOLC_IMAGE_API_KEY") or os.getenv("VOLC_API_KEY"))
    dashscope_video_live = bool(os.getenv("DASHSCOPE_API_KEY"))
    minimax_video_live = bool(os.getenv("MINIMAX_API_KEY"))
    # ark 视频已退役（见 RETIRED_VIDEO_PROVIDERS），故不再有 `ark_video_live` 这个入参：
    # 能力表里没有它的条目，密钥在不在都不该影响任何一家可达性。
    video_providers = _video_provider_catalog(
        dashscope_video_live=dashscope_video_live,
        minimax_video_live=minimax_video_live,
        siliconflow_live=siliconflow_live,
    )
    return {
        "mode_requirements": MODE_REQUIREMENTS,
        "default_video_provider": default_video_provider(),
        "generation_modes": [
            {"id": "mock", "label": "本地演示（不调用真实模型）", "is_default": True},
            {"id": "live_strict", "label": "真实模型模式（失败即失败）", "is_default": False},
            {"id": "live_with_local_fallback", "label": "真实模型模式（允许本地回退）", "is_default": False},
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
        "live_access": _live_access_payload(),
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
    reference_paths: list[str] | None = None,
) -> dict:
    mode = (video_mode or "t2v").lower()
    if mode not in MODE_REQUIREMENTS:
        raise CapabilityError("UNSUPPORTED_VIDEO_MODE", f"不支持的视频模式：{video_mode}")

    requested_provider = normalize_video_provider(provider) or default_video_provider()
    # 退役判定必须排在"未知 provider"之前：ark 是个**已知**名字，报"未知"会让人以为
    # 是自己拼错了，反复试；这里要直接说清为什么不能用、以及该改用什么。
    retired_reason = retired_video_provider_reason(requested_provider)
    if retired_reason:
        raise CapabilityError(
            "PROVIDER_RETIRED",
            f"{requested_provider} 已退役，不能再用于新生成。{retired_reason}"
            "请改用 dashscope（四种模式全覆盖，已真实验收）或 minimax（参考图模式除外）。",
        )
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
    if requirements.get("requires_reference") and not reference_paths:
        raise CapabilityError(
            "MISSING_REFERENCE_IMAGE",
            "参考图模式需要至少一张参考图。请先在角色/场景卡片上挂参考图，或在镜头里选一张参考图。",
        )

    return {
        "provider": capability["id"],
        "model": model_capability["id"],
        "video_mode": mode,
        "duration_seconds": int(duration_seconds),
        "aspect_ratio": aspect_ratio,
        "resolution": model_capability.get("default_resolution") or capability.get("default_resolution"),
        "provider_label": capability["label"],
        "model_label": model_capability.get("label") or model_capability["id"],
        "reference_count": len(reference_paths or []),
        # 各家的规则不一样：Seedance 的参考图与首尾帧互斥，wan2.7-r2v 允许并存。
        # 这里把它变成能力声明，provider 层据此决定要不要带上首帧。
        "reference_includes_first_frame": bool(capability.get("reference_includes_first_frame")),
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
    # 默认 provider 若指向一家已退役的，绝不能借它的密钥报"已配置"：那会让诊断面板说
    # 可用、而 provider 层 404。退役就是不可用，密钥在不在都不改变这一点。
    video_retired = retired_video_provider_reason(canonical_video)
    video_configured = False if video_retired else {
        "siliconflow": siliconflow_key,
        "ark": ark_video_key,
        "dashscope": dashscope_key,
        "minimax": minimax_key,
    }.get(canonical_video, siliconflow_key or ark_video_key)
    return {
        "llm": {
            "configured": deepseek_key,
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "status_label": "已配置" if deepseek_key else "未配置",
        },
        "image": {
            "configured": image_configured,
            "provider": image_provider,
            "model": (os.getenv("VOLC_IMAGE_MODEL") or os.getenv("DOUBAO_IMAGE_ENDPOINT", "doubao-seedream-5-0-260128"))
            if image_provider in {"ark", "volc"}
            else os.getenv("SILICONFLOW_IMAGE_MODEL", "Qwen/Qwen-Image"),
            "status_label": "已配置" if image_configured else "未配置",
        },
        "video": {
            "configured": bool(video_configured),
            "provider": canonical_video,
            "model": _default_model_for_provider(canonical_video),
            "status_label": "已退役" if video_retired else ("已配置" if video_configured else "未配置"),
            "retired_reason": video_retired,
            # 只列仍在投放面上的 provider。ark 已退役，若还列成 available=True，就和
            # 「能力表里根本没有它」自相矛盾 —— 而诊断面板的全部意义正是不自相矛盾。
            "available_providers": {
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
    dashscope_t2v = os.getenv("DASHSCOPE_T2V_MODEL", "wan2.7-t2v")
    dashscope_i2v = os.getenv("DASHSCOPE_I2V_MODEL", "wan2.7-i2v")
    # 参考生视频是单独的模型：wan2.7-i2v 只吃首帧，参考图要 r2v 才收。
    dashscope_r2v = os.getenv("DASHSCOPE_R2V_MODEL", "wan2.7-r2v")
    minimax_model = os.getenv("MINIMAX_VIDEO_MODEL", "MiniMax-H3")
    siliconflow_model = os.getenv("SILICONFLOW_VIDEO_MODEL", "Wan-AI/Wan2.2-T2V-A14B")
    # ark 条目整条移除：能力表只列**可交付**的 provider（见 RETIRED_VIDEO_PROVIDERS）。
    #
    # 一个必须说清的后果：它原先声明的 `reference_includes_first_frame: False`（参考图与首帧
    # 互斥）随条目退场，于是这条能力声明在剩下的 provider 组合下恒为 True —— 换个角度说，
    # `validate_video_generation` 里那条分支现在没有 provider 会走到。这是**有意的取舍**：
    # ark 适配器的互斥规则仍有单测钉着（test_reference_generation.test_payloads 直接调
    # `_ark_content_items`），所以规则本身不是无人看管，只是暂时不在投放路径上。
    return [
        {
            "id": "dashscope",
            "label": "阿里百炼 Wan",
            "aliases": ["wan", "alibaba", "dashscope_wan"],
            "mode": "live-ready" if live_flags["dashscope_video_live"] else "not-configured",
            "supported_modes": ["t2v", "i2v", "keyframes", "reference"],
            "supported_ratios": ["16:9", "9:16", "1:1"],
            "supported_durations": [2, 5, 10, 15],
            "supported_resolutions": ["720P", "1080P"],
            "default_resolution": os.getenv("DASHSCOPE_VIDEO_RESOLUTION", "720P"),
            "default_model": dashscope_i2v,
            # wan2.7-r2v 的 media 里 first_frame 与 reference_image 可以同时出现，
            # 这是三家主力里唯一"首帧 + 角色参考图"都能要的组合。
            "reference_includes_first_frame": True,
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
                {
                    "id": dashscope_r2v,
                    "label": "Wan 2.7 R2V",
                    "supported_modes": ["reference"],
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


def _live_access_payload() -> dict:
    from .llm_catalog import deepseek_configured, live_llm_authorized, live_vision_authorized
    from .live_budget import live_video_authorized
    from .video_provider import video_key_status

    deepseek_key = deepseek_configured()
    video_keys = video_key_status()
    any_video_key = any(video_keys.values())
    llm_authorized = live_llm_authorized()
    vision_authorized = live_vision_authorized()
    video_authorized = live_video_authorized()

    text_ready = bool(llm_authorized and deepseek_key)
    vision_ready = bool(vision_authorized and deepseek_key)
    # 视频只要有一家可用就算可用：分发环会跳过没有密钥的候选，
    # 只认 MiniMax 会把「只配了 ark / dashscope」误报成不可用。
    video_ready = bool(video_authorized and any_video_key)
    ready = text_ready or vision_ready or video_ready

    # Report the two gates separately. A missing key and a closed authorization
    # switch collapse into the same false flag above, and telling them apart is
    # the entire point of a diagnostic.
    #
    # Keep this payload free of environment variable names: it is returned to the
    # browser, and test_stage_models.py asserts that no key or switch name leaks
    # through here. Deployment detail belongs in the repository docs instead.
    blocked_by: list[str] = []
    if not deepseek_key:
        blocked_by.append("文本与视觉：未配置访问密钥")
    if not any_video_key:
        blocked_by.append("视频：未配置访问密钥")
    if not llm_authorized:
        blocked_by.append("文本：真实调用授权未开启")
    if not vision_authorized:
        blocked_by.append("视觉：真实调用授权未开启")
    if not video_authorized:
        blocked_by.append("视频：真实调用授权未开启")

    hint = (
        "真实模型已开通。严格真实失败会标记任务失败；允许本地回退时会明确写明「已使用本地回退」。"
        if ready
        else (
            "真实调用尚未授权，当前只走本地演示路径。仅配置密钥不会发起真实请求，"
            "还需要显式开启真实调用授权开关（文本、视觉、视频各一个，默认都关闭；"
            "文本总开关会连带打开视觉与视频）。单次闭环的护栏默认为视频 1 次、总额 5 元。"
            "具体开启方式见仓库内的配置模板注释与交付文档。"
        )
    )
    return {
        "ready": ready,
        "text_ready": text_ready,
        "vision_ready": vision_ready,
        "video_ready": video_ready,
        "authorized": {"llm": llm_authorized, "vision": vision_authorized, "video": video_authorized},
        "keys_present": {"deepseek": deepseek_key, **video_keys},
        "blocked_by": blocked_by,
        "hint": hint,
    }


def live_access_snapshot() -> dict:
    """Public view of authorization state, used by ``/api/health``."""
    return _live_access_payload()


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
