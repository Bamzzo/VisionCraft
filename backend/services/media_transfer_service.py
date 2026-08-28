"""Provider-neutral image handoff for image-to-video generation.

The workflow supplies an existing VisionCraft asset path and semantic role.
This layer validates the local bytes, creates the downstream representation,
and records provenance without letting provider-specific payload formats leak
into workflow code.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..config import PROJECTS_DIR
from ..database import connect, utc_now

SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_LOCAL_IMAGE_BYTES = 12 * 1024 * 1024


class MediaTransferError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class MediaReference:
    asset_id: str
    role: str
    transfer_mode: str
    url: str
    mime_type: str
    sha256: str
    byte_size: int


def prepare_image_reference(
    project_id: str,
    public_path: str | None,
    *,
    target_provider: str,
    target_model: str,
    role: str,
) -> MediaReference | None:
    """Build a safe provider input reference from an existing project asset.

    ``None`` means no optional reference frame was selected.  All validation
    failures are explicit; callers must not silently fall back to T2V.
    """
    if not public_path:
        return None
    asset = _load_asset(project_id, public_path)
    local_path = _resolve_project_file(project_id, public_path)
    content = local_path.read_bytes()
    if len(content) > MAX_LOCAL_IMAGE_BYTES:
        raise MediaTransferError(
            "IMAGE_TOO_LARGE",
            f"Reference image is {len(content)} bytes; limit is {MAX_LOCAL_IMAGE_BYTES} bytes before provider upload.",
        )
    suffix = local_path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        raise MediaTransferError(
            "UNSUPPORTED_IMAGE_FORMAT",
            f"Reference image format {suffix or 'unknown'} is not supported. Use PNG, JPEG, or WebP.",
        )
    mime_type = mimetypes.guess_type(local_path.name)[0] or "image/png"
    sha256 = hashlib.sha256(content).hexdigest()
    mode, reference = _compile_reference(content, mime_type, public_path)
    _record_transfer(
        asset_id=asset["id"],
        target_provider=target_provider,
        target_model=target_model,
        transfer_mode=mode,
        role=role,
        request_reference=_redact_reference(reference, mode),
        metadata={"mime_type": mime_type, "byte_size": len(content), "sha256": sha256, "source_path": public_path},
    )
    _backfill_asset_metadata(asset["id"], mime_type, len(content), sha256)
    return MediaReference(asset["id"], role, mode, reference, mime_type, sha256, len(content))


def _compile_reference(content: bytes, mime_type: str, public_path: str) -> tuple[str, str]:
    mode = os.getenv("VISIONCRAFT_MEDIA_TRANSFER_MODE", "data_url").lower()
    if mode == "public_url":
        base_url = os.getenv("VISIONCRAFT_MEDIA_PUBLIC_BASE_URL", "").rstrip("/")
        if not base_url:
            raise MediaTransferError(
                "MEDIA_PUBLIC_URL_NOT_CONFIGURED",
                "Public URL mode requires VISIONCRAFT_MEDIA_PUBLIC_BASE_URL. Use data_url for local development.",
            )
        return "public_url", base_url + public_path
    if mode not in {"data_url", "auto"}:
        raise MediaTransferError("UNKNOWN_MEDIA_TRANSFER_MODE", f"Unknown media transfer mode: {mode}")
    encoded = base64.b64encode(content).decode("ascii")
    return "data_url", f"data:{mime_type};base64,{encoded}"


def _load_asset(project_id: str, public_path: str):
    with connect() as conn:
        asset = conn.execute(
            "SELECT * FROM assets WHERE project_id = ? AND file_path = ?",
            (project_id, public_path),
        ).fetchone()
    if not asset:
        raise MediaTransferError("ASSET_NOT_FOUND", "Selected reference image does not belong to this project.")
    return asset


def _resolve_project_file(project_id: str, public_path: str) -> Path:
    expected_prefix = f"/assets/{project_id}/"
    if not public_path.startswith(expected_prefix):
        raise MediaTransferError("INVALID_ASSET_PATH", "Reference asset path is outside the current project.")
    filename = public_path[len(expected_prefix) :]
    if not filename or Path(filename).name != filename:
        raise MediaTransferError("INVALID_ASSET_PATH", "Reference asset filename is invalid.")
    path = PROJECTS_DIR / project_id / filename
    if not path.is_file():
        raise MediaTransferError("ASSET_FILE_MISSING", "Reference asset metadata exists but its local file is missing.")
    return path


def _record_transfer(**kwargs) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO media_transfers
            (id, asset_id, target_provider, target_model, transfer_mode, role, request_reference, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"mt_{uuid.uuid4().hex[:12]}",
                kwargs["asset_id"], kwargs["target_provider"], kwargs["target_model"], kwargs["transfer_mode"],
                kwargs["role"], kwargs["request_reference"], json.dumps(kwargs["metadata"], ensure_ascii=False), utc_now(),
            ),
        )


def _backfill_asset_metadata(asset_id: str, mime_type: str, byte_size: int, sha256: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE assets SET mime_type = COALESCE(mime_type, ?), byte_size = COALESCE(byte_size, ?), sha256 = COALESCE(sha256, ?) WHERE id = ?",
            (mime_type, byte_size, sha256, asset_id),
        )


def _redact_reference(reference: str, mode: str) -> str:
    return "<data-url-omitted>" if mode == "data_url" else reference
