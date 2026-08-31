"""P4-A short-text adaptation, Story Bible, storyboard review, and checkpoints."""
from __future__ import annotations

import uuid

from ..database import connect, from_json, to_json, utc_now
from ..providers.llm_catalog import ModelConfigError
from ..services.checkpoint_service import complete_checkpoint, get_paused_checkpoint, save_workflow_checkpoint
from ..services.job_service import create_job, list_active_jobs, redact_text, update_job
from ..services.model_config_service import (
    clear_stage_stale,
    plan_adaptations_with_policy,
    plan_story_bible_with_policy,
    plan_storyboard_with_policy,
)
from ..services.project_service import get_project, update_project_status
from ..workflow.adaptation_planner import plan_adaptations, plan_story_bible, plan_storyboard


class AdaptationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


BIBLE_SCALAR_FIELDS = (
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
)
BIBLE_JSON_FIELDS = ("themes", "style_tags", "character_cards", "scene_cards")
STORYBOARD_FIELDS = (
    "title",
    "narrative_purpose",
    "characters",
    "scene",
    "action_text",
    "camera_motion",
    "duration_seconds",
    "visual_prompt",
    "bible_character",
    "bible_scene",
    "source_excerpt",
)
SCOPE_STATUSES = {"created", "draft", "adaptation_options_ready", "awaiting_scope_review", "running", "failed"}
PRODUCTION_OK = {"production_ready", "ready_for_review", "review_pending", "video_ready", "completed"}
PAST_SCOPE = {
    "awaiting_bible_review",
    "story_bible_ready",
    "awaiting_storyboard_review",
    "storyboard_draft_ready",
    *PRODUCTION_OK,
}
PAST_BIBLE = {"awaiting_storyboard_review", "storyboard_draft_ready", *PRODUCTION_OK}
PAST_STORYBOARD = set(PRODUCTION_OK)


def start_adaptation_workflow(project_id: str, job_id: str | None = None) -> dict:
    project = _require_project(project_id)
    from ..workflow.medium_text_planner import text_scale
    from .medium_text_service import MediumTextError, ensure_implicit_short_scope, run_medium_analysis

    scale = text_scale(project["source_text"])
    if scale == "long":
        raise AdaptationError(
            "TEXT_TOO_LONG",
            "当前文本超过 10,000 字。P5-B 章节检索尚未实现，请先截取不超过 10,000 字，或使用短文本直接改编。",
        )
    job_id = job_id or create_job(project_id, "adaptation_workflow", "文本理解已排队")
    if scale == "medium":
        try:
            state = run_medium_analysis(project_id, job_id)
        except MediumTextError as exc:
            raise AdaptationError(exc.code, str(exc)) from exc
        return {"job_id": job_id, "status": state.get("status"), "project": state}
    ensure_implicit_short_scope(project)
    update_job(job_id, "running", 8, "文本理解：正在抽取段落、人物与冲突依据", stage="understand_text")
    try:
        payload = _generate_options(project)
    except ModelConfigError as exc:
        update_job(job_id, "failed", 100, str(exc), str(exc), stage="understand_text")
        raise AdaptationError(exc.code, str(exc)) from exc
    if any(item.get("used_local_fallback") for item in payload):
        update_job(job_id, "running", 22, "真实文本模型失败，已使用本地回退生成改编方案", stage="plan_adaptations")
    update_project_status(project_id, "awaiting_scope_review")
    save_workflow_checkpoint(
        project_id,
        job_id,
        "scope_review",
        {
            "project_id": project_id,
            "job_id": job_id,
            "node": "scope_review",
            "stage": "awaiting_scope_review",
            "option_id": None,
            "input_summary": f"已生成 {len(payload)} 个改编候选",
            "pause_reason": "已到达改编范围审核节点，等待选择并确认方案。",
        },
    )
    update_job(job_id, "paused", 28, "改编方案已就绪，等待选择故事范围", stage="awaiting_scope_review")
    return {"job_id": job_id, "status": "awaiting_scope_review", "options": payload}


def generate_options_from_context(project_id: str, job_id: str | None = None) -> list[dict]:
    project = _require_project(project_id)
    job_id = job_id or create_job(project_id, "adaptation_workflow", "改编方案生成已排队")
    update_job(job_id, "running", 22, "正在根据已确认范围生成改编候选方案", stage="plan_adaptations")
    try:
        payload = _generate_options(project)
    except ModelConfigError as exc:
        update_job(job_id, "failed", 100, str(exc), str(exc), stage="plan_adaptations")
        raise AdaptationError(exc.code, str(exc)) from exc
    if any(item.get("used_local_fallback") for item in payload):
        update_job(job_id, "running", 24, "真实文本模型失败，已使用本地回退生成改编方案", stage="plan_adaptations")
    update_project_status(project_id, "awaiting_scope_review")
    save_workflow_checkpoint(
        project_id,
        job_id,
        "scope_review",
        {
            "project_id": project_id,
            "job_id": job_id,
            "node": "scope_review",
            "stage": "awaiting_scope_review",
            "input_summary": f"已生成 {len(payload)} 个改编候选",
            "pause_reason": "已到达改编范围审核节点，等待选择并确认方案。",
        },
    )
    update_job(job_id, "paused", 28, "改编方案已就绪，等待选择故事范围", stage="awaiting_scope_review")
    return payload


def list_adaptation_options(project_id: str) -> list[dict]:
    _require_project(project_id)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM adaptation_options WHERE project_id = ? ORDER BY option_index",
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def select_adaptation_option(project_id: str, option_id: str) -> dict:
    project = _require_project(project_id)
    status = project.get("status") or ""
    if status in PAST_SCOPE:
        raise AdaptationError("ALREADY_CONFIRMED", "改编范围已确认，不能倒退更换方案。如需更换，请先重生成改编范围。")
    option = _require_option(project_id, option_id)
    now = utc_now()
    with connect() as conn:
        conn.execute("UPDATE adaptation_options SET selected = 0 WHERE project_id = ?", (project_id,))
        conn.execute("UPDATE adaptation_options SET selected = 1 WHERE id = ? AND project_id = ?", (option_id, project_id))
        conn.execute(
            "UPDATE projects SET selected_option_id = ?, status = ?, updated_at = ? WHERE id = ?",
            (option_id, "awaiting_scope_review", now, project_id),
        )
    _record_review(project_id, "scope", "select", f"选定方案：{option['title']}", option_id)
    paused = get_paused_checkpoint(project_id)
    if paused and paused.get("node") == "scope_review":
        save_workflow_checkpoint(
            project_id,
            paused["job_id"],
            "scope_review",
            {
                **(paused.get("state") or {}),
                "option_id": option_id,
                "input_summary": f"已选定方案：{option.get('title') or option_id}",
            },
        )
    return get_adaptation_state(project_id)


def confirm_scope(project_id: str, option_id: str | None = None, job_id: str | None = None) -> dict:
    project = _require_project(project_id)
    option_id = option_id or project.get("selected_option_id")
    status = project.get("status") or ""
    bible = _load_bible(project_id)
    if option_id and project.get("selected_option_id") and option_id != project.get("selected_option_id") and status in PAST_SCOPE:
        raise AdaptationError("ALREADY_CONFIRMED", "改编范围已确认，不能倒退更换方案。如需更换，请先重生成改编范围。")
    if status in PAST_SCOPE and bible and bible.get("review_status") != "stale":
        state = get_adaptation_state(project_id)
        state["resumed_idempotent"] = True
        state["resume_message"] = "改编范围已确认，未重复生成 Story Bible。"
        return state
    if not option_id:
        raise AdaptationError("SCOPE_NOT_SELECTED", "尚未选择改编方案。请先选定一个候选范围，再确认并生成 Story Bible。")
    option = _require_option(project_id, option_id)
    select_adaptation_option(project_id, option_id)
    job_id = _reuse_or_create_job(project_id, "adaptation_bible", "Story Bible 生成已排队", job_id)
    update_project_status(project_id, "running")
    update_job(job_id, "running", 40, "Story Bible：正在根据选定方案生成可编辑圣经", stage="story_bible")
    try:
        _write_bible(project, option)
    except Exception as exc:
        _fail_workflow(project_id, job_id, "生成 Story Bible 失败，可从范围审核检查点继续。", exc)
        raise AdaptationError("WORKFLOW_FAILED", "生成 Story Bible 失败，可从当前审核检查点继续。") from exc
    update_project_status(project_id, "awaiting_bible_review")
    save_workflow_checkpoint(
        project_id,
        job_id,
        "bible_review",
        {
            "project_id": project_id,
            "job_id": job_id,
            "node": "bible_review",
            "stage": "awaiting_bible_review",
            "option_id": option_id,
            "input_summary": f"已确认方案：{option.get('title') or option_id}",
            "pause_reason": "已到达 Story Bible 审核节点，等待确认后再生成分镜。",
        },
    )
    _record_review(project_id, "scope", "confirm", f"确认范围：{option['title']}", option_id)
    update_job(job_id, "paused", 55, "Story Bible 已生成，等待确认或修改", stage="awaiting_bible_review")
    return get_adaptation_state(project_id)


def save_story_bible_draft(project_id: str, payload: dict) -> dict:
    _require_project(project_id)
    current = _load_bible(project_id)
    if not current:
        raise AdaptationError("BIBLE_MISSING", "还没有 Story Bible。请先选择改编方案并确认范围。")
    merged = _merge_bible(current, payload)
    _upsert_bible_row(project_id, merged)
    _sync_bible_cards(project_id, merged)
    return get_adaptation_state(project_id)


def confirm_bible(project_id: str, payload: dict | None = None, job_id: str | None = None) -> dict:
    project = _require_project(project_id)
    if not project.get("selected_option_id"):
        raise AdaptationError("SCOPE_NOT_SELECTED", "尚未选择改编方案，不能确认 Story Bible。请先完成范围审核。")
    bible = _load_bible(project_id)
    drafts = _load_storyboard(project_id)
    status = project.get("status") or ""
    if status in PAST_BIBLE and bible and bible.get("review_status") == "confirmed" and drafts:
        if payload:
            raise AdaptationError("ALREADY_CONFIRMED", "Story Bible 已确认，不能倒退修改。如需修改，请重生成 Story Bible。")
        state = get_adaptation_state(project_id)
        state["resumed_idempotent"] = True
        state["resume_message"] = "Story Bible 已确认，未重复生成分镜。"
        return state
    if payload:
        save_story_bible_draft(project_id, payload)
        bible = _load_bible(project_id)
    if not bible:
        raise AdaptationError("BIBLE_MISSING", "还没有 Story Bible。请先确认改编范围以生成圣经。")
    job_id = _reuse_or_create_job(project_id, "adaptation_storyboard", "分镜草案生成已排队", job_id)
    with connect() as conn:
        conn.execute(
            "UPDATE story_bibles SET review_status = ?, updated_at = ? WHERE project_id = ?",
            ("confirmed", utc_now(), project_id),
        )
    _record_review(project_id, "bible", "confirm", "确认 Story Bible，生成分镜草案", bible.get("option_id"))
    return generate_storyboard(project_id, job_id)


def generate_storyboard(project_id: str, job_id: str | None = None) -> dict:
    project = _require_project(project_id)
    bible = _load_bible(project_id)
    if not bible or bible.get("review_status") != "confirmed":
        raise AdaptationError("BIBLE_NOT_CONFIRMED", "尚未确认 Story Bible，不能生成分镜。请先保存并确认圣经。")
    if not project.get("selected_option_id"):
        raise AdaptationError("SCOPE_NOT_SELECTED", "尚未选择改编方案，不能生成分镜。")
    option = _require_option(project_id, project["selected_option_id"])
    job_id = job_id or _reuse_or_create_job(project_id, "adaptation_storyboard", "分镜草案生成已排队", job_id)
    update_project_status(project_id, "running")
    update_job(job_id, "running", 68, "分镜：正在生成带原文依据的镜头草案", stage="storyboard")
    try:
        _write_storyboard(project, option, bible)
    except Exception as exc:
        _fail_workflow(project_id, job_id, "生成分镜失败，可从 Story Bible 审核检查点继续。", exc)
        raise AdaptationError("WORKFLOW_FAILED", "生成分镜失败，可从当前审核检查点继续。") from exc
    update_project_status(project_id, "awaiting_storyboard_review")
    save_workflow_checkpoint(
        project_id,
        job_id,
        "storyboard_review",
        {
            "project_id": project_id,
            "job_id": job_id,
            "node": "storyboard_review",
            "stage": "awaiting_storyboard_review",
            "option_id": project.get("selected_option_id"),
            "input_summary": f"分镜草案 {len(_load_storyboard(project_id))} 镜",
            "pause_reason": "已到达分镜审核节点，等待确认后进入镜头制作。",
        },
    )
    update_job(job_id, "paused", 82, "分镜草案已就绪，等待审核确认", stage="awaiting_storyboard_review")
    return get_adaptation_state(project_id)


def save_storyboard_drafts(project_id: str, items: list[dict]) -> dict:
    _require_project(project_id)
    existing = {row["id"]: row for row in _load_storyboard(project_id)}
    if not existing:
        raise AdaptationError("STORYBOARD_MISSING", "还没有分镜草案。请先确认 Story Bible 以生成分镜。")
    now = utc_now()
    with connect() as conn:
        for item in items:
            draft_id = item.get("id")
            if not draft_id or draft_id not in existing:
                raise AdaptationError("STORYBOARD_MISMATCH", "分镜草稿不属于当前项目，无法保存。")
            current = existing[draft_id]
            merged = {**current, **{key: item[key] for key in STORYBOARD_FIELDS if key in item}}
            if "characters" in merged and not isinstance(merged["characters"], str):
                merged["characters"] = to_json(merged["characters"])
            conn.execute(
                """
                UPDATE storyboard_drafts SET
                  title=?, narrative_purpose=?, characters=?, scene=?, action_text=?, camera_motion=?,
                  duration_seconds=?, visual_prompt=?, bible_character=?, bible_scene=?, source_excerpt=?,
                  source_type=?, review_status=?, updated_at=?
                WHERE id=? AND project_id=?
                """,
                (
                    merged.get("title") or "",
                    merged.get("narrative_purpose") or "",
                    merged["characters"] if isinstance(merged.get("characters"), str) else to_json(merged.get("characters") or []),
                    merged.get("scene") or "",
                    merged.get("action_text") or "",
                    merged.get("camera_motion") or "",
                    int(merged.get("duration_seconds") or 5),
                    merged.get("visual_prompt") or "",
                    merged.get("bible_character"),
                    merged.get("bible_scene"),
                    merged.get("source_excerpt") or "",
                    "human_edit",
                    "edited",
                    now,
                    draft_id,
                    project_id,
                ),
            )
    return get_adaptation_state(project_id)


def confirm_storyboard(project_id: str, items: list[dict] | None = None, job_id: str | None = None) -> dict:
    project = _require_project(project_id)
    bible = _load_bible(project_id)
    if not bible or bible.get("review_status") != "confirmed":
        raise AdaptationError("BIBLE_NOT_CONFIRMED", "尚未确认 Story Bible，不能生成或确认分镜。请先保存并确认圣经。")
    status = project.get("status") or ""
    drafts = _load_storyboard(project_id)
    if status in PAST_STORYBOARD and (_has_production_shots(project_id) or drafts):
        if items:
            raise AdaptationError("ALREADY_CONFIRMED", "分镜已确认并进入制作，不能倒退重复确认。如需修改，请重生成分镜。")
        paused = get_paused_checkpoint(project_id)
        if paused:
            complete_checkpoint(paused["id"])
        state = get_adaptation_state(project_id)
        state["resumed_idempotent"] = True
        state["resume_message"] = "分镜已确认，未重复创建制作镜头。"
        return state
    if items:
        save_storyboard_drafts(project_id, items)
    drafts = _load_storyboard(project_id)
    if not drafts:
        raise AdaptationError("STORYBOARD_MISSING", "没有可确认的分镜草案。")
    job_id = _reuse_or_create_job(project_id, "adaptation_production", "正在进入镜头制作", job_id)
    update_job(job_id, "running", 90, "确认分镜：写入制作镜头，不自动生成关键帧或视频", stage="production")
    try:
        _promote_storyboard(project_id, drafts)
    except Exception as exc:
        _fail_workflow(project_id, job_id, "确认分镜失败，可从分镜审核检查点继续。", exc)
        raise AdaptationError("WORKFLOW_FAILED", "确认分镜失败，可从当前审核检查点继续。") from exc
    with connect() as conn:
        conn.execute(
            "UPDATE storyboard_drafts SET review_status = ?, updated_at = ? WHERE project_id = ?",
            ("confirmed", utc_now(), project_id),
        )
    update_project_status(project_id, "production_ready")
    paused = get_paused_checkpoint(project_id)
    if paused:
        complete_checkpoint(paused["id"])
    _record_review(project_id, "storyboard", "confirm", "确认分镜，进入镜头制作", None)
    update_job(job_id, "completed", 100, "分镜已确认，可进行关键帧、模型选择与局部生成", stage="production_ready")
    return get_adaptation_state(project_id)


def regenerate_stage(project_id: str, stage: str, job_id: str | None = None) -> dict:
    project = _require_project(project_id)
    if stage in {"analysis", "storyline"}:
        from .medium_text_service import MediumTextError, regenerate_medium

        try:
            return regenerate_medium(project_id, stage, job_id)
        except MediumTextError as exc:
            raise AdaptationError(exc.code, str(exc)) from exc
    if stage not in {"scope", "bible", "storyboard"}:
        raise AdaptationError("INVALID_STAGE", "只能对范围、Story Bible 或分镜执行修改后重生成。")
    job_id = job_id or create_job(project_id, f"adaptation_regen_{stage}", f"{_stage_label(stage)}修改后重生成已排队")
    if stage == "scope":
        update_job(job_id, "running", 12, "失效下游 Story Bible 与分镜草案，保留已有镜头版本与资产", stage="regenerate_scope")
        _invalidate_bible_and_storyboard(project_id)
        from ..workflow.medium_text_planner import text_scale
        from .medium_text_service import load_confirmed_scope

        if text_scale(project["source_text"]) == "medium" and load_confirmed_scope(project_id):
            generate_options_from_context(project_id, job_id)
        else:
            start_adaptation_workflow(project_id, job_id)
        _record_review(project_id, "scope", "regenerate", "修改后重生成改编方案", None)
    elif stage == "bible":
        if not project.get("selected_option_id"):
            raise AdaptationError("SCOPE_NOT_SELECTED", "没有已选方案，无法重生成 Story Bible。")
        update_job(job_id, "running", 40, "失效分镜草案并重生成 Story Bible，不删除已有镜头版本/视频/资产", stage="regenerate_bible")
        _clear_storyboard(project_id)
        option = _require_option(project_id, project["selected_option_id"])
        _write_bible(project, option, preserve_user_fields=False)
        update_project_status(project_id, "awaiting_bible_review")
        save_workflow_checkpoint(
            project_id,
            job_id,
            "bible_review",
            {
                "project_id": project_id,
                "job_id": job_id,
                "node": "bible_review",
                "stage": "awaiting_bible_review",
                "option_id": option["id"],
                "input_summary": "Story Bible 已重生成",
                "pause_reason": "已到达 Story Bible 审核节点，等待确认后再生成分镜。",
            },
        )
        _record_review(project_id, "bible", "regenerate", "修改后重生成 Story Bible", option["id"])
        update_job(job_id, "paused", 55, "Story Bible 已重生成，等待确认", stage="awaiting_bible_review")
    else:
        bible = _load_bible(project_id)
        if not bible or bible.get("review_status") != "confirmed":
            raise AdaptationError("BIBLE_NOT_CONFIRMED", "尚未确认 Story Bible，不能重生成分镜。")
        option = _require_option(project_id, project["selected_option_id"])
        update_job(job_id, "running", 70, "仅重生成分镜草案，保留 Story Bible 与已有制作镜头版本", stage="regenerate_storyboard")
        _write_storyboard(project, option, bible)
        update_project_status(project_id, "awaiting_storyboard_review")
        save_workflow_checkpoint(
            project_id,
            job_id,
            "storyboard_review",
            {
                "project_id": project_id,
                "job_id": job_id,
                "node": "storyboard_review",
                "stage": "awaiting_storyboard_review",
                "option_id": project.get("selected_option_id"),
                "input_summary": "分镜草案已重生成",
                "pause_reason": "已到达分镜审核节点，等待确认后进入镜头制作。",
            },
        )
        _record_review(project_id, "storyboard", "regenerate", "修改后重生成分镜草案", None)
        update_job(job_id, "paused", 82, "分镜草案已重生成，等待审核", stage="awaiting_storyboard_review")
    return get_adaptation_state(project_id)


def get_adaptation_state(project_id: str) -> dict:
    project = get_project(project_id)
    if not project:
        raise AdaptationError("PROJECT_NOT_FOUND", "项目不存在。")
    return project


def assert_batch_generation_allowed(project: dict) -> None:
    status = project.get("status") or ""
    if status in {
        "created",
        "draft",
        "awaiting_storyline_review",
        "adaptation_options_ready",
        "awaiting_scope_review",
        "story_bible_ready",
        "awaiting_bible_review",
        "storyboard_draft_ready",
        "awaiting_storyboard_review",
    }:
        raise AdaptationError(
            "STORYBOARD_NOT_CONFIRMED",
            "分镜尚未确认，不能批量生成关键帧或视频。请先完成三次审核，或使用单镜头局部生成。",
        )


def planner_source_text(project: dict) -> str:
    from .medium_text_service import load_confirmed_scope

    scope = load_confirmed_scope(project["id"])
    if scope and (scope.get("scoped_text") or "").strip():
        return scope["scoped_text"]
    return project["source_text"]


def _active_scope_id(project_id: str) -> str | None:
    from .medium_text_service import load_confirmed_scope

    scope = load_confirmed_scope(project_id)
    return scope.get("id") if scope else None


def _remap_excerpt(full_text: str, excerpt: str, start, end) -> tuple[str, int | None, int | None]:
    quote = excerpt or ""
    if quote and quote in full_text:
        index = full_text.find(quote)
        return quote, index, index + len(quote)
    try:
        start_i = int(start) if start is not None else None
        end_i = int(end) if end is not None else None
    except (TypeError, ValueError):
        return quote, None, None
    return quote, start_i, end_i


def _generate_options(project: dict) -> list[dict]:
    now = utc_now()
    context = planner_source_text(project)
    full = project["source_text"]
    scope_id = _active_scope_id(project["id"])

    def planner():
        return plan_adaptations(project["title"], context, project.get("style") or "", int(project.get("duration_seconds") or 5))

    planned, lineage = plan_adaptations_with_policy(project, planner)
    source = lineage.get("source") or "mock_planner"
    with connect() as conn:
        conn.execute("DELETE FROM adaptation_options WHERE project_id = ?", (project["id"],))
        conn.execute("UPDATE projects SET selected_option_id = NULL, updated_at = ? WHERE id = ?", (now, project["id"]))
        rows = []
        for item in planned:
            excerpt, start, end = _remap_excerpt(full, item.get("source_excerpt") or "", item.get("source_start"), item.get("source_end"))
            if excerpt and excerpt not in context:
                continue
            option_id = f"opt_{uuid.uuid4().hex[:10]}"
            conn.execute(
                """
                INSERT INTO adaptation_options
                (id, project_id, option_index, title, rationale, protagonist_goal, conflict, ending_orientation,
                 suggested_duration_seconds, suggested_shot_count, source_excerpt, source_start, source_end,
                 selected, source, created_at, scope_id, provider, model, generation_mode, used_local_fallback, config_source, stale)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    option_id,
                    project["id"],
                    item["option_index"],
                    item["title"],
                    item["rationale"],
                    item["protagonist_goal"],
                    item["conflict"],
                    item["ending_orientation"],
                    item["suggested_duration_seconds"],
                    item["suggested_shot_count"],
                    excerpt,
                    start,
                    end,
                    source,
                    now,
                    scope_id,
                    lineage.get("provider"),
                    lineage.get("model"),
                    lineage.get("generation_mode"),
                    lineage.get("used_local_fallback") or 0,
                    lineage.get("config_source"),
                ),
            )
            rows.append(
                {
                    **item,
                    "id": option_id,
                    "project_id": project["id"],
                    "selected": 0,
                    "source_excerpt": excerpt,
                    "source_start": start,
                    "source_end": end,
                    "scope_id": scope_id,
                    "source": source,
                    "provider": lineage.get("provider"),
                    "model": lineage.get("model"),
                    "generation_mode": lineage.get("generation_mode"),
                    "used_local_fallback": lineage.get("used_local_fallback") or 0,
                    "config_source": lineage.get("config_source"),
                }
            )
    if not rows:
        raise AdaptationError("SCOPE_EMPTY", "选定范围内无法生成改编方案。请重新选择事件范围后确认。")
    clear_stage_stale(project["id"], ["text", "storyline"])
    update_project_status(project["id"], "adaptation_options_ready")
    return rows


def _write_bible(project: dict, option: dict, preserve_user_fields: bool = True) -> None:
    def planner():
        return plan_story_bible(project["title"], planner_source_text(project), project.get("style") or "", option)

    try:
        planned, lineage = plan_story_bible_with_policy(project, option, planner)
    except ModelConfigError as exc:
        raise AdaptationError(exc.code, str(exc)) from exc
    current = _load_bible(project["id"]) if preserve_user_fields else None
    merged = _merge_bible(current, planned) if current else planned
    if not preserve_user_fields:
        merged = planned
    merged["option_id"] = option["id"]
    merged["review_status"] = "draft"
    merged["source"] = lineage.get("source") or "mock_planner"
    merged["scope_id"] = option.get("scope_id") or _active_scope_id(project["id"])
    _upsert_bible_row(project["id"], merged)
    _apply_output_lineage("story_bibles", "project_id = ?", (project["id"],), lineage)
    _sync_bible_cards(project["id"], merged)
    clear_stage_stale(project["id"], ["bible"])


def _write_storyboard(project: dict, option: dict, bible: dict) -> None:
    context = planner_source_text(project)
    full = project["source_text"]
    scope_id = option.get("scope_id") or _active_scope_id(project["id"])

    def planner():
        return plan_storyboard(project["title"], context, project.get("style") or "", option, bible)

    try:
        shots, lineage = plan_storyboard_with_policy(project, option, bible, planner)
    except ModelConfigError as exc:
        raise AdaptationError(exc.code, str(exc)) from exc
    now = utc_now()
    _clear_storyboard(project["id"])
    with connect() as conn:
        for item in shots:
            excerpt, start, end = _remap_excerpt(full, item.get("source_excerpt") or "", item.get("source_start"), item.get("source_end"))
            if excerpt and excerpt not in context:
                continue
            conn.execute(
                """
                INSERT INTO storyboard_drafts
                (id, project_id, shot_index, title, narrative_purpose, characters, scene, action_text, camera_motion,
                 duration_seconds, visual_prompt, bible_character, bible_scene, source_excerpt, source_start, source_end,
                 source_type, review_status, created_at, updated_at, scope_id, provider, model, generation_mode,
                 used_local_fallback, config_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"sb_{uuid.uuid4().hex[:10]}",
                    project["id"],
                    item["shot_index"],
                    item["title"],
                    item["narrative_purpose"],
                    to_json(item.get("characters") or []),
                    item.get("scene") or "",
                    item.get("action_text") or "",
                    item.get("camera_motion") or "",
                    int(item.get("duration_seconds") or 5),
                    item.get("visual_prompt") or "",
                    item.get("bible_character"),
                    item.get("bible_scene"),
                    excerpt,
                    start,
                    end,
                    "auto_draft",
                    "draft",
                    now,
                    now,
                    scope_id,
                    lineage.get("provider"),
                    lineage.get("model"),
                    lineage.get("generation_mode"),
                    lineage.get("used_local_fallback") or 0,
                    lineage.get("config_source"),
                ),
            )
    clear_stage_stale(project["id"], ["storyboard"])
    update_project_status(project["id"], "storyboard_draft_ready")


def _promote_storyboard(project_id: str, drafts: list[dict]) -> None:
    now = utc_now()
    forked_existing = False
    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM shots WHERE project_id = ? ORDER BY shot_index",
            (project_id,),
        ).fetchall()
        by_index = {int(row["shot_index"]): dict(row) for row in existing}
        for draft in drafts:
            index = int(draft["shot_index"])
            payload = _storyboard_shot_payload(draft)
            if index in by_index:
                shot = by_index[index]
                owned = conn.execute(
                    "SELECT id FROM shots WHERE id = ? AND project_id = ?",
                    (shot["id"], project_id),
                ).fetchone()
                if not owned or shot.get("project_id") != project_id:
                    raise AdaptationError("SHOT_MISMATCH", "镜头与项目关系不匹配，无法确认分镜。")
                version_id = _insert_confirmed_version(
                    conn,
                    shot["id"],
                    payload,
                    now,
                    change_summary="确认分镜生成的新版本",
                )
                _update_promoted_shot(conn, project_id, shot["id"], draft, payload, version_id, now)
                _upsert_promoted_draft(conn, project_id, shot["id"], payload, now)
                forked_existing = True
            else:
                shot_id = f"shot_{uuid.uuid4().hex[:10]}"
                version_id = f"version_{uuid.uuid4().hex[:10]}"
                conn.execute(
                    """
                    INSERT INTO shots
                    (id, project_id, shot_index, title, description, characters, scene, camera_motion, visual_prompt,
                     negative_prompt, audio_prompt, rag_evidence, narrative_purpose, action_text, duration_seconds,
                     bible_character, bible_scene, source_excerpt, source_start, source_end, source_type, review_status,
                     status, retry_count, current_version_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (
                        shot_id,
                        project_id,
                        index,
                        draft["title"],
                        payload["description"],
                        to_json(payload["characters"]),
                        payload["scene"],
                        payload["camera_motion"],
                        payload["visual_prompt"],
                        "",
                        "",
                        to_json(payload["rag"]),
                        payload["narrative_purpose"],
                        payload["action_text"],
                        payload["duration_seconds"],
                        payload["bible_character"],
                        payload["bible_scene"],
                        payload["source_excerpt"],
                        payload["source_start"],
                        payload["source_end"],
                        payload["source_type"],
                        "confirmed",
                        "production_ready",
                        version_id,
                        now,
                        now,
                    ),
                )
                _insert_confirmed_version(
                    conn,
                    shot_id,
                    payload,
                    now,
                    version_id=version_id,
                    version_number=1,
                    change_summary="分镜确认冻结的初始版本",
                )
                _upsert_promoted_draft(conn, project_id, shot_id, payload, now)
        if forked_existing:
            conn.execute(
                "UPDATE projects SET assembly_stale = 1, updated_at = ? WHERE id = ?",
                (now, project_id),
            )


def _storyboard_shot_payload(draft: dict) -> dict:
    characters = draft.get("characters")
    if isinstance(characters, str):
        characters = from_json(characters, [])
    description = draft.get("action_text") or draft.get("narrative_purpose") or ""
    return {
        "description": description,
        "action_text": draft.get("action_text") or "",
        "narrative_purpose": draft.get("narrative_purpose") or "",
        "characters": characters or [],
        "scene": draft.get("scene") or "",
        "camera_motion": draft.get("camera_motion") or "",
        "visual_prompt": draft.get("visual_prompt") or "",
        "duration_seconds": int(draft.get("duration_seconds") or 5),
        "video_mode": draft.get("video_mode") or "t2v",
        "provider": None,
        "model": None,
        "bible_character": draft.get("bible_character"),
        "bible_scene": draft.get("bible_scene"),
        "source_excerpt": draft.get("source_excerpt") or "",
        "source_start": draft.get("source_start"),
        "source_end": draft.get("source_end"),
        "source_type": "human_edit" if draft.get("source_type") == "human_edit" else "auto_draft",
        "rag": [
            {
                "kind": "adaptation",
                "label": "改编依据",
                "excerpt": draft.get("source_excerpt") or "",
                "score": 1,
                "start": draft.get("source_start"),
                "end": draft.get("source_end"),
            }
        ],
    }


def _insert_confirmed_version(
    conn,
    shot_id: str,
    payload: dict,
    now: str,
    *,
    version_id: str | None = None,
    version_number: int | None = None,
    change_summary: str,
) -> str:
    version_id = version_id or f"version_{uuid.uuid4().hex[:10]}"
    if version_number is None:
        version_number = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 AS n FROM shot_versions WHERE shot_id = ?",
            (shot_id,),
        ).fetchone()["n"]
    conn.execute(
        """
        INSERT INTO shot_versions
        (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
         first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at,
         camera_motion, duration_seconds, reference_frame_path, change_summary)
        VALUES (?, ?, ?, ?, ?, '', '', NULL, NULL, NULL, ?, ?, ?, 'storyboard_confirm', ?, ?, ?, NULL, ?)
        """,
        (
            version_id,
            shot_id,
            version_number,
            payload["description"],
            payload["visual_prompt"],
            payload["video_mode"],
            payload.get("provider"),
            payload.get("model"),
            now,
            payload["camera_motion"],
            payload["duration_seconds"],
            change_summary,
        ),
    )
    return version_id


def _update_promoted_shot(conn, project_id: str, shot_id: str, draft: dict, payload: dict, version_id: str, now: str) -> None:
    conn.execute(
        """
        UPDATE shots SET title=?, description=?, characters=?, scene=?, camera_motion=?, visual_prompt=?,
          rag_evidence=?, narrative_purpose=?, action_text=?, duration_seconds=?, bible_character=?,
          bible_scene=?, source_excerpt=?, source_start=?, source_end=?, source_type=?, review_status=?,
          current_version_id=?, updated_at=?
        WHERE id=? AND project_id=?
        """,
        (
            draft["title"],
            payload["description"],
            to_json(payload["characters"]),
            payload["scene"],
            payload["camera_motion"],
            payload["visual_prompt"],
            to_json(payload["rag"]),
            payload["narrative_purpose"],
            payload["action_text"],
            payload["duration_seconds"],
            payload["bible_character"],
            payload["bible_scene"],
            payload["source_excerpt"],
            payload["source_start"],
            payload["source_end"],
            payload["source_type"],
            "confirmed",
            version_id,
            now,
            shot_id,
            project_id,
        ),
    )
    changed = conn.execute("SELECT changes() AS n").fetchone()["n"]
    if int(changed or 0) != 1:
        raise AdaptationError("SHOT_MISMATCH", "未能更新属于当前项目的镜头，确认分镜已中止。")


def _upsert_promoted_draft(conn, project_id: str, shot_id: str, payload: dict, now: str) -> None:
    existing = conn.execute("SELECT shot_id FROM shot_drafts WHERE shot_id = ? AND project_id = ?", (shot_id, project_id)).fetchone()
    values = (
        payload["description"],
        payload["camera_motion"],
        payload["visual_prompt"],
        "",
        "",
        payload["video_mode"],
        payload.get("provider"),
        payload.get("model"),
        payload["duration_seconds"],
        None,
        None,
        None,
        now,
    )
    if existing:
        conn.execute(
            """
            UPDATE shot_drafts SET
              description=?, camera_motion=?, visual_prompt=?, negative_prompt=?, audio_prompt=?,
              video_mode=?, provider=?, model=?, duration_seconds=?, first_frame_path=?, last_frame_path=?,
              reference_frame_path=?, updated_at=?
            WHERE shot_id=? AND project_id=?
            """,
            (*values, shot_id, project_id),
        )
    else:
        conn.execute(
            """
            INSERT INTO shot_drafts
            (shot_id, project_id, description, camera_motion, visual_prompt, negative_prompt, audio_prompt,
             video_mode, provider, model, duration_seconds, first_frame_path, last_frame_path, reference_frame_path, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (shot_id, project_id, *values),
        )


def _invalidate_bible_and_storyboard(project_id: str) -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute(
            "UPDATE story_bibles SET review_status = ?, updated_at = ? WHERE project_id = ?",
            ("stale", now, project_id),
        )
    _clear_storyboard(project_id)


def _clear_storyboard(project_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM storyboard_drafts WHERE project_id = ?", (project_id,))


def _merge_bible(current: dict, payload: dict) -> dict:
    merged = dict(current)
    for key in BIBLE_SCALAR_FIELDS:
        if key in payload:
            merged[key] = payload[key]
    for key in BIBLE_JSON_FIELDS:
        if key in payload:
            merged[key] = payload[key]
    if merged.get("adaptation_summary") and "summary" not in payload:
        merged["summary"] = merged["adaptation_summary"]
    return merged


def _upsert_bible_row(project_id: str, bible: dict) -> None:
    now = utc_now()
    with connect() as conn:
        existing = conn.execute("SELECT project_id FROM story_bibles WHERE project_id = ?", (project_id,)).fetchone()
        values = (
            bible.get("summary") or bible.get("adaptation_summary") or "",
            bible.get("worldview") or "",
            to_json(bible.get("style_tags") or []),
            to_json(bible.get("themes") or []),
            bible.get("logline") or "",
            bible.get("adaptation_summary") or bible.get("summary") or "",
            bible.get("emotion_curve") or "",
            bible.get("protagonist") or "",
            bible.get("protagonist_goal") or "",
            bible.get("obstacle") or "",
            to_json(bible.get("character_cards") or []),
            to_json(bible.get("scene_cards") or []),
            bible.get("visual_style") or "",
            bible.get("consistency_constraints") or "",
            bible.get("option_id"),
            bible.get("source") or "mock_planner",
            bible.get("review_status") or "draft",
            bible.get("scope_id"),
            now,
            project_id,
        )
        if existing:
            conn.execute(
                """
                UPDATE story_bibles SET
                  summary=?, worldview=?, style_tags=?, themes=?, logline=?, adaptation_summary=?, emotion_curve=?,
                  protagonist=?, protagonist_goal=?, obstacle=?, character_cards_json=?, scene_cards_json=?,
                  visual_style=?, consistency_constraints=?, option_id=?, source=?, review_status=?, scope_id=?, updated_at=?
                WHERE project_id=?
                """,
                values,
            )
        else:
            conn.execute(
                """
                INSERT INTO story_bibles
                (summary, worldview, style_tags, themes, logline, adaptation_summary, emotion_curve, protagonist,
                 protagonist_goal, obstacle, character_cards_json, scene_cards_json, visual_style,
                 consistency_constraints, option_id, source, review_status, scope_id, updated_at, project_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*values, now),
            )


def _sync_bible_cards(project_id: str, bible: dict) -> None:
    now = utc_now()
    characters = bible.get("character_cards") or []
    scenes = bible.get("scene_cards") or []
    with connect() as conn:
        for card in characters:
            name = card.get("name")
            if not name:
                continue
            row = conn.execute(
                "SELECT id, asset_id FROM characters WHERE project_id = ? AND name = ?",
                (project_id, name),
            ).fetchone()
            description = " / ".join(filter(None, [card.get("identity"), card.get("motivation"), card.get("invariant")]))
            visual = card.get("appearance") or ""
            if row:
                conn.execute(
                    "UPDATE characters SET role=?, description=?, visual_prompt=? WHERE id=?",
                    (card.get("role") or "", description, visual, row["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO characters (id, project_id, name, role, description, visual_prompt, asset_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (f"char_{uuid.uuid4().hex[:10]}", project_id, name, card.get("role") or "", description, visual, now),
                )
        for card in scenes:
            name = card.get("name")
            if not name:
                continue
            row = conn.execute(
                "SELECT id FROM scenes WHERE project_id = ? AND name = ?",
                (project_id, name),
            ).fetchone()
            description = " / ".join(filter(None, [card.get("environment"), card.get("time"), card.get("invariant")]))
            visual = card.get("visuals") or ""
            if row:
                conn.execute(
                    "UPDATE scenes SET description=?, visual_prompt=? WHERE id=?",
                    (description, visual, row["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO scenes (id, project_id, name, description, visual_prompt, asset_id, created_at)
                    VALUES (?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (f"scene_{uuid.uuid4().hex[:10]}", project_id, name, description, visual, now),
                )


def _load_bible(project_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM story_bibles WHERE project_id = ?", (project_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["style_tags"] = from_json(item.get("style_tags"), [])
    item["themes"] = from_json(item.get("themes"), [])
    item["character_cards"] = from_json(item.get("character_cards_json"), [])
    item["scene_cards"] = from_json(item.get("scene_cards_json"), [])
    return item


def _load_storyboard(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM storyboard_drafts WHERE project_id = ? ORDER BY shot_index",
            (project_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["characters"] = from_json(item.get("characters"), [])
        result.append(item)
    return result


def _record_review(project_id: str, stage: str, action: str, summary: str, target_id: str | None) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO review_records (id, project_id, stage, action, summary, target_id, target_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (f"rev_{uuid.uuid4().hex[:10]}", project_id, stage, action, summary, target_id, utc_now()),
        )


def _reuse_or_create_job(project_id: str, job_type: str, message: str, job_id: str | None = None) -> str:
    if job_id:
        return job_id
    for job in list_active_jobs(project_id):
        if job.get("type") == job_type and job.get("status") in {"queued", "running", "paused"}:
            return job["id"]
    return create_job(project_id, job_type, message)


def _fail_workflow(project_id: str, job_id: str | None, message: str, exc: Exception) -> None:
    update_project_status(project_id, "failed")
    if job_id:
        update_job(job_id, "failed", 100, message, redact_text(str(exc)), stage="failed")


def _has_production_shots(project_id: str) -> bool:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM shots WHERE project_id = ?", (project_id,)).fetchone()
    return int(row["n"] or 0) > 0


def _require_project(project_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise AdaptationError("PROJECT_NOT_FOUND", "项目不存在。")
    return dict(row)


def _require_option(project_id: str, option_id: str) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM adaptation_options WHERE id = ? AND project_id = ?",
            (option_id, project_id),
        ).fetchone()
    if not row:
        raise AdaptationError("OPTION_MISMATCH", "改编方案不属于当前项目，或尚未生成。请重新启动改编流程。")
    return dict(row)


def _stage_label(stage: str) -> str:
    return {"scope": "改编范围", "bible": "Story Bible", "storyboard": "分镜"}[stage]


def _apply_output_lineage(table: str, where_sql: str, where_args: tuple, lineage: dict) -> None:
    with connect() as conn:
        conn.execute(
            f"""
            UPDATE {table}
            SET provider=?, model=?, generation_mode=?, used_local_fallback=?, config_source=?, source=?
            WHERE {where_sql}
            """,
            (
                lineage.get("provider"),
                lineage.get("model"),
                lineage.get("generation_mode"),
                lineage.get("used_local_fallback") or 0,
                lineage.get("config_source"),
                lineage.get("source"),
                *where_args,
            ),
        )
