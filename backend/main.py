import asyncio
import json

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import FRONTEND_DIR, PROJECTS_DIR, init_environment
from .database import connect, init_db
from .providers.capabilities import CapabilityError, get_provider_capabilities, get_provider_diagnostics
from .providers.llm_provider import live_llm_available
from .schemas import (
    AdaptationRegenerateRequest,
    AdaptationSelectRequest,
    AssemblySettingsUpdate,
    DemoCleanupRequest,
    FeedbackCreate,
    GenerationModeUpdate,
    KeyframeRedrawRequest,
    KeyframeSelectRequest,
    MediumRegenerateRequest,
    MediumScopeSaveRequest,
    MediumStorylineSelectRequest,
    ProjectCreate,
    ShotDraftUpdate,
    StageModelConfigUpdate,
    StoryboardSaveRequest,
    StoryBibleUpdate,
    VideoGenerateRequest,
    VisionReviewRequest,
)
from .services.adaptation_service import (
    AdaptationError,
    assert_batch_generation_allowed,
    confirm_bible,
    confirm_scope,
    confirm_storyboard,
    generate_storyboard,
    get_adaptation_state,
    list_adaptation_options,
    regenerate_stage,
    save_story_bible_draft,
    save_storyboard_drafts,
    select_adaptation_option,
)
from .services.medium_text_service import (
    MediumTextError,
    apply_recommended_scope,
    confirm_adaptation_scope,
    medium_text_state,
    regenerate_medium,
    run_medium_analysis,
    save_adaptation_scope,
    select_storyline,
)
from .services.export_service import build_markdown
from .services.feedback_service import apply_feedback
from .services.job_service import collect_sse_opening, create_job, format_sse, get_job, get_job_events, job_center_snapshot, list_active_jobs
from .services.keyframe_service import redraw_shot_keyframes, select_shot_keyframes
from .services.asset_upload_service import (
    MAX_AUDIO_BYTES,
    MAX_IMAGE_BYTES,
    AssetUploadError,
    public_asset_payload,
    upload_project_asset,
)
from .services.local_keyframe_service import LocalKeyframeError, register_local_first_frame
from .services.memory_service import index_project_memory, search_project_memory
from .services.model_config_service import list_stage_configs, save_stage_config, set_generation_mode
from .providers.llm_catalog import ModelConfigError
from .providers.vision_adapter import VisionAdapterError
from .services.vision_review_service import review_project_image
from .services.project_service import (
    ProjectSettingsError,
    cleanup_demo_data,
    create_project,
    delete_project,
    get_project,
    list_projects,
    update_project_settings,
)
from .services.shot_edit_service import (
    ShotEditError,
    freeze_shot_version,
    get_shot_editor,
    prepare_version_for_generation,
    rollback_shot_to_version,
    save_shot_draft,
)
from .services.video_service import (
    AssemblyError,
    assemble_project_video,
    enqueue_project_assembly,
    generate_project_videos,
    generate_shot_video,
    get_assembly_settings_payload,
    get_assembly_status,
    prepare_shot_video_generation,
    refresh_project_video_tasks,
    save_assembly_settings,
    safe_retry_shot_video,
)
from .services.checkpoint_service import CheckpointError
from .services.workflow_control_service import (
    list_project_checkpoints,
    pause_project,
    resume_project,
    start_or_reuse_workflow,
)
from .workflow.adaptation_workflow import run_adaptation_workflow


init_environment()
init_db()

app = FastAPI(title="VisionCraft API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/assets", StaticFiles(directory=PROJECTS_DIR), name="assets")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "mode": "live-ready" if live_llm_available() else "mock-ready", "llm_live": live_llm_available()}


@app.get("/api/providers/capabilities")
def provider_capabilities() -> dict:
    return get_provider_capabilities()


@app.get("/api/providers/diagnostics")
def provider_diagnostics() -> dict:
    return get_provider_diagnostics()


def _raise_model_config(exc: ModelConfigError | VisionAdapterError) -> None:
    status = 404 if getattr(exc, "code", "") in {"PROJECT_NOT_FOUND"} else 400
    raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/model-configs")
def get_model_configs_endpoint(project_id: str) -> dict:
    try:
        return list_stage_configs(project_id)
    except ModelConfigError as exc:
        _raise_model_config(exc)


@app.put("/api/projects/{project_id}/model-configs/{stage}")
def put_model_config_endpoint(project_id: str, stage: str, payload: StageModelConfigUpdate) -> dict:
    try:
        return save_stage_config(
            project_id,
            stage,
            provider=payload.provider,
            model=payload.model,
            parameters=payload.parameters,
            workflow_run_id=payload.workflow_run_id,
        )
    except ModelConfigError as exc:
        _raise_model_config(exc)


@app.put("/api/projects/{project_id}/generation-mode")
def put_generation_mode_endpoint(project_id: str, payload: GenerationModeUpdate) -> dict:
    try:
        return set_generation_mode(project_id, payload.generation_mode)
    except ModelConfigError as exc:
        _raise_model_config(exc)


@app.post("/api/projects/{project_id}/vision-review")
def vision_review_endpoint(project_id: str, payload: VisionReviewRequest) -> dict:
    try:
        return review_project_image(
            project_id,
            asset_id=payload.asset_id,
            asset_path=payload.asset_path,
            role=payload.role or "keyframe",
        )
    except (ModelConfigError, VisionAdapterError) as exc:
        _raise_model_config(exc)


@app.post("/api/projects")
def create_project_endpoint(payload: ProjectCreate) -> dict:
    return create_project(payload)


@app.get("/api/projects")
def list_projects_endpoint(include_archived: bool = Query(default=False)) -> list[dict]:
    return list_projects(include_archived=include_archived)


@app.get("/api/projects/{project_id}")
def get_project_endpoint(project_id: str) -> dict:
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.patch("/api/projects/{project_id}")
def patch_project_endpoint(project_id: str, payload: dict = Body(default={})) -> dict:
    try:
        return update_project_settings(project_id, payload or {})
    except ProjectSettingsError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@app.delete("/api/projects/{project_id}")
def delete_project_endpoint(project_id: str) -> dict:
    return {"deleted": delete_project(project_id)}


@app.post("/api/projects/{project_id}/run")
def run_project_endpoint(project_id: str, background_tasks: BackgroundTasks) -> dict:
    try:
        result = start_or_reuse_workflow(project_id, run_now=False)
    except CheckpointError as exc:
        _raise_checkpoint(exc)
    if not result.get("reused"):
        background_tasks.add_task(run_adaptation_workflow, project_id, result["job_id"])
    return result


@app.post("/api/projects/{project_id}/pause")
def pause_project_endpoint(project_id: str) -> dict:
    try:
        return pause_project(project_id)
    except CheckpointError as exc:
        _raise_checkpoint(exc)


@app.post("/api/projects/{project_id}/resume")
def resume_project_endpoint(project_id: str) -> dict:
    try:
        return resume_project(project_id)
    except CheckpointError as exc:
        _raise_checkpoint(exc)


@app.get("/api/projects/{project_id}/checkpoints")
def list_checkpoints_endpoint(project_id: str) -> dict:
    try:
        return list_project_checkpoints(project_id)
    except CheckpointError as exc:
        _raise_checkpoint(exc)


@app.post("/api/projects/{project_id}/checkpoints/{checkpoint_id}/resume")
def resume_checkpoint_endpoint(project_id: str, checkpoint_id: str) -> dict:
    try:
        return resume_project(project_id, checkpoint_id=checkpoint_id)
    except CheckpointError as exc:
        _raise_checkpoint(exc)


@app.post("/api/projects/{project_id}/retry")
def retry_project_endpoint(project_id: str, background_tasks: BackgroundTasks) -> dict:
    if not get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    job_id = create_job(project_id, "adaptation_regen_scope", "改编范围重生成已排队")
    background_tasks.add_task(regenerate_stage, project_id, "scope", job_id)
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
def get_job_endpoint(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/projects/{project_id}/job-events")
def list_project_job_events(
    project_id: str,
    after_id: int = Query(default=0, ge=0),
    job_id: str | None = Query(default=None),
) -> dict:
    if not _project_exists(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    snapshot = job_center_snapshot(project_id, after_id=after_id)
    if job_id:
        snapshot["events"] = get_job_events(project_id, after_id=after_id, job_id=job_id)
    return snapshot


@app.get("/api/projects/{project_id}/events")
async def project_events(
    project_id: str,
    after_id: int = Query(default=0, ge=0),
    once: bool = Query(default=False),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    if not _project_exists(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    cursor = after_id
    if last_event_id and last_event_id.isdigit():
        cursor = max(cursor, int(last_event_id))

    async def event_stream():
        last_id = cursor
        for frame in collect_sse_opening(project_id, after_id=last_id):
            yield frame
            if frame.startswith("id:"):
                try:
                    last_id = max(last_id, int(frame.split("\n", 1)[0].split(":", 1)[1].strip()))
                except ValueError:
                    pass
        if once:
            return
        for _ in range(3600):
            if not _project_exists(project_id):
                yield "event: error\ndata: {\"message\":\"项目不存在\"}\n\n"
                return
            events = get_job_events(project_id, after_id=last_id)
            if events:
                for event in events:
                    yield format_sse(event)
                    last_id = event["id"]
            else:
                yield ": ping\n\n"
            await asyncio.sleep(0.8)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _raise_shot_edit(exc: ShotEditError) -> None:
    status = 404 if exc.code in {"PROJECT_NOT_FOUND", "SHOT_NOT_FOUND", "VERSION_NOT_FOUND"} else 400
    raise HTTPException(status_code=status, detail=str(exc)) from exc


def _raise_adaptation(exc: AdaptationError) -> None:
    status = 404 if exc.code in {"PROJECT_NOT_FOUND"} else 400
    raise HTTPException(status_code=status, detail=str(exc)) from exc


def _raise_checkpoint(exc: CheckpointError) -> None:
    status = 404 if exc.code in {"PROJECT_NOT_FOUND", "CHECKPOINT_NOT_FOUND"} else 400
    raise HTTPException(status_code=status, detail=str(exc)) from exc


def _raise_medium(exc: MediumTextError) -> None:
    status = 404 if exc.code in {"PROJECT_NOT_FOUND"} else 400
    raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/adaptation")
def get_adaptation_endpoint(project_id: str) -> dict:
    try:
        return get_adaptation_state(project_id)
    except AdaptationError as exc:
        _raise_adaptation(exc)


@app.get("/api/projects/{project_id}/adaptation/options")
def list_adaptation_options_endpoint(project_id: str) -> dict:
    try:
        return {"items": list_adaptation_options(project_id)}
    except AdaptationError as exc:
        _raise_adaptation(exc)


@app.post("/api/projects/{project_id}/adaptation/options/{option_id}/select")
def select_adaptation_option_endpoint(project_id: str, option_id: str) -> dict:
    try:
        return select_adaptation_option(project_id, option_id)
    except AdaptationError as exc:
        _raise_adaptation(exc)


@app.post("/api/projects/{project_id}/adaptation/scope/confirm")
def confirm_scope_endpoint(project_id: str, payload: AdaptationSelectRequest | None = None) -> dict:
    try:
        return confirm_scope(project_id, payload.option_id if payload else None)
    except AdaptationError as exc:
        _raise_adaptation(exc)


@app.put("/api/projects/{project_id}/adaptation/bible")
def save_bible_endpoint(project_id: str, payload: StoryBibleUpdate) -> dict:
    try:
        return save_story_bible_draft(project_id, payload.model_dump(exclude_unset=True))
    except AdaptationError as exc:
        _raise_adaptation(exc)


@app.post("/api/projects/{project_id}/adaptation/bible/confirm")
def confirm_bible_endpoint(project_id: str, payload: StoryBibleUpdate | None = None) -> dict:
    try:
        return confirm_bible(project_id, payload.model_dump(exclude_unset=True) if payload else None)
    except AdaptationError as exc:
        _raise_adaptation(exc)


@app.post("/api/projects/{project_id}/adaptation/storyboard")
def generate_storyboard_endpoint(project_id: str) -> dict:
    try:
        return generate_storyboard(project_id)
    except AdaptationError as exc:
        _raise_adaptation(exc)


@app.put("/api/projects/{project_id}/adaptation/storyboard")
def save_storyboard_endpoint(project_id: str, payload: StoryboardSaveRequest) -> dict:
    try:
        return save_storyboard_drafts(project_id, payload.shots)
    except AdaptationError as exc:
        _raise_adaptation(exc)


@app.post("/api/projects/{project_id}/adaptation/storyboard/confirm")
def confirm_storyboard_endpoint(project_id: str, payload: StoryboardSaveRequest | None = None) -> dict:
    try:
        return confirm_storyboard(project_id, payload.shots if payload else None)
    except AdaptationError as exc:
        _raise_adaptation(exc)


@app.post("/api/projects/{project_id}/adaptation/regenerate")
def regenerate_adaptation_endpoint(project_id: str, payload: AdaptationRegenerateRequest) -> dict:
    try:
        return regenerate_stage(project_id, payload.stage)
    except AdaptationError as exc:
        _raise_adaptation(exc)


@app.get("/api/projects/{project_id}/medium-text")
def get_medium_text_endpoint(project_id: str) -> dict:
    try:
        return medium_text_state(project_id)
    except MediumTextError as exc:
        _raise_medium(exc)


@app.post("/api/projects/{project_id}/medium-text/analyze")
def analyze_medium_text_endpoint(project_id: str) -> dict:
    try:
        return run_medium_analysis(project_id, regenerate=True)
    except MediumTextError as exc:
        _raise_medium(exc)


@app.post("/api/projects/{project_id}/medium-text/storylines/{storyline_id}/select")
def select_storyline_endpoint(project_id: str, storyline_id: str) -> dict:
    try:
        return select_storyline(project_id, storyline_id)
    except MediumTextError as exc:
        _raise_medium(exc)


@app.post("/api/projects/{project_id}/medium-text/storylines/select")
def select_storyline_body_endpoint(project_id: str, payload: MediumStorylineSelectRequest) -> dict:
    try:
        return select_storyline(project_id, payload.storyline_id)
    except MediumTextError as exc:
        _raise_medium(exc)


@app.put("/api/projects/{project_id}/medium-text/scope")
def save_medium_scope_endpoint(project_id: str, payload: MediumScopeSaveRequest) -> dict:
    try:
        return save_adaptation_scope(
            project_id,
            storyline_id=payload.storyline_id,
            event_ids=payload.event_ids,
            chunk_ids=payload.chunk_ids,
            user_note=payload.user_note,
        )
    except MediumTextError as exc:
        _raise_medium(exc)


@app.post("/api/projects/{project_id}/medium-text/scope/recommend")
def recommend_medium_scope_endpoint(project_id: str) -> dict:
    try:
        return apply_recommended_scope(project_id)
    except MediumTextError as exc:
        _raise_medium(exc)


@app.post("/api/projects/{project_id}/medium-text/scope/confirm")
def confirm_medium_scope_endpoint(project_id: str, payload: MediumScopeSaveRequest | None = None) -> dict:
    try:
        data = payload.model_dump(exclude_unset=True) if payload else {}
        return confirm_adaptation_scope(
            project_id,
            storyline_id=data.get("storyline_id"),
            event_ids=data.get("event_ids"),
            chunk_ids=data.get("chunk_ids"),
            user_note=data.get("user_note"),
        )
    except MediumTextError as exc:
        _raise_medium(exc)


@app.post("/api/projects/{project_id}/medium-text/regenerate")
def regenerate_medium_endpoint(project_id: str, payload: MediumRegenerateRequest) -> dict:
    try:
        return regenerate_medium(project_id, payload.stage)
    except MediumTextError as exc:
        _raise_medium(exc)


@app.post("/api/projects/{project_id}/shots/{shot_id}/feedback")
def feedback_endpoint(project_id: str, shot_id: str, payload: FeedbackCreate) -> dict:
    if not get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return apply_feedback(project_id, shot_id, payload.user_text)


@app.post("/api/projects/{project_id}/shots/{shot_id}/versions/{version_id}/rollback")
def rollback_version_endpoint(project_id: str, shot_id: str, version_id: str) -> dict:
    try:
        rollback_shot_to_version(project_id, shot_id, version_id)
    except ShotEditError as exc:
        _raise_shot_edit(exc)
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="找不到可回滚的镜头版本。")
    return project


@app.get("/api/projects/{project_id}/shots/{shot_id}/editor")
def shot_editor_endpoint(project_id: str, shot_id: str) -> dict:
    try:
        return get_shot_editor(project_id, shot_id)
    except ShotEditError as exc:
        _raise_shot_edit(exc)


@app.put("/api/projects/{project_id}/shots/{shot_id}/draft")
def save_shot_draft_endpoint(project_id: str, shot_id: str, payload: ShotDraftUpdate) -> dict:
    try:
        return save_shot_draft(project_id, shot_id, payload.model_dump(exclude_unset=True))
    except ShotEditError as exc:
        _raise_shot_edit(exc)


@app.post("/api/projects/{project_id}/shots/{shot_id}/versions")
def freeze_shot_version_endpoint(project_id: str, shot_id: str, payload: ShotDraftUpdate | None = None) -> dict:
    try:
        return freeze_shot_version(
            project_id,
            shot_id,
            payload.model_dump(exclude_unset=True) if payload else None,
        )
    except ShotEditError as exc:
        _raise_shot_edit(exc)


@app.post("/api/projects/{project_id}/shots/{shot_id}/keyframes/select")
def select_keyframes_endpoint(project_id: str, shot_id: str, payload: KeyframeSelectRequest) -> dict:
    if not get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return select_shot_keyframes(project_id, shot_id, payload.first_frame_path, payload.last_frame_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/assets/upload")
async def upload_project_asset_endpoint(
    project_id: str,
    asset_role: str = Form(...),
    shot_id: str | None = Form(default=None),
    subtitle_text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> dict:
    content = None
    if file is not None:
        chunks: list[bytes] = []
        total = 0
        while True:
            piece = await file.read(1024 * 1024)
            if not piece:
                break
            total += len(piece)
            if total > MAX_AUDIO_BYTES:
                raise HTTPException(status_code=400, detail="上传文件超过大小限制。")
            chunks.append(piece)
        content = b"".join(chunks)
    try:
        result = upload_project_asset(
            project_id,
            asset_role=asset_role,
            content=content,
            filename=(file.filename if file else "") or "",
            shot_id=shot_id or None,
            subtitle_text=subtitle_text,
        )
    except AssetUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail="上传失败，请检查文件后重试。") from exc
    return {"ok": True, "asset": public_asset_payload(result["asset"]), "shot": result.get("shot")}


@app.post("/api/projects/{project_id}/shots/{shot_id}/keyframes/register-local")
async def register_local_keyframe_endpoint(
    project_id: str,
    shot_id: str,
    file: UploadFile = File(...),
) -> dict:
    content = await file.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="上传文件超过大小限制。图片不超过 20 MB。")
    try:
        return register_local_first_frame(project_id, shot_id, content, filename=file.filename or "")
    except LocalKeyframeError as exc:
        status = 404 if exc.code in {"PROJECT_NOT_FOUND", "SHOT_NOT_FOUND"} else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/shots/{shot_id}/keyframes/redraw")
def redraw_keyframes_endpoint(project_id: str, shot_id: str, payload: KeyframeRedrawRequest) -> dict:
    if not get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return redraw_shot_keyframes(project_id, shot_id, payload.target)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/shots/{shot_id}/video")
def generate_video_endpoint(
    project_id: str,
    shot_id: str,
    background_tasks: BackgroundTasks,
    payload: VideoGenerateRequest | None = None,
) -> dict:
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not any(shot["id"] == shot_id for shot in project.get("shots", [])):
        raise HTTPException(status_code=404, detail="Shot not found")
    fields = payload.model_dump(exclude_unset=True) if payload else {}
    try:
        frozen = prepare_version_for_generation(
            project_id,
            shot_id,
            fields,
            fields.get("version_id"),
        )
        prepared = prepare_shot_video_generation(
            project_id,
            shot_id,
            video_mode=frozen.get("video_mode") or "t2v",
            provider=frozen.get("provider"),
            model=frozen.get("model"),
            duration_seconds=frozen.get("duration_seconds"),
            version_id=frozen["id"],
            allow_fork=False,
        )
    except ShotEditError as exc:
        _raise_shot_edit(exc)
    except CapabilityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job_id = create_job(
        project_id,
        "video_generation",
        f"{prepared['provider_label']} / {prepared['model_label']} 已排队",
        shot_id=shot_id,
    )
    background_tasks.add_task(
        generate_shot_video,
        project_id,
        shot_id,
        job_id,
        prepared["video_mode"],
        prepared["provider"],
        prepared["model"],
        prepared["duration_seconds"],
        prepared["version_id"],
        False,
    )
    return {
        "job_id": job_id,
        "status": "queued",
        "provider": prepared["provider"],
        "model": prepared["model"],
        "video_mode": prepared["video_mode"],
        "version_id": prepared["version_id"],
    }


@app.post("/api/projects/{project_id}/shots/{shot_id}/video/safe-retry")
def safe_retry_video_endpoint(project_id: str, shot_id: str, background_tasks: BackgroundTasks) -> dict:
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not any(shot["id"] == shot_id for shot in project.get("shots", [])):
        raise HTTPException(status_code=404, detail="Shot not found")
    job_id = create_job(project_id, "video_safety_retry", "安全改写重试已排队", shot_id=shot_id)
    background_tasks.add_task(safe_retry_shot_video, project_id, shot_id, job_id)
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/projects/{project_id}/videos")
def generate_all_videos_endpoint(project_id: str, background_tasks: BackgroundTasks) -> dict:
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        assert_batch_generation_allowed(project)
    except AdaptationError as exc:
        _raise_adaptation(exc)
    if not project.get("shots"):
        raise HTTPException(status_code=400, detail="没有可生成的制作镜头。请先确认分镜。")
    job_id = create_job(project_id, "batch_video_generation", "批量视频生成已排队")
    background_tasks.add_task(generate_project_videos, project_id, job_id)
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/projects/{project_id}/videos/refresh")
def refresh_video_tasks_endpoint(project_id: str, background_tasks: BackgroundTasks) -> dict:
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    job_id = create_job(project_id, "video_task_refresh", "正在回查同一云端任务，不会重复提交或重复计费")
    background_tasks.add_task(refresh_project_video_tasks, project_id, job_id)
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/projects/{project_id}/assembly")
def assembly_status_endpoint(project_id: str) -> dict:
    if not get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return get_assembly_status(project_id)


@app.get("/api/projects/{project_id}/assembly-settings")
def get_assembly_settings_endpoint(project_id: str) -> dict:
    if not get_project(project_id):
        raise HTTPException(status_code=404, detail="项目不存在。")
    return get_assembly_settings_payload(project_id)


@app.put("/api/projects/{project_id}/assembly-settings")
def put_assembly_settings_endpoint(project_id: str, payload: AssemblySettingsUpdate) -> dict:
    if not get_project(project_id):
        raise HTTPException(status_code=404, detail="项目不存在。")
    try:
        return save_assembly_settings(project_id, payload.model_dump())
    except AssemblyError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc


@app.post("/api/projects/{project_id}/assemble")
def assemble_video_endpoint(project_id: str, background_tasks: BackgroundTasks) -> dict:
    if not get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        plan = enqueue_project_assembly(project_id)
    except AssemblyError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    if not plan.get("reused"):
        background_tasks.add_task(assemble_project_video, project_id, plan["job_id"])
    return plan


@app.post("/api/projects/demo/cleanup")
def demo_cleanup_endpoint(payload: DemoCleanupRequest | None = None) -> dict:
    payload = payload or DemoCleanupRequest()
    return cleanup_demo_data(
        keep_project_id=payload.keep_project_id,
        archive_failed=payload.archive_failed,
        remove_invalid_videos=payload.remove_invalid_videos,
    )


@app.post("/api/projects/{project_id}/memory/index")
def index_memory_endpoint(project_id: str) -> dict:
    if not get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {"indexed": index_project_memory(project_id)}


@app.get("/api/projects/{project_id}/memory/search")
def search_memory_endpoint(project_id: str, q: str = Query(min_length=1), limit: int = Query(default=6, ge=1, le=20)) -> dict:
    if not get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {"items": search_project_memory(project_id, q, limit)}


@app.get("/api/projects/{project_id}/export/json")
def export_json(project_id: str) -> JSONResponse:
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return JSONResponse(project)


@app.get("/api/projects/{project_id}/export/markdown")
def export_markdown(project_id: str) -> PlainTextResponse:
    markdown = build_markdown(project_id)
    if not markdown:
        raise HTTPException(status_code=404, detail="Project not found")
    return PlainTextResponse(markdown, media_type="text/markdown; charset=utf-8")


def _project_exists(project_id: str) -> bool:
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone()
    return row is not None


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
