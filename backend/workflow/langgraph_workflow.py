from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from ..database import connect
from ..providers.llm_provider import ProviderError, generate_story_plan, live_llm_available
from ..schemas import ProjectCreate
from ..services.checkpoint_service import complete_checkpoint, get_paused_checkpoint, save_workflow_checkpoint
from ..services.job_service import increment_job_retry, update_job
from ..services.memory_service import build_shot_evidence, index_project_memory
from ..services.project_service import clear_generated_project_data, compute_shot_count, save_story_bible, update_project_status
from .mock_workflow import _build_story_data, _insert_characters, _insert_scenes, _insert_shots, _normalize_live_story_data, _shot_description


class VisionCraftState(TypedDict, total=False):
    project_id: str
    job_id: str
    payload: ProjectCreate
    shot_count: int
    story_data: dict[str, Any]
    character_assets: dict[str, str]
    scene_assets: dict[str, str]
    evidence_by_index: dict[int, list[dict]]
    routing_mode: str
    review_mode: bool
    saved_checkpoint_id: str


def run_langgraph_workflow(project_id: str, job_id: str) -> None:
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            graph = _build_graph()
            graph.invoke({"project_id": project_id, "job_id": job_id})
            return
        except Exception as exc:  # pragma: no cover - workflow guard for local demo resilience
            if attempt < max_attempts:
                retry_count = increment_job_retry(job_id)
                update_job(job_id, "running", 8, f"LangGraph attempt failed, retrying workflow ({retry_count})", str(exc))
                continue
            update_project_status(project_id, "failed")
            update_job(job_id, "failed", 100, "LangGraph workflow failed", str(exc))


def resume_langgraph_workflow(project_id: str, job_id: str) -> None:
    checkpoint = get_paused_checkpoint(project_id)
    if not checkpoint or checkpoint["job_id"] != job_id:
        update_job(job_id, "failed", 100, "没有可恢复的审核检查点", "没有可恢复的审核检查点")
        return
    try:
        state = checkpoint.get("state") or {}
        state["project_id"] = project_id
        state["job_id"] = job_id
        state["saved_checkpoint_id"] = checkpoint["id"]
        update_project_status(project_id, "running")
        update_job(job_id, "running", 93, "正在从监制检查点继续")
        graph = _build_resume_graph()
        graph.invoke(state)
    except Exception as exc:  # pragma: no cover - workflow guard for local demo resilience
        update_project_status(project_id, "failed")
        update_job(job_id, "failed", 100, "从检查点恢复失败，可稍后重试", str(exc))


def _build_graph():
    # 按生产顺序拆分节点，前端才能说明当前运行到哪个智能体阶段。
    workflow = StateGraph(VisionCraftState)
    workflow.add_node("load_project", _load_project)
    workflow.add_node("plan_story", _plan_story)
    workflow.add_node("generate_assets", _generate_assets)
    workflow.add_node("index_seed_memory", _index_seed_memory)
    workflow.add_node("generate_keyframes", _generate_keyframes)
    workflow.add_node("quality_gate", _quality_gate)
    workflow.add_node("pause_review", _pause_review)
    workflow.add_node("index_memory", _index_memory)
    workflow.add_node("complete", _complete)
    workflow.set_entry_point("load_project")
    workflow.add_edge("load_project", "plan_story")
    workflow.add_edge("plan_story", "generate_assets")
    workflow.add_edge("generate_assets", "index_seed_memory")
    workflow.add_edge("index_seed_memory", "generate_keyframes")
    workflow.add_edge("generate_keyframes", "quality_gate")
    workflow.add_conditional_edges(
        "quality_gate",
        _route_after_quality_gate,
        {"pause_review": "pause_review", "index_memory": "index_memory"},
    )
    workflow.add_edge("pause_review", END)
    workflow.add_edge("index_memory", "complete")
    workflow.add_edge("complete", END)
    return workflow.compile()


def _build_resume_graph():
    # 监制模式在质检后暂停。恢复时不重建资产，只完成索引和状态更新。
    workflow = StateGraph(VisionCraftState)
    workflow.add_node("index_memory", _index_memory)
    workflow.add_node("complete", _complete)
    workflow.set_entry_point("index_memory")
    workflow.add_edge("index_memory", "complete")
    workflow.add_edge("complete", END)
    return workflow.compile()


def _load_project(state: VisionCraftState) -> VisionCraftState:
    project_id = state["project_id"]
    job_id = state["job_id"]
    update_project_status(project_id, "running")
    update_job(job_id, "running", 3, "LangGraph workflow started")
    with connect() as conn:
        project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not project:
        raise RuntimeError("Project not found")
    clear_generated_project_data(project_id)
    payload = ProjectCreate(
        title=project["title"],
        source_text=project["source_text"],
        style=project["style"],
        aspect_ratio=project["aspect_ratio"],
        duration_seconds=project["duration_seconds"],
        shot_count_mode=project["shot_count_mode"],
        requested_shot_count=project["requested_shot_count"],
        review_mode=bool(project["review_mode"]),
    )
    state["payload"] = payload
    state["shot_count"] = compute_shot_count(payload)
    state["routing_mode"] = project["routing_mode"]
    state["review_mode"] = bool(project["review_mode"])
    return state


def _plan_story(state: VisionCraftState) -> VisionCraftState:
    payload = state["payload"]
    job_id = state["job_id"]
    shot_count = state["shot_count"]
    route = state.get("routing_mode", "direct")
    update_job(job_id, "running", 22, f"Narrative Planner building story bible ({route} route)")
    story_data = _build_story_data(payload)
    if live_llm_available():
        update_job(job_id, "running", 34, f"Calling live LLM provider for {route} story planning")
        try:
            story_data = _normalize_live_story_data(generate_story_plan(payload, shot_count), payload)
        except ProviderError as exc:
            update_job(job_id, "running", 40, f"Live LLM failed, fallback to local planner: {exc}")
    save_story_bible(
        state["project_id"],
        story_data["summary"],
        story_data["worldview"],
        story_data["style_tags"],
        story_data["themes"],
    )
    state["story_data"] = story_data
    return state


def _generate_assets(state: VisionCraftState) -> VisionCraftState:
    update_job(state["job_id"], "running", 52, "Visual Director generating baseline assets")
    state["character_assets"] = _insert_characters(state["project_id"], state["story_data"]["characters"])
    state["scene_assets"] = _insert_scenes(state["project_id"], state["story_data"]["scenes"])
    return state


def _index_seed_memory(state: VisionCraftState) -> VisionCraftState:
    count = index_project_memory(state["project_id"])
    update_job(state["job_id"], "running", 62, f"ChromaDB seeded {count} item(s) before keyframe generation")
    return state


def _generate_keyframes(state: VisionCraftState) -> VisionCraftState:
    update_job(state["job_id"], "running", 74, "Key Animator retrieving RAG evidence and generating continuous keyframes")
    evidence_by_index = _build_evidence_by_index(state)
    state["evidence_by_index"] = evidence_by_index
    _insert_shots(
        state["project_id"],
        state["payload"],
        state["story_data"],
        state["character_assets"],
        state["scene_assets"],
        evidence_by_index,
    )
    return state


def _quality_gate(state: VisionCraftState) -> VisionCraftState:
    with connect() as conn:
        missing = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM shots s
            JOIN shot_versions sv ON sv.id = s.current_version_id
            WHERE s.project_id = ? AND (sv.first_frame_path IS NULL OR sv.last_frame_path IS NULL)
            """,
            (state["project_id"],),
        ).fetchone()["count"]
    if missing:
        raise RuntimeError(f"Visual Critic found {missing} shot(s) without complete keyframes")
    update_job(state["job_id"], "running", 90, "Visual Critic passed continuity and keyframe package")
    return state


def _route_after_quality_gate(state: VisionCraftState) -> str:
    return "pause_review" if state.get("review_mode") else "index_memory"


def _pause_review(state: VisionCraftState) -> VisionCraftState:
    # 检查点保存恢复所需状态，用户确认后不必重新跑完整流程。
    checkpoint_id = save_workflow_checkpoint(
        state["project_id"],
        state["job_id"],
        "quality_gate",
        {
            "project_id": state["project_id"],
            "job_id": state["job_id"],
            "node": "quality_gate",
            "stage": "review_pending",
            "input_summary": "质检通过，等待人工确认",
            "pause_reason": "已到达旧版监制质检节点，等待人工确认后继续。",
        },
    )
    state["saved_checkpoint_id"] = checkpoint_id
    update_project_status(state["project_id"], "review_pending")
    update_job(state["job_id"], "paused", 92, "已到达监制审核节点，等待确认后继续", stage="review_pending")
    return state


def _index_memory(state: VisionCraftState) -> VisionCraftState:
    count = index_project_memory(state["project_id"])
    update_job(state["job_id"], "running", 96, f"ChromaDB indexed {count} memory item(s)")
    return state


def _complete(state: VisionCraftState) -> VisionCraftState:
    if state.get("saved_checkpoint_id"):
        complete_checkpoint(state["saved_checkpoint_id"])
    update_project_status(state["project_id"], "ready_for_review")
    update_job(state["job_id"], "completed", 100, "LangGraph storyboard package ready for review")
    return state


def _build_evidence_by_index(state: VisionCraftState) -> dict[int, list[dict]]:
    payload = state["payload"]
    story_data = state["story_data"]
    evidence_by_index: dict[int, list[dict]] = {}
    for index in range(state["shot_count"]):
        live_shot = story_data.get("shots", [])[index] if index < len(story_data.get("shots", [])) else {}
        title = live_shot.get("title") or f"镜头 {index + 1}"
        description = live_shot.get("description") or _shot_description(payload.title, index, state["shot_count"])
        evidence_by_index[index + 1] = build_shot_evidence(state["project_id"], title, description)
    return evidence_by_index
