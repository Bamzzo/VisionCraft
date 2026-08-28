"""P5-A medium-text chunks, events, storylines, and adaptation scope. No live LLM."""
from __future__ import annotations

import uuid

from ..database import connect, from_json, to_json, utc_now
from ..services.job_service import create_job, update_job
from ..services.project_service import get_project, update_project_status
from ..workflow.medium_text_planner import (
    coverage_ok,
    extract_events,
    plan_storylines,
    scale_label,
    segment_source,
    text_scale,
)


class MediumTextError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def attach_medium_text(project: dict) -> dict:
    if not project:
        return project
    source = project.get("source_text") or ""
    scale = text_scale(source)
    project["text_scale"] = scale
    project["text_scale_label"] = scale_label(scale)
    project["source_chunks"] = list_chunks(project["id"])
    project["story_events"] = list_events(project["id"])
    project["storylines"] = list_storylines(project["id"])
    project["adaptation_scope"] = load_current_scope(project["id"])
    return project


def list_chunks(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM source_chunks WHERE project_id = ? ORDER BY chunk_index",
            (project_id,),
        ).fetchall()
    return [_decode_chunk(row) for row in rows]


def list_events(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM story_events WHERE project_id = ? ORDER BY event_index",
            (project_id,),
        ).fetchall()
    return [_decode_event(row) for row in rows]


def list_storylines(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM storylines WHERE project_id = ? ORDER BY storyline_index",
            (project_id,),
        ).fetchall()
    return [_decode_storyline(row) for row in rows]


def load_current_scope(project_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM adaptation_scopes WHERE project_id = ? ORDER BY updated_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    return _decode_scope(row) if row else None


def load_confirmed_scope(project_id: str) -> dict | None:
    scope = load_current_scope(project_id)
    if scope and scope.get("review_status") == "confirmed":
        return scope
    return None


def medium_text_state(project_id: str) -> dict:
    project = get_project(project_id)
    if not project:
        raise MediumTextError("PROJECT_NOT_FOUND", "项目不存在。")
    return project


def run_medium_analysis(project_id: str, job_id: str | None = None, *, regenerate: bool = False) -> dict:
    project = _require_project(project_id)
    scale = text_scale(project["source_text"])
    if scale == "long":
        raise MediumTextError(
            "TEXT_TOO_LONG",
            "当前文本超过 10,000 字。P5-B 的章节树与检索尚未实现，请先截取 1,501～10,000 字，或使用不超过 1,500 字的短文本直接改编。",
        )
    if scale == "short":
        raise MediumTextError(
            "TEXT_TOO_SHORT",
            "当前是短文本（不超过 1,500 字），无需分块与故事线选择。请直接启动改编流程。",
        )
    job_id = job_id or create_job(project_id, "medium_text_analysis", "中等文本分析已排队")
    update_job(job_id, "running", 10, "正在按段落边界拆分原文，不调用付费模型", stage="source_segmentation")
    _invalidate_p4_drafts(project_id)
    if regenerate:
        with connect() as conn:
            conn.execute("UPDATE storylines SET selected = 0 WHERE project_id = ?", (project_id,))
    source = project["source_text"]
    chunks = segment_source(source)
    if not chunks or not coverage_ok(source, chunks):
        raise MediumTextError("SEGMENT_FAILED", "原文分块失败，存在无法回溯的缺口。请检查文本后重试。")
    update_job(job_id, "running", 35, "正在从各块提取事件与原文依据", stage="event_extraction")
    events = extract_events(source, chunks)
    update_job(job_id, "running", 55, "正在组合 2～3 条有差异的候选故事线", stage="storyline_planning")
    storylines = plan_storylines(project["title"], source, chunks, events)
    if not (2 <= len(storylines) <= 3):
        raise MediumTextError("STORYLINE_FAILED", "未能生成足够差异的候选故事线。请修改后重生成分析。")
    _persist_analysis(project_id, source, chunks, events, storylines)
    update_project_status(project_id, "awaiting_storyline_review")
    from .checkpoint_service import save_workflow_checkpoint

    save_workflow_checkpoint(
        project_id,
        job_id,
        "storyline_review",
        {"project_id": project_id, "job_id": job_id, "node": "storyline_review", "stage": "awaiting_storyline_review"},
    )
    _record_review(
        project_id,
        "storyline",
        "analyze" if not regenerate else "regenerate",
        "已生成分块、事件与候选故事线" if not regenerate else "已重生成分块、事件与候选故事线，已失效故事线选择与后续改编草案",
        None,
    )
    update_job(job_id, "paused", 40, "候选故事线已就绪，等待选择故事线与范围", stage="awaiting_storyline_review")
    return medium_text_state(project_id)


def select_storyline(project_id: str, storyline_id: str) -> dict:
    _require_project(project_id)
    line = _require_storyline(project_id, storyline_id)
    now = utc_now()
    with connect() as conn:
        conn.execute("UPDATE storylines SET selected = 0 WHERE project_id = ?", (project_id,))
        conn.execute("UPDATE storylines SET selected = 1 WHERE id = ? AND project_id = ?", (storyline_id, project_id))
    event_ids = line.get("event_ids") or []
    chunk_ids = line.get("chunk_ids") or []
    _upsert_scope(
        project_id,
        storyline_id=storyline_id,
        event_ids=event_ids,
        chunk_ids=chunk_ids,
        review_status="draft",
        user_note="",
        invalidate_p4=True,
    )
    update_project_status(project_id, "awaiting_storyline_review")
    _record_review(project_id, "storyline", "select", f"选定故事线：{line['title']}", storyline_id)
    _touch(project_id, now)
    return medium_text_state(project_id)


def save_adaptation_scope(
    project_id: str,
    *,
    storyline_id: str | None = None,
    event_ids: list[str] | None = None,
    chunk_ids: list[str] | None = None,
    user_note: str | None = None,
) -> dict:
    _require_project(project_id)
    current = load_current_scope(project_id)
    selected = next((item for item in list_storylines(project_id) if item.get("selected")), None)
    storyline_id = storyline_id or (current or {}).get("storyline_id") or (selected or {}).get("id")
    if not storyline_id:
        raise MediumTextError("STORYLINE_NOT_SELECTED", "尚未选择故事线。请先点击一条候选故事线，再保存范围。")
    line = _require_storyline(project_id, storyline_id)
    if event_ids is None:
        event_ids = (current or {}).get("event_ids") or line.get("event_ids") or []
    event_ids = _require_event_ids(project_id, event_ids)
    if not event_ids:
        raise MediumTextError("SCOPE_EMPTY", "至少保留一个事件。取消勾选后范围内不能为空。")
    if chunk_ids is None:
        chunk_ids = _chunk_ids_for_events(project_id, event_ids)
    else:
        chunk_ids = _require_chunk_ids(project_id, chunk_ids)
    _upsert_scope(
        project_id,
        storyline_id=storyline_id,
        event_ids=event_ids,
        chunk_ids=chunk_ids,
        review_status="draft",
        user_note=user_note if user_note is not None else ((current or {}).get("user_note") or ""),
        invalidate_p4=True,
    )
    update_project_status(project_id, "awaiting_storyline_review")
    _record_review(project_id, "storyline", "save_scope", "已保存用户选择的事件与片段范围", storyline_id)
    return medium_text_state(project_id)


def confirm_adaptation_scope(
    project_id: str,
    *,
    storyline_id: str | None = None,
    event_ids: list[str] | None = None,
    chunk_ids: list[str] | None = None,
    user_note: str | None = None,
    job_id: str | None = None,
) -> dict:
    state = save_adaptation_scope(
        project_id,
        storyline_id=storyline_id,
        event_ids=event_ids,
        chunk_ids=chunk_ids,
        user_note=user_note,
    )
    scope = state.get("adaptation_scope") or {}
    if not (scope.get("scoped_text") or "").strip():
        raise MediumTextError("SCOPE_EMPTY", "选中范围没有可改编原文。请重新勾选事件后确认。")
    now = utc_now()
    with connect() as conn:
        conn.execute(
            "UPDATE adaptation_scopes SET review_status = ?, updated_at = ? WHERE project_id = ?",
            ("confirmed", now, project_id),
        )
    job_id = job_id or create_job(project_id, "adaptation_workflow", "已根据选定范围生成改编方案")
    from .adaptation_service import generate_options_from_context

    generate_options_from_context(project_id, job_id=job_id)
    _record_review(project_id, "storyline", "confirm", "已确认故事范围并进入改编方案审核", scope.get("storyline_id"))
    return medium_text_state(project_id)


def apply_recommended_scope(project_id: str) -> dict:
    selected = next((item for item in list_storylines(project_id) if item.get("selected")), None)
    if not selected:
        raise MediumTextError("STORYLINE_NOT_SELECTED", "尚未选择故事线。请先选择一条候选故事线，再按推荐范围继续。")
    return save_adaptation_scope(
        project_id,
        storyline_id=selected["id"],
        event_ids=list(selected.get("event_ids") or []),
        chunk_ids=list(selected.get("chunk_ids") or []),
        user_note="",
    )


def regenerate_medium(project_id: str, stage: str, job_id: str | None = None) -> dict:
    if stage not in {"analysis", "storyline"}:
        raise MediumTextError("INVALID_STAGE", "只能对文本分析或故事线范围执行修改后重生成。")
    if stage == "analysis":
        return run_medium_analysis(project_id, job_id, regenerate=True)
    scope = load_current_scope(project_id)
    if not scope or not scope.get("storyline_id"):
        raise MediumTextError("STORYLINE_NOT_SELECTED", "没有已选故事线，无法只重生成范围之后的改编草案。")
    job_id = job_id or create_job(project_id, "medium_scope_regen", "故事线范围变更后重生成已排队")
    update_job(job_id, "running", 20, "失效范围之后的改编方案、Bible 与分镜草案，保留镜头版本与资产", stage="regenerate_storyline")
    _invalidate_p4_drafts(project_id)
    with connect() as conn:
        conn.execute(
            "UPDATE adaptation_scopes SET review_status = ?, updated_at = ? WHERE project_id = ?",
            ("draft", utc_now(), project_id),
        )
    update_project_status(project_id, "awaiting_storyline_review")
    _record_review(project_id, "storyline", "regenerate", "改变故事线范围，已失效后续 P4 草案", scope.get("id"))
    update_job(job_id, "paused", 35, "请重新确认事件范围后进入改编", stage="awaiting_storyline_review")
    return medium_text_state(project_id)


def ensure_implicit_short_scope(project: dict) -> dict:
    source = project.get("source_text") or ""
    now = utc_now()
    existing = load_current_scope(project["id"])
    if existing and existing.get("review_status") == "confirmed" and existing.get("scoped_text"):
        return existing
    _upsert_scope(
        project["id"],
        storyline_id=None,
        event_ids=[],
        chunk_ids=[],
        review_status="confirmed",
        user_note="短文本隐式全范围",
        invalidate_p4=False,
        scoped_override=source,
        start_offset=0,
        end_offset=len(source),
        source="implicit_short_scope",
    )
    _touch(project["id"], now)
    return load_current_scope(project["id"]) or {}


def _persist_analysis(project_id: str, source: str, chunks: list[dict], events: list[dict], storylines: list[dict]) -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute("DELETE FROM storylines WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM story_events WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM source_chunks WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM adaptation_scopes WHERE project_id = ?", (project_id,))
        chunk_ids_by_index = {}
        for chunk in chunks:
            chunk_id = f"chk_{uuid.uuid4().hex[:10]}"
            chunk_ids_by_index[chunk["chunk_index"]] = chunk_id
            conn.execute(
                """
                INSERT INTO source_chunks
                (id, project_id, chunk_index, text, start_offset, end_offset, char_count, summary,
                 characters_json, places_json, conflict_terms_json, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    project_id,
                    chunk["chunk_index"],
                    chunk["text"],
                    chunk["start_offset"],
                    chunk["end_offset"],
                    chunk["char_count"],
                    chunk.get("summary") or "",
                    to_json(chunk.get("characters") or []),
                    to_json(chunk.get("places") or []),
                    to_json(chunk.get("conflict_terms") or []),
                    chunk.get("source") or "mock_segmenter",
                    now,
                ),
            )
        event_ids_by_index = {}
        for event in events:
            event_id = f"evt_{uuid.uuid4().hex[:10]}"
            event_ids_by_index[event["event_index"]] = event_id
            chunk_ids = [chunk_ids_by_index[idx] for idx in event.get("chunk_indexes") or [] if idx in chunk_ids_by_index]
            conn.execute(
                """
                INSERT INTO story_events
                (id, project_id, event_index, title, summary, characters_json, places_json, goal, conflict, outcome,
                 chunk_ids_json, source_excerpt, source_start, source_end, importance, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    project_id,
                    event["event_index"],
                    event["title"],
                    event.get("summary") or "",
                    to_json(event.get("characters") or []),
                    to_json(event.get("places") or []),
                    event.get("goal") or "",
                    event.get("conflict") or "",
                    event.get("outcome") or "",
                    to_json(chunk_ids),
                    event.get("source_excerpt") or "",
                    event.get("source_start"),
                    event.get("source_end"),
                    float(event.get("importance") or 0),
                    event.get("source") or "mock_event_extractor",
                    now,
                ),
            )
        for line in storylines:
            event_ids = [event_ids_by_index[idx] for idx in line.get("event_indexes") or [] if idx in event_ids_by_index]
            chunk_ids = [chunk_ids_by_index[idx] for idx in line.get("chunk_indexes") or [] if idx in chunk_ids_by_index]
            conn.execute(
                """
                INSERT INTO storylines
                (id, project_id, storyline_index, title, rationale, protagonist, protagonist_goal, conflict,
                 turning_point, ending_orientation, event_ids_json, chunk_ids_json, source_excerpt,
                 suggested_duration_seconds, suggested_shot_count, selected, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    f"sln_{uuid.uuid4().hex[:10]}",
                    project_id,
                    line["storyline_index"],
                    line["title"],
                    line.get("rationale") or "",
                    line.get("protagonist") or "",
                    line.get("protagonist_goal") or "",
                    line.get("conflict") or "",
                    line.get("turning_point") or "",
                    line.get("ending_orientation") or "",
                    to_json(event_ids),
                    to_json(chunk_ids),
                    line.get("source_excerpt") or "",
                    int(line.get("suggested_duration_seconds") or 45),
                    int(line.get("suggested_shot_count") or 5),
                    line.get("source") or "mock_storyline_planner",
                    now,
                ),
            )


def _upsert_scope(
    project_id: str,
    *,
    storyline_id: str | None,
    event_ids: list[str],
    chunk_ids: list[str],
    review_status: str,
    user_note: str,
    invalidate_p4: bool,
    scoped_override: str | None = None,
    start_offset: int | None = None,
    end_offset: int | None = None,
    source: str = "user_scope",
) -> dict:
    project = _require_project(project_id)
    if scoped_override is not None:
        scoped_text = scoped_override
        start_offset = 0 if start_offset is None else start_offset
        end_offset = len(scoped_text) if end_offset is None else end_offset
    else:
        scoped_text, start_offset, end_offset, chunk_ids = _build_scoped_text(project["source_text"], project_id, event_ids, chunk_ids)
    now = utc_now()
    existing = load_current_scope(project_id)
    scope_id = existing["id"] if existing else f"scp_{uuid.uuid4().hex[:10]}"
    with connect() as conn:
        if existing:
            conn.execute(
                """
                UPDATE adaptation_scopes SET
                  storyline_id=?, event_ids_json=?, chunk_ids_json=?, scoped_text=?, start_offset=?, end_offset=?,
                  review_status=?, user_note=?, source=?, updated_at=?
                WHERE project_id=?
                """,
                (
                    storyline_id,
                    to_json(event_ids),
                    to_json(chunk_ids),
                    scoped_text,
                    start_offset,
                    end_offset,
                    review_status,
                    user_note or "",
                    source,
                    now,
                    project_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO adaptation_scopes
                (id, project_id, storyline_id, event_ids_json, chunk_ids_json, scoped_text, start_offset, end_offset,
                 review_status, user_note, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope_id,
                    project_id,
                    storyline_id,
                    to_json(event_ids),
                    to_json(chunk_ids),
                    scoped_text,
                    start_offset,
                    end_offset,
                    review_status,
                    user_note or "",
                    source,
                    now,
                    now,
                ),
            )
    if invalidate_p4:
        _invalidate_p4_drafts(project_id)
    return load_current_scope(project_id) or {}


def _build_scoped_text(source: str, project_id: str, event_ids: list[str], chunk_ids: list[str]) -> tuple[str, int, int, list[str]]:
    chunks = {item["id"]: item for item in list_chunks(project_id)}
    if not chunk_ids:
        chunk_ids = _chunk_ids_for_events(project_id, event_ids)
    ranges = []
    kept = []
    for chunk_id in chunk_ids:
        chunk = chunks.get(chunk_id)
        if not chunk:
            continue
        kept.append(chunk_id)
        ranges.append((int(chunk["start_offset"]), int(chunk["end_offset"])))
    if not ranges:
        return "", 0, 0, []
    merged = _merge_ranges(ranges)
    parts = [source[start:end] for start, end in merged]
    return "".join(parts), merged[0][0], merged[-1][1], kept


def _chunk_ids_for_events(project_id: str, event_ids: list[str]) -> list[str]:
    events = {item["id"]: item for item in list_events(project_id)}
    ids = []
    for event_id in event_ids:
        event = events.get(event_id)
        if not event:
            continue
        for chunk_id in event.get("chunk_ids") or []:
            if chunk_id not in ids:
                ids.append(chunk_id)
    return ids


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[list[int]]:
    ordered = sorted(ranges)
    merged: list[list[int]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return merged


def _invalidate_p4_drafts(project_id: str) -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute("DELETE FROM adaptation_options WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM storyboard_drafts WHERE project_id = ?", (project_id,))
        conn.execute(
            "UPDATE story_bibles SET review_status = ?, updated_at = ? WHERE project_id = ?",
            ("stale", now, project_id),
        )
        conn.execute(
            "UPDATE projects SET selected_option_id = NULL, updated_at = ? WHERE id = ?",
            (now, project_id),
        )


def _require_project(project_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise MediumTextError("PROJECT_NOT_FOUND", "项目不存在。")
    return dict(row)


def _require_storyline(project_id: str, storyline_id: str) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM storylines WHERE id = ? AND project_id = ?",
            (storyline_id, project_id),
        ).fetchone()
    if not row:
        raise MediumTextError("STORYLINE_MISMATCH", "故事线不属于当前项目。请只选择本项目生成的候选故事线。")
    return _decode_storyline(row)


def _require_event_ids(project_id: str, event_ids: list[str]) -> list[str]:
    owned = {item["id"] for item in list_events(project_id)}
    cleaned = []
    for event_id in event_ids:
        if event_id not in owned:
            raise MediumTextError("EVENT_MISMATCH", "所选事件不属于当前项目。请只勾选本项目分析得到的事件。")
        if event_id not in cleaned:
            cleaned.append(event_id)
    return cleaned


def _require_chunk_ids(project_id: str, chunk_ids: list[str]) -> list[str]:
    owned = {item["id"] for item in list_chunks(project_id)}
    cleaned = []
    for chunk_id in chunk_ids:
        if chunk_id not in owned:
            raise MediumTextError("CHUNK_MISMATCH", "所选文本块不属于当前项目。请只勾选本项目分块结果。")
        if chunk_id not in cleaned:
            cleaned.append(chunk_id)
    return cleaned


def _record_review(project_id: str, stage: str, action: str, summary: str, target_id: str | None) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO review_records (id, project_id, stage, action, summary, target_id, target_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (f"rev_{uuid.uuid4().hex[:10]}", project_id, stage, action, summary, target_id, utc_now()),
        )


def _touch(project_id: str, now: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))


def _decode_chunk(row) -> dict:
    item = dict(row)
    item["characters"] = from_json(item.get("characters_json"), [])
    item["places"] = from_json(item.get("places_json"), [])
    item["conflict_terms"] = from_json(item.get("conflict_terms_json"), [])
    return item


def _decode_event(row) -> dict:
    item = dict(row)
    item["characters"] = from_json(item.get("characters_json"), [])
    item["places"] = from_json(item.get("places_json"), [])
    item["chunk_ids"] = from_json(item.get("chunk_ids_json"), [])
    return item


def _decode_storyline(row) -> dict:
    item = dict(row)
    item["event_ids"] = from_json(item.get("event_ids_json"), [])
    item["chunk_ids"] = from_json(item.get("chunk_ids_json"), [])
    item["selected"] = bool(item.get("selected"))
    return item


def _decode_scope(row) -> dict:
    item = dict(row)
    item["event_ids"] = from_json(item.get("event_ids_json"), [])
    item["chunk_ids"] = from_json(item.get("chunk_ids_json"), [])
    return item
