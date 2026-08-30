"""Project-level stage model configs and generation mode."""
from __future__ import annotations

import uuid

from ..database import connect, from_json, to_json, utc_now
from ..providers.live_budget import BudgetBlockedError, assert_live_text_allowed
from ..providers.llm_adapter import (
    JsonParseError,
    LiveCallNotAuthorized,
    ProviderError,
    adaptation_messages,
    build_text_request,
    coerce_adaptation_options,
    coerce_story_bible,
    coerce_storyboard,
    complete_json,
    story_bible_messages,
    storyboard_messages,
)
from ..providers.llm_catalog import (
    ALL_STAGES,
    GENERATION_MODES,
    STAGE_DOWNSTREAM_UI,
    STAGE_LABELS,
    ModelConfigError,
    default_for_stage,
    models_for_stage,
    validate_stage_selection,
)
from ..services.job_service import redact_value
from ..services.project_service import get_project


def get_generation_mode(project: dict | str) -> str:
    if isinstance(project, str):
        project = get_project(project)
    mode = (project or {}).get("generation_mode") or "mock"
    return mode if mode in GENERATION_MODES else "mock"


def set_generation_mode(project_id: str, mode: str) -> dict:
    if mode not in GENERATION_MODES:
        raise ModelConfigError(
            "INVALID_GENERATION_MODE",
            "生成模式无效。可用：mock（本地确定性）、live_strict（严格真实）、live_with_local_fallback（真实失败后本地回退）。",
        )
    _require_project(project_id)
    now = utc_now()
    with connect() as conn:
        conn.execute(
            "UPDATE projects SET generation_mode = ?, updated_at = ? WHERE id = ?",
            (mode, now, project_id),
        )
    return get_project_model_state(project_id)


def list_stage_configs(project_id: str) -> dict:
    project = _require_project(project_id)
    stored = _load_stored(project_id)
    stages = {}
    for stage in ALL_STAGES:
        stages[stage] = _resolved_row(project_id, stage, stored.get(stage), include_available=True)
    return {
        "generation_mode": get_generation_mode(project),
        "stale_stages": _as_str_list(project.get("stale_stages")),
        "stages": stages,
    }


def resolve_stage_model(project_id: str, stage: str) -> dict:
    stored = _load_stored(project_id)
    return _resolved_row(project_id, stage, stored.get(stage), include_available=False)


def save_stage_config(
    project_id: str,
    stage: str,
    *,
    provider: str,
    model: str,
    parameters: dict | None = None,
    workflow_run_id: str | None = None,
) -> dict:
    project = _require_project(project_id)
    matched = validate_stage_selection(stage, provider, model)
    now = utc_now()
    parameters = parameters or {}
    default = default_for_stage(stage)
    is_default = matched["provider"] == default["provider"] and matched["model"] == default["model"]
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM workflow_model_configs WHERE project_id = ? AND stage = ?",
            (project_id, stage),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE workflow_model_configs
                SET provider=?, model=?, parameters=?, selected_by_user=1, is_default=?, workflow_run_id=?, updated_at=?
                WHERE id=?
                """,
                (provider, model, to_json(parameters), 1 if is_default else 0, workflow_run_id, now, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO workflow_model_configs
                (id, project_id, workflow_run_id, stage, provider, model, parameters, selected_by_user, is_default, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    f"mc_{uuid.uuid4().hex[:10]}",
                    project_id,
                    workflow_run_id,
                    stage,
                    provider,
                    model,
                    to_json(parameters),
                    1 if is_default else 0,
                    now,
                    now,
                ),
            )
    invalidated = _mark_downstream_stale(project_id, stage, _as_str_list(project.get("stale_stages")))
    state = get_project_model_state(project_id)
    state["invalidated_stages"] = invalidated
    state["saved_stage"] = stage
    return state


def get_project_model_state(project_id: str) -> dict:
    from ..services.project_service import get_project as load

    return load(project_id)


def lineage_from_config(config: dict, *, source: str, used_local_fallback: bool = False) -> dict:
    return {
        "provider": config.get("provider"),
        "model": config.get("model"),
        "generation_mode": config.get("generation_mode"),
        "used_local_fallback": 1 if used_local_fallback else 0,
        "config_source": "user" if config.get("selected_by_user") else "default",
        "source": source,
    }


def run_text_stage(
    project: dict,
    stage: str,
    *,
    planner,
    messages: list[dict],
    coerce,
) -> tuple[Any, dict]:
    """Run planner or live LLM according to generation_mode. Never silent-success on live failure."""
    config = resolve_stage_model(project["id"], stage)
    config["generation_mode"] = get_generation_mode(project)
    mode = config["generation_mode"]
    planned = planner()
    if mode == "mock":
        return planned, lineage_from_config(config, source="mock_planner")

    prepared = build_text_request(
        provider=config["provider"],
        model=config["model"],
        messages=messages,
    )
    try:
        plan = assert_live_text_allowed(
            project["id"],
            stage,
            int(prepared.public_metadata().get("prompt_chars") or 0),
            planner_context(project),
        )
        prepared.metadata.update(plan)
    except BudgetBlockedError:
        raise
    public_meta = redact_value(prepared.public_metadata())
    try:
        parsed = complete_json(prepared)
        result = coerce(parsed, planned)
        lineage = lineage_from_config(config, source="live_llm")
        lineage["request_meta"] = public_meta
        return result, lineage
    except (LiveCallNotAuthorized, JsonParseError, ProviderError, ModelConfigError) as exc:
        reason = _safe_error_text(exc)
        if isinstance(exc, BudgetBlockedError) or getattr(exc, "code", "") == "BLOCKED_BEFORE_CALL":
            raise
        if mode == "live_strict":
            raise ModelConfigError("LIVE_LLM_FAILED", _strict_failure_message(exc, stage, reason)) from exc
        lineage = lineage_from_config(config, source="local_fallback", used_local_fallback=True)
        lineage["fallback_reason"] = reason
        lineage["request_meta"] = public_meta
        return planned, lineage


def plan_adaptations_with_policy(project: dict, planner) -> tuple[list[dict], dict]:
    source_text = planner_context(project)
    return run_text_stage(
        project,
        "adaptation_options",
        planner=planner,
        messages=adaptation_messages(
            project.get("title") or "",
            source_text,
            project.get("style") or "",
            int(project.get("duration_seconds") or 5),
        ),
        coerce=lambda parsed, fallback: coerce_adaptation_options(parsed, fallback, source_text),
    )


def plan_story_bible_with_policy(project: dict, option: dict, planner) -> tuple[dict, dict]:
    source_text = planner_context(project)
    return run_text_stage(
        project,
        "story_bible",
        planner=planner,
        messages=story_bible_messages(project.get("title") or "", source_text, project.get("style") or "", option),
        coerce=lambda parsed, fallback: coerce_story_bible(parsed, fallback),
    )


def plan_storyboard_with_policy(project: dict, option: dict, bible: dict, planner) -> tuple[list[dict], dict]:
    source_text = planner_context(project)
    return run_text_stage(
        project,
        "storyboard",
        planner=planner,
        messages=storyboard_messages(
            project.get("title") or "",
            source_text,
            project.get("style") or "",
            option,
            bible,
        ),
        coerce=lambda parsed, fallback: coerce_storyboard(parsed, fallback, source_text),
    )


def _as_str_list(value) -> list:
    if isinstance(value, list):
        return [str(item) for item in value]
    parsed = from_json(value, []) or []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def planner_context(project: dict) -> str:
    from ..services.adaptation_service import planner_source_text

    return planner_source_text(project)


def attach_model_state(project: dict) -> dict:
    if not project:
        return project
    project["generation_mode"] = get_generation_mode(project)
    stale = project.get("stale_stages")
    if isinstance(stale, list):
        project["stale_stages"] = stale
    else:
        project["stale_stages"] = from_json(stale, []) or []
    stored = _load_stored(project["id"])
    project["model_configs"] = {
        stage: _resolved_row(project["id"], stage, stored.get(stage), include_available=True)
        for stage in ALL_STAGES
    }
    return project


def _resolved_row(project_id: str, stage: str, stored: dict | None, *, include_available: bool) -> dict:
    default = default_for_stage(stage)
    if stored:
        row = {
            "stage": stage,
            "provider": stored["provider"],
            "model": stored["model"],
            "parameters": from_json(stored.get("parameters"), {}) or {},
            "selected_by_user": bool(stored.get("selected_by_user")),
            "is_default": bool(stored.get("is_default")),
            "configured": _configured_flag(stored["provider"], stored["model"], stage),
            "created_at": stored.get("created_at"),
            "updated_at": stored.get("updated_at"),
            "workflow_run_id": stored.get("workflow_run_id"),
            "label": STAGE_LABELS.get(stage, stage),
            "config_source": "user",
        }
    else:
        row = {
            "stage": stage,
            "provider": default["provider"],
            "model": default["model"],
            "parameters": {},
            "selected_by_user": False,
            "is_default": True,
            "configured": bool(default.get("configured")),
            "created_at": None,
            "updated_at": None,
            "workflow_run_id": None,
            "label": STAGE_LABELS.get(stage, stage),
            "config_source": "default",
        }
    if include_available:
        row["available"] = models_for_stage(stage)
    return row


def _configured_flag(provider: str, model: str, stage: str) -> bool:
    for item in models_for_stage(stage):
        if item["provider"] == provider and item["model"] == model:
            return bool(item.get("configured"))
    return False


def _load_stored(project_id: str) -> dict[str, dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM workflow_model_configs WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    return {row["stage"]: dict(row) for row in rows}


def _mark_downstream_stale(project_id: str, stage: str, current: list) -> list[str]:
    extra = list(STAGE_DOWNSTREAM_UI.get(stage, []))
    merged = list(dict.fromkeys([*current, *extra]))
    now = utc_now()
    with connect() as conn:
        conn.execute(
            "UPDATE projects SET stale_stages = ?, updated_at = ? WHERE id = ?",
            (to_json(merged), now, project_id),
        )
        if "bible" in extra:
            conn.execute(
                "UPDATE story_bibles SET review_status = CASE WHEN review_status = 'confirmed' THEN 'stale' ELSE review_status END, updated_at = ? WHERE project_id = ?",
                (now, project_id),
            )
        if "storyboard" in extra:
            conn.execute(
                "UPDATE storyboard_drafts SET review_status = 'stale', updated_at = ? WHERE project_id = ?",
                (now, project_id),
            )
        if "text" in extra or "storyline" in extra:
            conn.execute(
                "UPDATE adaptation_options SET stale = 1 WHERE project_id = ?",
                (project_id,),
            )
    return extra


def clear_stage_stale(project_id: str, ui_stages: list[str]) -> None:
    project = _require_project(project_id)
    current = [item for item in _as_str_list(project.get("stale_stages")) if item not in ui_stages]
    with connect() as conn:
        conn.execute(
            "UPDATE projects SET stale_stages = ?, updated_at = ? WHERE id = ?",
            (to_json(current), utc_now(), project_id),
        )


def _require_project(project_id: str) -> dict:
    project = get_project(project_id)
    if not project:
        raise ModelConfigError("PROJECT_NOT_FOUND", "项目不存在。")
    return project


def _safe_error_text(exc: Exception) -> str:
    text = str(exc)
    text = text.replace("\n", " ")
    lowered = text.lower()
    if "sk-" in lowered or "bearer " in lowered or "data:image" in lowered:
        return "文本模型调用失败。"
    return text[:240]


def _strict_failure_message(exc: Exception, stage: str, reason: str) -> str:
    label = STAGE_LABELS.get(stage, stage)
    if isinstance(exc, LiveCallNotAuthorized):
        return f"{label}处于严格真实模式，但真实调用尚未授权，任务已失败。{reason}"
    if isinstance(exc, JsonParseError):
        return f"{label}的真实模型返回无法解析的 JSON，任务已失败。{reason}"
    return f"{label}的真实模型调用失败，任务已失败。{reason}"
