"""Project-scoped user asset upload. No object storage, no external URLs, no live APIs."""
from __future__ import annotations

import re
from pathlib import Path

from ..database import connect
from ..services.asset_service import persist_uploaded_asset
from ..services.media_transfer_service import MediaTransferError, sniff_raster_image
from ..services.project_service import get_project

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_AUDIO_BYTES = 50 * 1024 * 1024
MAX_SRT_BYTES = 2 * 1024 * 1024
MAX_SUBTITLE_CHARS = 20_000
MAX_AUDIO_SECONDS = 600
MAX_SRT_CUES = 500

IMAGE_ROLES = {"keyframe", "first_frame", "last_frame", "reference_image"}
AUDIO_ROLES = {"audio", "background_audio"}
SUBTITLE_ROLES = {"subtitle"}
ALLOWED_ROLES = IMAGE_ROLES | AUDIO_ROLES | SUBTITLE_ROLES

ROLE_ASSET_TYPE = {
    "keyframe": "keyframe",
    "first_frame": "first-frame",
    "last_frame": "last-frame",
    "reference_image": "reference",
    "audio": "audio",
    "background_audio": "audio",
    "subtitle": "subtitle",
}

_SRT_ARROW = re.compile(
    r"^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


class AssetUploadError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def upload_project_asset(
    project_id: str,
    *,
    asset_role: str,
    content: bytes | None = None,
    filename: str = "",
    shot_id: str | None = None,
    subtitle_text: str | None = None,
) -> dict:
    project = get_project(project_id)
    if not project:
        raise AssetUploadError("PROJECT_NOT_FOUND", "项目不存在。", status_code=404)
    role = (asset_role or "").strip()
    if role not in ALLOWED_ROLES:
        raise AssetUploadError(
            "INVALID_ROLE",
            "素材角色无效。图片请使用 keyframe / first_frame / last_frame / reference_image；音频请使用 background_audio；字幕请使用 subtitle。",
        )
    if shot_id:
        _require_shot(project, project_id, shot_id)
        if role in AUDIO_ROLES or role in SUBTITLE_ROLES:
            raise AssetUploadError("INVALID_ROLE", "音频和字幕只能登记为当前项目成片素材，不能挂到镜头。")
        if role in IMAGE_ROLES and role not in {"first_frame", "last_frame", "reference_image", "keyframe"}:
            raise AssetUploadError("INVALID_ROLE", "该图片角色不能挂到镜头。")

    safe_name = _safe_filename(filename)
    if role in IMAGE_ROLES:
        asset = _save_image(project_id, role, content or b"", safe_name)
        try:
            attached = _attach_image(project_id, shot_id, role, asset["file_path"]) if shot_id else None
        except AssetUploadError:
            _rollback_asset(asset["id"], _disk_path(asset["file_path"], project_id))
            raise
        return {"ok": True, "asset": asset, "shot": attached}
    if role in AUDIO_ROLES:
        asset = _save_audio(project_id, role, content or b"", safe_name)
        return {"ok": True, "asset": asset}
    asset = _save_subtitle(project_id, content, subtitle_text, safe_name)
    return {"ok": True, "asset": asset}


def public_asset_payload(asset: dict) -> dict:
    return {
        "id": asset.get("id"),
        "project_id": asset.get("project_id"),
        "type": asset.get("type"),
        "role": asset.get("role") or asset.get("asset_role"),
        "file_path": asset.get("file_path"),
        "mime_type": asset.get("mime_type"),
        "byte_size": asset.get("byte_size"),
        "width": asset.get("width"),
        "height": asset.get("height"),
        "duration_seconds": asset.get("duration_seconds"),
        "name": asset.get("name"),
    }


def _save_image(project_id: str, role: str, content: bytes, name: str) -> dict:
    if not content:
        raise AssetUploadError("EMPTY_FILE", "请选择一张 JPEG 或 PNG 图片。")
    if len(content) > MAX_IMAGE_BYTES:
        raise AssetUploadError("FILE_TOO_LARGE", "上传文件超过大小限制。图片不超过 20 MB。")
    try:
        mime_type, suffix = sniff_raster_image(content, register_only=True)
    except MediaTransferError as exc:
        raise AssetUploadError(exc.code, _image_error(exc)) from exc
    if suffix not in {".png", ".jpg", ".jpeg"}:
        raise AssetUploadError("UNSUPPORTED_IMAGE_FORMAT", "仅支持 JPEG 或 PNG 图片。")
    width, height = _read_image_size(content, suffix)
    if not width or not height:
        raise AssetUploadError("UNSUPPORTED_IMAGE_FORMAT", "无法读取图片尺寸。请重新选择有效的 JPEG 或 PNG。")
    return persist_uploaded_asset(
        project_id,
        asset_type=ROLE_ASSET_TYPE[role],
        asset_role=role,
        name=name or "uploaded-image",
        content=content,
        suffix=suffix,
        mime_type=mime_type,
        width=width,
        height=height,
    )


def _save_audio(project_id: str, role: str, content: bytes, name: str) -> dict:
    if not content:
        raise AssetUploadError("EMPTY_FILE", "请选择 WAV、MP3、M4A、AAC 或 OGG 音频。")
    if len(content) > MAX_AUDIO_BYTES:
        raise AssetUploadError("FILE_TOO_LARGE", "上传文件超过大小限制。音频不超过 50 MB。")
    suffix = _sniff_audio_suffix(content, name)
    if not suffix:
        raise AssetUploadError("AUDIO_UNREADABLE", "文件内容无法识别为有效音频。")
    tmp_asset = persist_uploaded_asset(
        project_id,
        asset_type="audio",
        asset_role=role,
        name=name or "uploaded-audio",
        content=content,
        suffix=suffix,
        mime_type=_audio_mime(suffix),
    )
    disk = _disk_path(tmp_asset["file_path"], project_id)
    try:
        duration, readable = _probe_audio(disk)
        if not readable:
            raise AssetUploadError("AUDIO_UNREADABLE", "文件内容无法识别为有效音频。")
        if duration <= 0:
            raise AssetUploadError("AUDIO_UNREADABLE", "无法读取音频时长。请更换可播放的音频文件。")
        if duration > MAX_AUDIO_SECONDS:
            raise AssetUploadError("AUDIO_TOO_LONG", "音频时长超过 10 分钟上限，请裁剪后再上传。")
        _update_duration(tmp_asset["id"], duration)
        tmp_asset["duration_seconds"] = duration
        return tmp_asset
    except AssetUploadError:
        _rollback_asset(tmp_asset["id"], disk)
        raise
    except Exception:
        _rollback_asset(tmp_asset["id"], disk)
        raise AssetUploadError("AUDIO_UNREADABLE", "文件内容无法识别为有效音频。")


def _save_subtitle(project_id: str, content: bytes | None, subtitle_text: str | None, name: str) -> dict:
    if content:
        if len(content) > MAX_SRT_BYTES:
            raise AssetUploadError("FILE_TOO_LARGE", "上传文件超过大小限制。字幕不超过 2 MB。")
        text = _decode_text(content)
        _validate_srt(text)
        payload = text.encode("utf-8")
        label = name or "uploaded-subtitle.srt"
    else:
        body = (subtitle_text or "").strip()
        if not body:
            raise AssetUploadError("EMPTY_FILE", "请上传 SRT 文件，或填写字幕文本后生成。")
        if len(body) > MAX_SUBTITLE_CHARS:
            raise AssetUploadError("FILE_TOO_LARGE", "字幕文本过长。请缩短到 20,000 字以内。")
        payload = _plain_text_to_srt(body).encode("utf-8")
        label = "generated-subtitle.srt"
    return persist_uploaded_asset(
        project_id,
        asset_type="subtitle",
        asset_role="subtitle",
        name=label,
        content=payload,
        suffix=".srt",
        mime_type="application/x-subrip",
    )


def _attach_image(project_id: str, shot_id: str, role: str, public_path: str) -> dict | None:
    try:
        if role == "first_frame":
            from .keyframe_service import select_shot_keyframes

            return select_shot_keyframes(project_id, shot_id, first_frame_path=public_path, last_frame_path=None)
        if role == "last_frame":
            from .keyframe_service import select_shot_keyframes

            return select_shot_keyframes(project_id, shot_id, first_frame_path=None, last_frame_path=public_path)
        if role == "reference_image":
            from .shot_edit_service import save_shot_draft

            return save_shot_draft(project_id, shot_id, {"reference_frame_path": public_path})
    except (RuntimeError, ValueError) as exc:
        raise AssetUploadError("ATTACH_FAILED", "无法将图片挂到当前镜头。请确认镜头属于当前项目，且素材是 JPEG 或 PNG。") from exc
    return None


def _require_shot(project: dict, project_id: str, shot_id: str) -> None:
    shots = project.get("shots") or []
    if not any(shot.get("id") == shot_id for shot in shots):
        with connect() as conn:
            foreign = conn.execute("SELECT project_id FROM shots WHERE id = ?", (shot_id,)).fetchone()
        if foreign and foreign["project_id"] != project_id:
            raise AssetUploadError("SHOT_MISMATCH", "该素材不属于当前项目。")
        raise AssetUploadError("SHOT_NOT_FOUND", "镜头不存在。请先确认分镜再上传图片。", status_code=404)


def _safe_filename(name: str) -> str:
    base = (name or "").replace("\\", "/").split("/")[-1]
    base = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", base).strip("._")
    return base[:80]


def _decode_text(content: bytes) -> str:
    if content.startswith(b"\xef\xbb\xbf"):
        content = content[3:]
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssetUploadError("SRT_INVALID", "字幕必须是 UTF-8 文本。") from exc


def _validate_srt(text: str) -> None:
    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    cues = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line or line.isdigit():
            index += 1
            continue
        match = _SRT_ARROW.match(line)
        if not match:
            index += 1
            continue
        start = _srt_ms(match.groups()[0:4])
        end = _srt_ms(match.groups()[4:8])
        if start >= end:
            raise AssetUploadError("SRT_INVALID", "字幕时间轴格式不正确。开始时间必须早于结束时间。")
        cues.append(start)
        index += 1
    if not cues:
        raise AssetUploadError("SRT_INVALID", "字幕时间轴格式不正确。请使用标准 SRT 时间轴。")
    if len(cues) > MAX_SRT_CUES:
        raise AssetUploadError("SRT_INVALID", "字幕条目过多。请精简后再上传。")
    for prev, current in zip(cues, cues[1:]):
        if current < prev:
            raise AssetUploadError("SRT_INVALID", "字幕时间轴格式不正确。条目时间必须按顺序递增。")


def _srt_ms(parts: tuple[str, ...]) -> int:
    hours, minutes, seconds, millis = (int(part) for part in parts)
    millis = millis if millis >= 100 or len(parts[3]) == 3 else millis * 10
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def _plain_text_to_srt(text: str) -> str:
    body = "\n".join(line.strip() for line in text.splitlines() if line.strip()) or text.strip()
    return f"1\n00:00:00,000 --> 00:00:05,000\n{body}\n"


def _sniff_audio_suffix(content: bytes, filename: str) -> str | None:
    if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WAVE":
        return ".wav"
    if content.startswith(b"OggS"):
        return ".ogg"
    if content.startswith(b"ID3") or (len(content) >= 2 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0):
        return ".mp3"
    if len(content) >= 8 and content[4:8] == b"ftyp":
        brand = content[8:12]
        if brand in {b"M4A ", b"M4B ", b"mp42", b"isom", b"mp41", b"M4V "}:
            return ".m4a"
        return ".m4a"
    if len(content) >= 2 and content[0] == 0xFF and (content[1] & 0xF6) in {0xF0, 0xF2, 0xF4, 0xF6}:
        return ".aac"
    hinted = Path(_safe_filename(filename)).suffix.lower()
    if hinted in {".wav", ".mp3", ".m4a", ".aac", ".ogg"}:
        return hinted
    return None


def _audio_mime(suffix: str) -> str:
    return {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
    }.get(suffix, "application/octet-stream")


def _probe_audio(path: Path) -> tuple[float, bool]:
    from .video_service import _ffprobe_executable, _ffprobe_json

    if not _ffprobe_executable():
        raise AssetUploadError("AUDIO_PROBE_UNAVAILABLE", "本机没有可用的 FFprobe，无法校验音频。请安装 FFmpeg 后重试。")
    payload = _ffprobe_json(path)
    streams = payload.get("streams") or []
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    duration = float((payload.get("format") or {}).get("duration") or 0)
    if not duration:
        for stream in streams:
            if stream.get("codec_type") == "audio" and stream.get("duration"):
                duration = float(stream["duration"])
                break
    return duration, has_audio


def _read_image_size(content: bytes, suffix: str) -> tuple[int | None, int | None]:
    from .asset_service import _image_dimensions

    return _image_dimensions(content, suffix)


def _disk_path(public_path: str, project_id: str) -> Path:
    from ..config import PROJECTS_DIR

    filename = public_path.rsplit("/", 1)[-1]
    return PROJECTS_DIR / project_id / filename


def _update_duration(asset_id: str, duration: float) -> None:
    with connect() as conn:
        conn.execute("UPDATE assets SET duration_seconds = ? WHERE id = ?", (duration, asset_id))


def _rollback_asset(asset_id: str, disk: Path) -> None:
    disk.unlink(missing_ok=True)
    with connect() as conn:
        conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))


def _image_error(exc: MediaTransferError) -> str:
    if exc.code == "SVG_NOT_ALLOWED":
        return "仅支持 JPEG 或 PNG 图片。"
    if exc.code == "UNSUPPORTED_IMAGE_FORMAT":
        return "仅支持 JPEG 或 PNG 图片。"
    return "仅支持 JPEG 或 PNG 图片。"
