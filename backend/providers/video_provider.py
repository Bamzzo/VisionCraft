import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import PROJECTS_DIR
from ..database import connect, utc_now
from ..services.asset_service import public_asset_path


@dataclass
class VideoAssetRequest:
    project_id: str
    shot_id: str
    version_id: str
    title: str
    description: str
    prompt: str
    first_frame_path: str | None
    duration_seconds: int
    aspect_ratio: str = "16:9"
    last_frame_path: str | None = None
    negative_prompt: str = ""
    audio_prompt: str = ""
    video_mode: str = "t2v"
    job_id: str | None = None


@dataclass
class VideoGenerationResult:
    status: str
    video_path: str | None = None
    provider: str = ""
    model: str = ""
    remote_task_id: str | None = None
    cloud_status: str = ""
    task_id: str | None = None
    message: str = ""


def generate_video_asset(request: VideoAssetRequest) -> VideoGenerationResult:
    failure_reasons: list[str] = []
    provider = os.getenv("VISIONCRAFT_VIDEO_PROVIDER", "siliconflow").lower()
    providers = [provider] if provider in {"siliconflow", "ark", "volc"} else ["siliconflow", "ark"]
    if provider == "siliconflow":
        providers.append("ark")

    attempted = False
    for candidate in dict.fromkeys(providers):
        try:
            if candidate == "siliconflow" and os.getenv("SILICONFLOW_API_KEY"):
                attempted = True
                return _generate_siliconflow_video(request)
            if candidate in {"ark", "volc"} and _ark_api_key():
                attempted = True
                return _generate_ark_video(request)
        except Exception as exc:
            failure_reasons.append(f"{candidate}: {_compact_error(exc)}")
    if not attempted:
        raise RuntimeError("No live video provider configured. Refusing to create placeholder still-video output.")
    raise RuntimeError("All live video providers failed. " + " | ".join(failure_reasons))


def refresh_remote_video_task(video_task_id: str) -> VideoGenerationResult:
    with connect() as conn:
        task = conn.execute("SELECT * FROM video_tasks WHERE id = ?", (video_task_id,)).fetchone()
        if not task:
            raise RuntimeError("Video task not found")
        project = conn.execute("SELECT * FROM projects WHERE id = ?", (task["project_id"],)).fetchone()
        shot = conn.execute("SELECT * FROM shots WHERE id = ?", (task["shot_id"],)).fetchone()
        version = conn.execute("SELECT * FROM shot_versions WHERE id = ?", (task["version_id"],)).fetchone()
    if not project or not shot or not version:
        raise RuntimeError("Video task is detached from project, shot, or version")
    request = VideoAssetRequest(
        project_id=task["project_id"],
        shot_id=task["shot_id"],
        version_id=task["version_id"],
        title=shot["title"],
        description=shot["description"],
        prompt=task["prompt"] or shot["visual_prompt"],
        negative_prompt=shot["negative_prompt"],
        audio_prompt=shot["audio_prompt"],
        first_frame_path=version["first_frame_path"],
        last_frame_path=version["last_frame_path"],
        video_mode=version["video_mode"] or "t2v",
        duration_seconds=project["duration_seconds"],
        aspect_ratio=project["aspect_ratio"],
        job_id=task["job_id"],
    )
    if task["provider"] in {"ark", "volc"}:
        return _refresh_ark_video(request, dict(task))
    raise RuntimeError(f"Refresh is not implemented for provider: {task['provider']}")


def _generate_siliconflow_video(request: VideoAssetRequest) -> VideoGenerationResult:
    api_key = os.environ["SILICONFLOW_API_KEY"]
    base_url = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
    model = os.getenv("SILICONFLOW_VIDEO_MODEL", "Wan-AI/Wan2.2-T2V-A14B")
    image_size = os.getenv("SILICONFLOW_VIDEO_SIZE", "1280x720")
    poll_seconds = int(os.getenv("SILICONFLOW_VIDEO_POLL_SECONDS", "180"))
    prompt = _build_video_prompt(request)

    submit_payload = {"model": model, "prompt": prompt, "image_size": image_size}
    submit = _post_json(base_url + "/video/submit", api_key, submit_payload)
    request_id = submit["requestId"]

    deadline = time.time() + poll_seconds
    last_status = None
    while time.time() < deadline:
        status_payload = _post_json(base_url + "/video/status", api_key, {"requestId": request_id})
        last_status = status_payload.get("status")
        if last_status == "Succeed":
            video_url = status_payload["results"]["videos"][0]["url"]
            video_path = _download_and_record_video(request, video_url, prompt)
            return VideoGenerationResult(
                status="completed",
                video_path=video_path,
                provider="siliconflow",
                model=model,
                remote_task_id=request_id,
                cloud_status=str(last_status),
            )
        if last_status == "Failed":
            raise RuntimeError(status_payload.get("reason") or "Video generation failed")
        time.sleep(8)
    raise RuntimeError(f"Video generation timeout, last status={last_status}")


def _post_json(url: str, api_key: str, payload: dict) -> dict:
    http_request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(http_request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Video API HTTP {exc.code}: {detail}") from exc


def _get_json(url: str, api_key: str) -> dict:
    http_request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(http_request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Video API HTTP {exc.code}: {detail}") from exc


def _generate_ark_video(request: VideoAssetRequest) -> VideoGenerationResult:
    api_key = _ark_api_key()
    if not api_key:
        raise RuntimeError("No Ark video API key configured")
    base_url = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    model = os.getenv("VOLC_VIDEO_MODEL") or os.getenv("SEEDANCE_V2_ENDPOINT", "doubao-seedance-2-0-260128")
    poll_seconds = int(os.getenv("VOLC_VIDEO_POLL_SECONDS", os.getenv("SILICONFLOW_VIDEO_POLL_SECONDS", "180")))
    prompt = _build_video_prompt(request)
    content = _ark_content_items(request, prompt)
    submit_payload = {
        "model": model,
        "content": content,
        "generate_audio": True,
        "ratio": request.aspect_ratio or "16:9",
        "duration": max(1, request.duration_seconds),
        "resolution": os.getenv("VOLC_VIDEO_RESOLUTION", "720p"),
        "watermark": False,
    }
    submit = _post_json(base_url + "/contents/generations/tasks", api_key, submit_payload)
    task_id = submit.get("id") or submit.get("task_id") or submit.get("request_id")
    if not task_id:
        raise RuntimeError(f"Ark video submit returned no task id: {submit}")

    local_task_id = _upsert_video_task(
        request,
        provider="ark",
        model=model,
        remote_task_id=task_id,
        status="running",
        cloud_status=str(submit.get("status") or "submitted").lower(),
        prompt=prompt,
        submit_payload=submit_payload,
        status_payload=submit,
    )

    deadline = time.time() + poll_seconds
    last_status = str(submit.get("status") or "submitted").lower()
    last_payload = submit
    while time.time() < deadline:
        status_payload = _get_json(base_url + f"/contents/generations/tasks/{task_id}", api_key)
        last_payload = status_payload
        last_status = str(status_payload.get("status") or status_payload.get("task_status") or "").lower()
        result = _handle_ark_status(request, local_task_id, task_id, model, prompt, status_payload)
        if result.status != "running":
            return result
        time.sleep(30)

    _update_video_task(
        local_task_id,
        status="pending_remote",
        cloud_status=last_status,
        status_payload=last_payload,
        error_code="REMOTE_STILL_RUNNING",
        error_message="Seedance cloud task is still running after local polling timeout.",
    )
    return VideoGenerationResult(
        status="pending_remote",
        provider="ark",
        model=model,
        remote_task_id=task_id,
        cloud_status=last_status,
        task_id=local_task_id,
        message="Seedance cloud task is still running after local polling timeout.",
    )


def _refresh_ark_video(request: VideoAssetRequest, task: dict) -> VideoGenerationResult:
    api_key = _ark_api_key()
    if not api_key:
        raise RuntimeError("No Ark video API key configured")
    base_url = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    remote_task_id = task["remote_task_id"]
    status_payload = _get_json(base_url + f"/contents/generations/tasks/{remote_task_id}", api_key)
    result = _handle_ark_status(
        request,
        task["id"],
        remote_task_id,
        task["model"],
        task["prompt"] or _build_video_prompt(request),
        status_payload,
    )
    if result.status == "running":
        cloud_status = str(status_payload.get("status") or status_payload.get("task_status") or "running").lower()
        _update_video_task(task["id"], status="pending_remote", cloud_status=cloud_status, status_payload=status_payload)
        return VideoGenerationResult(
            status="pending_remote",
            provider=task["provider"],
            model=task["model"],
            remote_task_id=remote_task_id,
            cloud_status=cloud_status,
            task_id=task["id"],
            message="Seedance cloud task is still running.",
        )
    return result


def _handle_ark_status(
    request: VideoAssetRequest,
    local_task_id: str,
    remote_task_id: str,
    model: str,
    prompt: str,
    status_payload: dict,
) -> VideoGenerationResult:
    cloud_status = str(status_payload.get("status") or status_payload.get("task_status") or "").lower()
    if cloud_status in {"succeeded", "succeed", "success", "completed", "done"}:
        video_url = _find_video_url(status_payload)
        if not video_url:
            _update_video_task(
                local_task_id,
                status="failed",
                cloud_status=cloud_status,
                status_payload=status_payload,
                error_code="NO_VIDEO_URL",
                error_message="Ark video succeeded but returned no video URL.",
            )
            raise RuntimeError(f"Ark video succeeded but returned no video URL: {status_payload}")
        video_path = _download_and_record_video(request, video_url, prompt, f"provider:ark:{model}")
        _update_video_task(
            local_task_id,
            status="completed",
            cloud_status=cloud_status,
            status_payload=status_payload,
            video_url=video_url,
            result_path=video_path,
            error_code=None,
            error_message=None,
        )
        return VideoGenerationResult(
            status="completed",
            video_path=video_path,
            provider="ark",
            model=model,
            remote_task_id=remote_task_id,
            cloud_status=cloud_status,
            task_id=local_task_id,
        )
    if cloud_status in {"failed", "fail", "error", "cancelled", "canceled", "expired"}:
        code, message = _extract_error(status_payload)
        _update_video_task(
            local_task_id,
            status="failed",
            cloud_status=cloud_status,
            status_payload=status_payload,
            error_code=code,
            error_message=message,
        )
        raise RuntimeError(_friendly_video_error(code, message))
    _update_video_task(local_task_id, status="running", cloud_status=cloud_status or "running", status_payload=status_payload)
    return VideoGenerationResult(
        status="running",
        provider="ark",
        model=model,
        remote_task_id=remote_task_id,
        cloud_status=cloud_status or "running",
        task_id=local_task_id,
    )


def _download_and_record_video(
    request: VideoAssetRequest,
    video_url: str,
    prompt: str,
    embedding_ref: str = "provider:siliconflow",
) -> str:
    video_request = urllib.request.Request(video_url, method="GET")
    with urllib.request.urlopen(video_request, timeout=180) as response:
        content = response.read()
    asset_id = f"asset_{uuid.uuid4().hex[:10]}"
    filename = f"{asset_id}.mp4"
    file_path = _project_file_path(request.project_id, filename)
    file_path.write_bytes(content)
    _record_video_asset(request, asset_id, filename, request.description, prompt, embedding_ref)
    return public_asset_path(request.project_id, filename)


def _record_video_asset(
    request: VideoAssetRequest,
    asset_id: str,
    filename: str,
    description: str,
    prompt: str,
    embedding_ref: str,
) -> None:
    video_path = public_asset_path(request.project_id, filename)
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM assets WHERE project_id = ? AND file_path = ?",
            (request.project_id, video_path),
        ).fetchone()
        if not existing:
            conn.execute(
                """
                INSERT INTO assets
                (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    request.project_id,
                    "video",
                    f"{request.title} Video",
                    description,
                    prompt,
                    video_path,
                    embedding_ref,
                    utc_now(),
                ),
            )
        conn.execute(
            "UPDATE shot_versions SET video_path = ? WHERE id = ?",
            (video_path, request.version_id),
        )
        conn.execute(
            "UPDATE shots SET status = ?, updated_at = ? WHERE id = ?",
            ("video_ready", utc_now(), request.shot_id),
        )


def _upsert_video_task(
    request: VideoAssetRequest,
    provider: str,
    model: str,
    remote_task_id: str,
    status: str,
    cloud_status: str,
    prompt: str,
    submit_payload: dict,
    status_payload: dict,
) -> str:
    task_id = f"vt_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM video_tasks WHERE provider = ? AND remote_task_id = ?",
            (provider, remote_task_id),
        ).fetchone()
        if existing:
            task_id = existing["id"]
            conn.execute(
                """
                UPDATE video_tasks
                SET project_id = ?, shot_id = ?, version_id = ?, job_id = ?, model = ?,
                    status = ?, cloud_status = ?, prompt = ?, submit_payload = ?,
                    status_payload = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    request.project_id,
                    request.shot_id,
                    request.version_id,
                    request.job_id,
                    model,
                    status,
                    cloud_status,
                    prompt,
                    _safe_payload_json(submit_payload),
                    _safe_payload_json(status_payload),
                    now,
                    task_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO video_tasks
                (id, project_id, shot_id, version_id, job_id, provider, model, remote_task_id,
                 status, cloud_status, prompt, submit_payload, status_payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    request.project_id,
                    request.shot_id,
                    request.version_id,
                    request.job_id,
                    provider,
                    model,
                    remote_task_id,
                    status,
                    cloud_status,
                    prompt,
                    _safe_payload_json(submit_payload),
                    _safe_payload_json(status_payload),
                    now,
                    now,
                ),
            )
    return task_id


def _update_video_task(
    task_id: str,
    status: str,
    cloud_status: str,
    status_payload: dict,
    video_url: str | None = None,
    result_path: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE video_tasks
            SET status = ?, cloud_status = ?, status_payload = ?, video_url = COALESCE(?, video_url),
                result_path = COALESCE(?, result_path), error_code = ?, error_message = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                cloud_status,
                _safe_payload_json(status_payload),
                video_url,
                result_path,
                error_code,
                error_message,
                utc_now(),
                task_id,
            ),
        )


def _project_file_path(project_id: str, filename: str) -> Path:
    path = PROJECTS_DIR / project_id / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _local_asset_path(project_id: str, public_path: str | None) -> Path | None:
    if not public_path:
        return None
    filename = public_path.rsplit("/", 1)[-1]
    return PROJECTS_DIR / project_id / filename


def _local_asset_data_url(project_id: str, public_path: str | None) -> str | None:
    path = _local_asset_path(project_id, public_path)
    if not path or not path.exists() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return None
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _build_video_prompt(request: VideoAssetRequest) -> str:
    parts = [
        request.prompt,
        request.description,
        "cinematic motion, coherent subject movement, stable camera, natural lighting",
        "original visual design, no visible text, no watermark, no logo",
    ]
    if request.audio_prompt:
        parts.append(f"audio direction: {request.audio_prompt}")
    if request.negative_prompt:
        parts.append(f"avoid: {request.negative_prompt}")
    return ". ".join(part.strip(" .") for part in parts if part).strip()


def _ark_content_items(request: VideoAssetRequest, prompt: str) -> list[dict]:
    content = [{"type": "text", "text": prompt}]
    mode = (request.video_mode or "t2v").lower()
    if mode == "auto" and os.getenv("VOLC_VIDEO_USE_KEYFRAMES", "false").lower() in {"1", "true", "yes"}:
        mode = "keyframes"
    if mode not in {"i2v", "keyframes"}:
        return content

    first_frame = _local_asset_data_url(request.project_id, request.first_frame_path)
    last_frame = _local_asset_data_url(request.project_id, request.last_frame_path) if mode == "keyframes" else None
    if first_frame:
        first_item = {"type": "image_url", "image_url": {"url": first_frame}}
        if last_frame:
            first_item["role"] = "first_frame"
        content.append(first_item)
    if last_frame:
        content.append({"type": "image_url", "image_url": {"url": last_frame}, "role": "last_frame"})
    return content


def _compact_error(error: Exception) -> str:
    message = " ".join(str(error).replace("\n", " ").split())
    return message[:280] or error.__class__.__name__


def _ark_api_key() -> str:
    return os.getenv("VOLC_VIDEO_API_KEY") or os.getenv("VOLC_API_KEY") or ""


def _find_video_url(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"video_url", "videoUrl", "url"} and isinstance(item, str) and item.startswith("http"):
                return item
            found = _find_video_url(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_video_url(item)
            if found:
                return found
    return None


def _safe_payload_json(value: Any) -> str:
    return json.dumps(_sanitize_payload(value), ensure_ascii=False)


def _sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_payload(item) for item in value]
    if isinstance(value, str) and value.startswith("data:image/"):
        return f"{value[:32]}...<base64 omitted>"
    return value


def _extract_error(payload: dict) -> tuple[str, str]:
    candidates = [payload.get("error"), payload.get("message"), payload.get("reason")]
    for item in candidates:
        if isinstance(item, dict):
            code = str(item.get("code") or item.get("type") or "VIDEO_PROVIDER_ERROR")
            message = str(item.get("message") or item.get("msg") or item)
            return code, message
        if isinstance(item, str) and item.strip():
            return _guess_error_code(item), item
    return "VIDEO_PROVIDER_ERROR", json.dumps(payload, ensure_ascii=False)[:800]


def _guess_error_code(message: str) -> str:
    if "SetLimitExceeded" in message:
        return "SetLimitExceeded"
    if "SensitiveContent" in message or "PolicyViolation" in message:
        return "OutputVideoSensitiveContentDetected.PolicyViolation"
    if "AccessDenied" in message or "Permission" in message:
        return "AccessDenied"
    return "VIDEO_PROVIDER_ERROR"


def _friendly_video_error(code: str, message: str) -> str:
    if code == "SetLimitExceeded":
        return "Seedance 推理额度或安心体验限额已触发，模型服务被平台暂停。请在火山方舟开通管理中调整额度后重试。"
    if "SensitiveContent" in code or "PolicyViolation" in code:
        return "Seedance 内容安全或版权策略拦截了输出视频。请降低版权化人物/场景描述，换成更泛化的原创表达后重试。"
    if code == "AccessDenied":
        return "Seedance 模型或接入点权限不足。请检查火山方舟模型是否已开通、接入点是否可用。"
    return message
