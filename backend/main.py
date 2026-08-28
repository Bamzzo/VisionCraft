import asyncio
import json

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import FRONTEND_DIR, PROJECTS_DIR, init_environment
from .database import init_db
from .providers.capabilities import CapabilityError, get_provider_capabilities, get_provider_diagnostics
from .providers.llm_provider import live_llm_available
from .schemas import DemoCleanupRequest, FeedbackCreate, KeyframeRedrawRequest, KeyframeSelectRequest, ProjectCreate, VideoGenerateRequest
from .services.export_service import build_markdown
from .services.feedback_service import apply_feedback
from .services.job_service import create_job, get_job
from .services.keyframe_service import redraw_shot_keyframes, select_shot_keyframes
from .services.memory_service import index_project_memory, search_project_memory
from .services.project_service import cleanup_demo_data, create_project, delete_project, get_project, list_projects, rollback_shot_version
from .services.video_service import assemble_project_video, generate_project_videos, generate_shot_video, prepare_shot_video_generation, refresh_project_video_tasks, safe_retry_shot_video
from .services.checkpoint_service import get_paused_checkpoint
from .workflow.langgraph_workflow import resume_langgraph_workflow, run_langgraph_workflow


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


@app.delete("/api/projects/{project_id}")
def delete_project_endpoint(project_id: str) -> dict:
    return {"deleted": delete_project(project_id)}


@app.post("/api/projects/{project_id}/run")
def run_project_endpoint(project_id: str, background_tasks: BackgroundTasks) -> dict:
    if not get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    job_id = create_job(project_id, "full_workflow", "Workflow queued")
    background_tasks.add_task(run_langgraph_workflow, project_id, job_id)
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/projects/{project_id}/resume")
def resume_project_endpoint(project_id: str, background_tasks: BackgroundTasks) -> dict:
    if not get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    checkpoint = get_paused_checkpoint(project_id)
    if not checkpoint:
        raise HTTPException(status_code=400, detail="No paused workflow checkpoint")
    job_id = checkpoint["job_id"]
    background_tasks.add_task(resume_langgraph_workflow, project_id, job_id)
    return {"job_id": job_id, "status": "resuming"}


@app.post("/api/projects/{project_id}/retry")
def retry_project_endpoint(project_id: str, background_tasks: BackgroundTasks) -> dict:
    if not get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    job_id = create_job(project_id, "full_workflow_retry", "Workflow retry queued")
    background_tasks.add_task(run_langgraph_workflow, project_id, job_id)
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
def get_job_endpoint(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/projects/{project_id}/events")
async def project_events(project_id: str) -> StreamingResponse:
    async def event_stream():
        last_payload = ""
        for _ in range(240):
            project = get_project(project_id)
            if not project:
                yield "event: error\ndata: {\"message\":\"Project not found\"}\n\n"
                return
            jobs = project.get("jobs", [])
            payload = json.dumps(
                {
                    "status": project["status"],
                    "job": jobs[0] if jobs else None,
                    "shot_count": len(project.get("shots", [])),
                },
                ensure_ascii=False,
            )
            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
            if jobs and jobs[0]["status"] in {"completed", "failed", "waiting_remote"}:
                return
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/projects/{project_id}/shots/{shot_id}/feedback")
def feedback_endpoint(project_id: str, shot_id: str, payload: FeedbackCreate) -> dict:
    if not get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return apply_feedback(project_id, shot_id, payload.user_text)


@app.post("/api/projects/{project_id}/shots/{shot_id}/versions/{version_id}/rollback")
def rollback_version_endpoint(project_id: str, shot_id: str, version_id: str) -> dict:
    project = rollback_shot_version(project_id, shot_id, version_id)
    if not project:
        raise HTTPException(status_code=404, detail="Shot version not found")
    return project


@app.post("/api/projects/{project_id}/shots/{shot_id}/keyframes/select")
def select_keyframes_endpoint(project_id: str, shot_id: str, payload: KeyframeSelectRequest) -> dict:
    if not get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return select_shot_keyframes(project_id, shot_id, payload.first_frame_path, payload.last_frame_path)
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
    request_payload = payload or VideoGenerateRequest()
    try:
        prepared = prepare_shot_video_generation(
            project_id,
            shot_id,
            video_mode=request_payload.video_mode,
            provider=request_payload.provider,
            model=request_payload.model,
            duration_seconds=request_payload.duration_seconds,
        )
    except CapabilityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job_id = create_job(
        project_id,
        "video_generation",
        f"{prepared['provider_label']} / {prepared['model_label']} 已排队",
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
    job_id = create_job(project_id, "video_safety_retry", "Safe video retry queued")
    background_tasks.add_task(safe_retry_shot_video, project_id, shot_id, job_id)
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/projects/{project_id}/videos")
def generate_all_videos_endpoint(project_id: str, background_tasks: BackgroundTasks) -> dict:
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.get("shots"):
        raise HTTPException(status_code=400, detail="No shots available")
    job_id = create_job(project_id, "batch_video_generation", "Batch video generation queued")
    background_tasks.add_task(generate_project_videos, project_id, job_id)
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/projects/{project_id}/videos/refresh")
def refresh_video_tasks_endpoint(project_id: str, background_tasks: BackgroundTasks) -> dict:
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    job_id = create_job(project_id, "video_task_refresh", "Seedance cloud task refresh queued")
    background_tasks.add_task(refresh_project_video_tasks, project_id, job_id)
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/projects/{project_id}/assemble")
def assemble_video_endpoint(project_id: str, background_tasks: BackgroundTasks) -> dict:
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.get("shots"):
        raise HTTPException(status_code=400, detail="No shots available")
    job_id = create_job(project_id, "sequence_assembly", "Sequence assembly queued")
    background_tasks.add_task(assemble_project_video, project_id, job_id)
    return {"job_id": job_id, "status": "queued"}


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


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
