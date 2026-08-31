"""P4-A LangGraph stages: 文本理解 → 改编方案 → Story Bible → 分镜."""
from typing import TypedDict

from langgraph.graph import END, StateGraph

from ..services.adaptation_service import start_adaptation_workflow
from ..services.job_service import redact_text, update_job
from ..services.project_service import update_project_status


class AdaptationState(TypedDict, total=False):
    project_id: str
    job_id: str


def run_adaptation_workflow(project_id: str, job_id: str) -> None:
    try:
        graph = _build_graph()
        graph.invoke({"project_id": project_id, "job_id": job_id})
    except Exception as exc:
        update_project_status(project_id, "failed")
        update_job(job_id, "failed", 100, "改编工作流失败，可从检查点继续。", redact_text(str(exc)), stage="failed")


def _build_graph():
    workflow = StateGraph(AdaptationState)
    workflow.add_node("understand_and_plan", _understand_and_plan)
    workflow.set_entry_point("understand_and_plan")
    workflow.add_edge("understand_and_plan", END)
    return workflow.compile()


def _understand_and_plan(state: AdaptationState) -> AdaptationState:
    start_adaptation_workflow(state["project_id"], state["job_id"])
    return state
