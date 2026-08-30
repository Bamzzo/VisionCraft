"""Keyframe / reference-image visual review using the vision adapter."""
from __future__ import annotations

import uuid

from ..database import connect, from_json, to_json, utc_now
from ..providers.llm_adapter import JsonParseError, LiveCallNotAuthorized, ProviderError
from ..providers.llm_catalog import ModelConfigError
from ..providers.vision_adapter import (
    VisionAdapterError,
    build_vision_request,
    complete_vision_json,
    vision_prompt_for_keyframe,
)
from ..services.job_service import create_job, redact_value, update_job
from ..services.model_config_service import get_generation_mode, lineage_from_config, resolve_stage_model
from ..services.project_service import get_project


def review_project_image(
    project_id: str,
    *,
    asset_id: str | None = None,
    asset_path: str | None = None,
    role: str = "keyframe",
    job_id: str | None = None,
) -> dict:
    project = get_project(project_id)
    if not project:
        raise ModelConfigError("PROJECT_NOT_FOUND", "项目不存在。")
    path, asset = _resolve_asset(project_id, asset_id=asset_id, asset_path=asset_path)
    config = resolve_stage_model(project_id, "vision_review")
    config["generation_mode"] = get_generation_mode(project)
    job_id = job_id or create_job(project_id, "vision_review", "视觉检查已排队", stage="vision_review")
    update_job(job_id, "running", 20, "正在构造视觉检查请求", stage="vision_review")

    try:
        prepared = build_vision_request(
            project_id=project_id,
            public_path=path,
            prompt=vision_prompt_for_keyframe(role),
            provider=config["provider"],
            model=config["model"],
            role=role,
        )
    except VisionAdapterError as exc:
        update_job(job_id, "failed", 100, str(exc), str(exc), stage="vision_review")
        raise

    meta = redact_value(prepared.public_metadata())
    mode = config["generation_mode"]
    mock_result = _mock_vision_result(asset, role)
    result = mock_result
    source = "mock_vision"
    used_fallback = False
    error = None

    if mode != "mock":
        try:
            parsed = complete_vision_json(prepared)
            result = _coerce_vision_result(parsed, mock_result)
            source = "live_vision"
        except (LiveCallNotAuthorized, JsonParseError, ProviderError) as exc:
            error = str(exc)
            if mode == "live_strict":
                message = "视觉检查处于严格真实模式，任务已失败。"
                update_job(
                    job_id,
                    "failed",
                    100,
                    message,
                    error,
                    stage="vision_review",
                    detail=meta,
                )
                raise ModelConfigError("LIVE_VISION_FAILED", f"{message}{error[:180]}") from exc
            used_fallback = True
            source = "local_fallback"
            result = mock_result

    lineage = lineage_from_config(config, source=source, used_local_fallback=used_fallback)
    review_id = _store_review(
        project_id=project_id,
        asset=asset,
        role=role,
        prepared_meta=meta,
        result=result,
        lineage=lineage,
        fallback_reason=error if used_fallback else None,
    )
    message = "视觉检查已完成（本地模拟）" if source == "mock_vision" else (
        "视觉检查已使用本地回退" if used_fallback else "视觉检查已完成"
    )
    update_job(
        job_id,
        "completed",
        100,
        message,
        stage="vision_review",
        detail={**meta, "used_local_fallback": used_fallback, "source": source, "review_id": review_id},
    )
    payload = get_project(project_id)
    payload["latest_vision_review"] = _load_review(review_id)
    return payload


def list_vision_reviews(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM vision_reviews WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
    return [_public_review(dict(row)) for row in rows]


def _resolve_asset(project_id: str, *, asset_id: str | None, asset_path: str | None) -> tuple[str, dict]:
    with connect() as conn:
        row = None
        if asset_id:
            row = conn.execute(
                "SELECT * FROM assets WHERE id = ? AND project_id = ?",
                (asset_id, project_id),
            ).fetchone()
        elif asset_path:
            row = conn.execute(
                "SELECT * FROM assets WHERE project_id = ? AND file_path = ?",
                (project_id, asset_path),
            ).fetchone()
    if not row:
        raise VisionAdapterError("ASSET_NOT_FOUND", "所选图片不属于当前项目。")
    item = dict(row)
    return item["file_path"], item


def _store_review(**kwargs) -> str:
    review_id = f"vr_{uuid.uuid4().hex[:10]}"
    meta = kwargs["prepared_meta"]
    lineage = kwargs["lineage"]
    result = kwargs["result"]
    blob = to_json(
        {
            "result": result,
            "lineage": lineage,
            "fallback_reason": kwargs.get("fallback_reason"),
        }
    )
    if "data:image" in blob or "base64," in blob.lower():
        raise VisionAdapterError("UNSAFE_PERSIST", "视觉检查结果不能持久化 Data URL 或 Base64。")
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO vision_reviews
            (id, project_id, asset_id, asset_role, provider, model, transport_mode, mime_type,
             width, height, byte_size, request_id, result_json, used_local_fallback, generation_mode,
             source, config_source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                kwargs["project_id"],
                kwargs["asset"]["id"],
                kwargs["role"],
                lineage.get("provider"),
                lineage.get("model"),
                meta.get("transport_mode"),
                meta.get("mime_type"),
                meta.get("width"),
                meta.get("height"),
                meta.get("byte_size"),
                meta.get("request_id"),
                blob,
                lineage.get("used_local_fallback") or 0,
                lineage.get("generation_mode"),
                lineage.get("source"),
                lineage.get("config_source"),
                now,
            ),
        )
    return review_id


def _load_review(review_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM vision_reviews WHERE id = ?", (review_id,)).fetchone()
    return _public_review(dict(row)) if row else {}


def _public_review(row: dict) -> dict:
    payload = from_json(row.get("result_json"), {}) or {}
    item = {
        **row,
        "result": payload.get("result") or {},
        "lineage": payload.get("lineage") or {},
        "fallback_reason": payload.get("fallback_reason"),
    }
    item.pop("result_json", None)
    return item


def _mock_vision_result(asset: dict, role: str) -> dict:
    name = asset.get("name") or asset.get("type") or "关键帧"
    return {
        "description": f"本地视觉检查占位：{name}（角色 {role}）。未调用远程视觉模型。",
        "characters": [],
        "wardrobe": [],
        "props": [],
        "quality_notes": ["当前为本地模拟结果，等待人工确认后再发起真实视觉调用。"],
        "consistency_risks": [],
        "source": "mock_vision",
    }


def _coerce_vision_result(parsed: dict, fallback: dict) -> dict:
    data = parsed if isinstance(parsed, dict) else {}
    return {
        "description": str(data.get("description") or fallback["description"]),
        "characters": data.get("characters") if isinstance(data.get("characters"), list) else [],
        "wardrobe": data.get("wardrobe") if isinstance(data.get("wardrobe"), list) else [],
        "props": data.get("props") if isinstance(data.get("props"), list) else [],
        "quality_notes": data.get("quality_notes") if isinstance(data.get("quality_notes"), list) else [],
        "consistency_risks": data.get("consistency_risks") if isinstance(data.get("consistency_risks"), list) else [],
        "source": "live_vision",
    }
