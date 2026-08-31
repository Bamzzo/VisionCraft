"""Backend orchestration pause/resume on top of existing P4 review checkpoints.

This is not a second state machine. It reuses project.status, jobs, and
workflow_checkpoints. Pause is only allowed at review nodes. waiting_remote
video tasks are never cancelled or resubmitted.
"""
from __future__ import annotations

from typing import Any

from ..database import connect
from ..services.checkpoint_service import (
    REVIEW_NODES,
    REVIEW_STATUSES,
    CheckpointError,
    complete_checkpoint,
    get_checkpoint,
    get_paused_checkpoint,
    list_checkpoints,
    public_checkpoint,
    save_workflow_checkpoint,
)
from ..services.job_service import create_job, list_active_jobs, update_job
from ..services.project_service import get_project, update_project_status

ADAPTATION_JOB_TYPES = {
    "adaptation_workflow",
    "adaptation_bible",
    "adaptation_storyboard",
    "adaptation_production",
    "adaptation_regen_scope",
    "adaptation_regen_bible",
    "adaptation_regen_storyboard",
    "medium_text_analysis",
    "medium_scope_regen",
}

PAST_SCOPE = {
    "awaiting_bible_review",
    "story_bible_ready",
    "awaiting_storyboard_review",
    "storyboard_draft_ready",
    "production_ready",
    "ready_for_review",
    "video_ready",
    "completed",
}
PAST_BIBLE = {
    "awaiting_storyboard_review",
    "storyboard_draft_ready",
    "production_ready",
    "ready_for_review",
    "video_ready",
    "completed",
}
PAST_STORYBOARD = {"production_ready", "ready_for_review", "video_ready", "completed"}
PAST_STORYLINE = PAST_SCOPE | {"awaiting_scope_review", "adaptation_options_ready"}


def attach_workflow_control(project: dict | None) -> dict | None:
    if not project:
        return project
    paused = get_paused_checkpoint(project["id"])
    public = public_checkpoint(paused) if paused else {}
    status = project.get("status") or "created"
    waiting_remote = has_waiting_remote_video(project["id"], project)
    project["checkpoint"] = public or None
    project["workflow"] = {
        "execution_status": status,
        "paused": bool(public) and status in REVIEW_STATUSES,
        "review_node": public.get("node") if public else None,
        "pause_reason": public.get("pause_reason") if public else "",
        "input_summary": public.get("input_summary") if public else "",
        "can_pause": status in REVIEW_STATUSES and not waiting_remote,
        "can_resume": bool(public) and (status in REVIEW_STATUSES or status == "failed"),
        "waiting_remote": waiting_remote,
        "confirmed_readonly": status in PAST_STORYBOARD,
    }
    return project


def start_or_reuse_workflow(project_id: str, *, run_now: bool = False) -> dict:
    project = _require_project(project_id)
    active = _active_adaptation_job(project_id)
    if active and active.get("status") in {"queued", "running"}:
        return {
            "job_id": active["id"],
            "status": project.get("status") or active.get("status"),
            "reused": True,
            "message": "改编任务已在进行中，已复用原任务。",
        }
    if (project.get("status") or "") in REVIEW_STATUSES:
        paused = get_paused_checkpoint(project_id)
        job_id = (paused or {}).get("job_id") or (active or {}).get("id")
        return {
            "job_id": job_id,
            "status": project["status"],
            "reused": True,
            "checkpoint": public_checkpoint(paused) if paused else None,
            "message": "当前已在审核节点暂停。请确认后继续，无需重新启动。",
        }
    job_id = create_job(project_id, "adaptation_workflow", "改编流程已排队")
    update_project_status(project_id, "running")
    if run_now:
        from ..workflow.adaptation_workflow import run_adaptation_workflow

        run_adaptation_workflow(project_id, job_id)
        refreshed = get_project(project_id) or {}
        return {
            "job_id": job_id,
            "status": refreshed.get("status") or "running",
            "reused": False,
            "checkpoint": refreshed.get("checkpoint"),
            "message": "改编流程已启动。",
        }
    return {
        "job_id": job_id,
        "status": "queued",
        "reused": False,
        "message": "改编流程已排队。",
    }


def pause_project(project_id: str) -> dict:
    project = _require_project(project_id)
    if has_waiting_remote_video(project_id, project):
        raise CheckpointError(
            "WAITING_REMOTE",
            "当前有云端视频任务正在查询原来的远程任务，不能中断或重新提交。暂停只用于审核节点。",
        )
    status = project.get("status") or ""
    if status not in REVIEW_STATUSES:
        raise CheckpointError(
            "NOT_REVIEW_NODE",
            "当前不在可暂停的审核节点。请先到达范围、Story Bible 或分镜审核后再暂停。云端视频任务不会被中断。",
        )
    paused = get_paused_checkpoint(project_id)
    if paused:
        job_id = paused["job_id"]
        update_job(job_id, "paused", int(_job_progress(job_id) or 28), paused.get("state", {}).get("pause_reason") or "审核节点已暂停", stage=status)
        return {
            "job_id": job_id,
            "status": status,
            "reused": True,
            "checkpoint": public_checkpoint(paused),
            "message": "已在当前审核节点暂停。",
        }
    job = _active_adaptation_job(project_id) or {}
    job_id = job.get("id") or create_job(project_id, "adaptation_workflow", "审核节点已暂停")
    from .checkpoint_service import NODE_FOR_STATUS, PAUSE_REASON

    node = NODE_FOR_STATUS.get(status, "scope_review")
    checkpoint_id = save_workflow_checkpoint(
        project_id,
        job_id,
        node,
        {
            "project_id": project_id,
            "job_id": job_id,
            "node": node,
            "stage": status,
            "option_id": project.get("selected_option_id"),
            "pause_reason": PAUSE_REASON.get(node),
            "input_summary": "审核节点暂停",
        },
    )
    update_job(job_id, "paused", 50, PAUSE_REASON.get(node, "审核节点已暂停"), stage=status)
    return {
        "job_id": job_id,
        "status": status,
        "reused": False,
        "checkpoint_id": checkpoint_id,
        "checkpoint": public_checkpoint(get_paused_checkpoint(project_id)),
        "message": "已在当前审核节点暂停。",
    }


def resume_project(project_id: str, checkpoint_id: str | None = None, job_id: str | None = None) -> dict:
    project = _require_project(project_id)
    if has_waiting_remote_video(project_id, project):
        raise CheckpointError(
            "WAITING_REMOTE",
            "当前有云端视频任务正在查询原来的远程任务。恢复不会重新提交视频，请等待原任务完成或在镜头页刷新同一任务。",
        )
    checkpoint = _load_resume_checkpoint(project_id, checkpoint_id)
    if job_id and checkpoint.get("job_id") and job_id != checkpoint["job_id"]:
        raise CheckpointError("JOB_MISMATCH", "任务与当前检查点不匹配。请从项目页恢复，不要混用其他任务。")
    node = checkpoint.get("node") or ""
    status = project.get("status") or ""
    if node not in REVIEW_NODES and node:
        raise CheckpointError("CHECKPOINT_INVALID", "当前检查点不是可恢复的审核节点。")
    if status == "production_ready" and node == "storyboard_review":
        return _idempotent_resume(project, checkpoint, "分镜已确认，无需重复进入制作。")
    if node == "scope_review" and status in PAST_SCOPE and _bible_ready(project):
        return _idempotent_resume(project, checkpoint, "改编范围已确认，不会倒退或重复生成 Story Bible。")
    if node == "bible_review" and status in PAST_BIBLE and _storyboard_ready(project):
        return _idempotent_resume(project, checkpoint, "Story Bible 已确认，不会倒退或重复生成分镜。")
    if node == "storyline_review" and status in PAST_STORYLINE and (project.get("adaptation_options") or []):
        return _idempotent_resume(project, checkpoint, "故事范围已确认，不会倒退或重复生成改编方案。")
    if node == "quality_gate":
        from ..workflow.langgraph_workflow import resume_langgraph_workflow

        resume_job = checkpoint["job_id"]
        resume_langgraph_workflow(project_id, resume_job)
        return {
            "job_id": resume_job,
            "status": "resuming",
            "reused": False,
            "checkpoint": public_checkpoint(checkpoint),
            "message": "正在从旧版监制检查点继续。",
        }
    return _resume_review_node(project_id, project, checkpoint)


def list_project_checkpoints(project_id: str) -> dict:
    _require_project(project_id)
    items = list_checkpoints(project_id)
    return {"items": items, "paused": public_checkpoint(get_paused_checkpoint(project_id)) or None}


def has_waiting_remote_video(project_id: str, project: dict | None = None) -> bool:
    tasks = (project or {}).get("video_tasks") if project else None
    if tasks is None:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM video_tasks
                WHERE project_id = ? AND status IN ('running', 'pending_remote', 'submitted')
                """,
                (project_id,),
            ).fetchone()
        jobs = list_active_jobs(project_id)
        return int(row["n"] or 0) > 0 or any(job.get("status") == "waiting_remote" for job in jobs)
    if any(task.get("status") in {"running", "pending_remote", "submitted"} for task in tasks):
        return True
    jobs = project.get("active_jobs") or project.get("jobs") or []
    return any(job.get("status") == "waiting_remote" for job in jobs)


def _resume_review_node(project_id: str, project: dict, checkpoint: dict) -> dict:
    node = checkpoint.get("node")
    if node == "storyline_review":
        from .medium_text_service import MediumTextError, confirm_adaptation_scope

        try:
            state = confirm_adaptation_scope(project_id)
        except MediumTextError as exc:
            raise CheckpointError(exc.code, str(exc)) from exc
        return _resume_result(project_id, checkpoint, state, "已从故事线审核继续。")
    if node == "scope_review":
        from .adaptation_service import AdaptationError, confirm_scope

        try:
            state = confirm_scope(project_id, (checkpoint.get("state") or {}).get("option_id") or project.get("selected_option_id"))
        except AdaptationError as exc:
            raise CheckpointError(exc.code, str(exc)) from exc
        return _resume_result(project_id, checkpoint, state, "已确认范围并继续生成 Story Bible。")
    if node == "bible_review":
        from .adaptation_service import AdaptationError, confirm_bible

        try:
            state = confirm_bible(project_id)
        except AdaptationError as exc:
            raise CheckpointError(exc.code, str(exc)) from exc
        return _resume_result(project_id, checkpoint, state, "已确认 Story Bible 并继续生成分镜。")
    if node == "storyboard_review":
        from .adaptation_service import AdaptationError, confirm_storyboard

        try:
            state = confirm_storyboard(project_id)
        except AdaptationError as exc:
            raise CheckpointError(exc.code, str(exc)) from exc
        return _resume_result(project_id, checkpoint, state, "已确认分镜并进入镜头制作。")
    raise CheckpointError("CHECKPOINT_INVALID", "无法从当前检查点继续。请在审核面板确认当前步骤。")


def _resume_result(project_id: str, checkpoint: dict, state: dict, message: str) -> dict:
    refreshed = get_project(project_id) or state
    return {
        "job_id": checkpoint.get("job_id"),
        "status": refreshed.get("status"),
        "reused": bool(state.get("resumed_idempotent")),
        "checkpoint": refreshed.get("checkpoint"),
        "project": refreshed,
        "message": message if not state.get("resumed_idempotent") else (state.get("resume_message") or message),
    }


def _idempotent_resume(project: dict, checkpoint: dict, message: str) -> dict:
    if checkpoint.get("id") and (project.get("status") or "") in PAST_STORYBOARD:
        complete_checkpoint(checkpoint["id"])
    return {
        "job_id": checkpoint.get("job_id"),
        "status": project.get("status"),
        "reused": True,
        "checkpoint": project.get("checkpoint"),
        "message": message,
        "project": project,
    }


def _load_resume_checkpoint(project_id: str, checkpoint_id: str | None) -> dict:
    if checkpoint_id:
        item = get_checkpoint(checkpoint_id)
        if not item:
            raise CheckpointError("CHECKPOINT_NOT_FOUND", "检查点不存在。请刷新项目后，从当前审核步骤继续。")
        if item.get("project_id") != project_id:
            raise CheckpointError("CHECKPOINT_MISMATCH", "检查点不属于当前项目。请切换到正确项目后再恢复。")
        if item.get("status") not in {"paused", "completed", "superseded"}:
            raise CheckpointError("CHECKPOINT_INVALID", "该检查点已失效。请使用当前暂停的审核节点。")
        if item.get("status") == "completed":
            paused = get_paused_checkpoint(project_id)
            if paused:
                return paused
        return item
    paused = get_paused_checkpoint(project_id)
    if not paused:
        raise CheckpointError("NO_CHECKPOINT", "没有可恢复的审核检查点。请先启动改编流程，并在审核节点确认。")
    return paused


def _require_project(project_id: str) -> dict:
    project = get_project(project_id)
    if not project:
        raise CheckpointError("PROJECT_NOT_FOUND", "项目不存在。")
    return project


def _active_adaptation_job(project_id: str) -> dict | None:
    jobs = list_active_jobs(project_id)
    for job in jobs:
        if job.get("type") in ADAPTATION_JOB_TYPES:
            return job
    return None


def _job_progress(job_id: str) -> int | None:
    with connect() as conn:
        row = conn.execute("SELECT progress FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return int(row["progress"]) if row else None


def _bible_ready(project: dict) -> bool:
    bible = project.get("story_bible") or {}
    return bool(bible) and bible.get("review_status") != "stale"


def _storyboard_ready(project: dict) -> bool:
    drafts = project.get("storyboard_drafts") or []
    return bool(drafts) and not all(item.get("review_status") == "stale" for item in drafts)
