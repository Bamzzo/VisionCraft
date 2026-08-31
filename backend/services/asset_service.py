import html
import hashlib
import mimetypes
import struct
import uuid
from pathlib import Path

from ..config import PROJECTS_DIR
from ..database import connect, utc_now


def project_asset_dir(project_id: str) -> Path:
    path = PROJECTS_DIR / project_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def public_asset_path(project_id: str, filename: str) -> str:
    return f"/assets/{project_id}/{filename}"


def create_placeholder_svg(
    project_id: str,
    asset_type: str,
    name: str,
    description: str,
    prompt: str,
    accent: str,
    embedding_ref: str | None = "provider:mock-svg",
) -> str:
    asset_id = f"asset_{uuid.uuid4().hex[:10]}"
    filename = f"{asset_id}.svg"
    file_path = project_asset_dir(project_id) / filename
    safe_name = html.escape(name)
    safe_type = html.escape(asset_type.upper())
    safe_desc = html.escape(description[:120])
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#111827"/>
      <stop offset="100%" stop-color="{accent}"/>
    </linearGradient>
  </defs>
  <rect width="960" height="540" rx="28" fill="url(#g)"/>
  <circle cx="790" cy="110" r="90" fill="rgba(255,255,255,0.12)"/>
  <circle cx="160" cy="440" r="130" fill="rgba(255,255,255,0.08)"/>
  <text x="56" y="88" fill="#f9fafb" font-family="Arial, sans-serif" font-size="24" letter-spacing="2">{safe_type}</text>
  <text x="56" y="170" fill="#ffffff" font-family="Arial, sans-serif" font-size="48" font-weight="700">{safe_name}</text>
  <foreignObject x="56" y="220" width="760" height="160">
    <div xmlns="http://www.w3.org/1999/xhtml" style="font-family: Arial, sans-serif; color: #d1d5db; font-size: 24px; line-height: 1.35;">{safe_desc}</div>
  </foreignObject>
</svg>"""
    file_path.write_text(svg, encoding="utf-8")

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                project_id,
                asset_type,
                name,
                description,
                prompt,
                public_asset_path(project_id, filename),
                embedding_ref,
                utc_now(),
            ),
        )
    return asset_id


def create_linked_asset(
    project_id: str,
    asset_type: str,
    name: str,
    description: str,
    prompt: str,
    source_file_path: str,
    embedding_ref: str,
) -> str:
    asset_id = f"asset_{uuid.uuid4().hex[:10]}"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                project_id,
                asset_type,
                name,
                description,
                prompt,
                source_file_path,
                embedding_ref,
                utc_now(),
            ),
        )
    return asset_id


def persist_binary_asset(
    project_id: str,
    asset_type: str,
    name: str,
    description: str,
    prompt: str,
    content: bytes,
    suffix: str,
    source_provider: str,
    source_model: str,
) -> str:
    """Store a provider result once, with media metadata for downstream use."""
    normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    asset_id = f"asset_{uuid.uuid4().hex[:10]}"
    filename = f"{asset_id}{normalized_suffix.lower()}"
    file_path = project_asset_dir(project_id) / filename
    file_path.write_bytes(content)
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    width, height = _image_dimensions(content, normalized_suffix.lower())
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO assets
            (id, project_id, type, name, description, prompt, file_path, embedding_ref,
             mime_type, byte_size, sha256, width, height, source_provider, source_model, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id, project_id, asset_type, name, description, prompt,
                public_asset_path(project_id, filename), f"provider:{source_provider}:{source_model}",
                mime_type, len(content), hashlib.sha256(content).hexdigest(), width, height,
                source_provider, source_model, utc_now(),
            ),
        )
    return asset_id


def persist_uploaded_asset(
    project_id: str,
    *,
    asset_type: str,
    asset_role: str,
    name: str,
    content: bytes,
    suffix: str,
    mime_type: str,
    width: int | None = None,
    height: int | None = None,
    duration_seconds: float | None = None,
    source: str = "user-upload",
) -> dict:
    """Save a user-uploaded file under a server-generated unique name."""
    normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    asset_id = f"asset_{uuid.uuid4().hex[:10]}"
    filename = f"{asset_id}{normalized_suffix.lower()}"
    file_path = project_asset_dir(project_id) / filename
    public_path = public_asset_path(project_id, filename)
    if width is None or height is None:
        detected_w, detected_h = _image_dimensions(content, normalized_suffix.lower())
        width = width if width is not None else detected_w
        height = height if height is not None else detected_h
    try:
        file_path.write_bytes(content)
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO assets
                (id, project_id, type, name, description, prompt, file_path, embedding_ref,
                 mime_type, byte_size, sha256, width, height, duration_seconds,
                 asset_role, source, source_provider, source_model, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    project_id,
                    asset_type,
                    name[:80] or asset_id,
                    "用户上传到当前项目的素材。",
                    "user-upload",
                    public_path,
                    f"upload:{asset_role}",
                    mime_type,
                    len(content),
                    hashlib.sha256(content).hexdigest(),
                    width,
                    height,
                    duration_seconds,
                    asset_role,
                    source,
                    "user-upload",
                    asset_role,
                    utc_now(),
                ),
            )
    except Exception:
        file_path.unlink(missing_ok=True)
        raise
    return {
        "id": asset_id,
        "project_id": project_id,
        "type": asset_type,
        "role": asset_role,
        "file_path": public_path,
        "mime_type": mime_type,
        "byte_size": len(content),
        "width": width,
        "height": height,
        "duration_seconds": duration_seconds,
        "name": name[:80] or asset_id,
        "source": source,
    }


def _image_dimensions(content: bytes, suffix: str) -> tuple[int | None, int | None]:
    """Read common image dimensions without adding an image-processing dependency."""
    if suffix == ".png" and len(content) >= 24 and content.startswith(b"\x89PNG\r\n\x1a\n"):
        return struct.unpack(">II", content[16:24])
    if suffix in {".jpg", ".jpeg"} and content.startswith(b"\xff\xd8"):
        sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        index = 2
        while index + 8 < len(content):
            if content[index] != 0xFF:
                index += 1
                continue
            marker = content[index + 1]
            if marker == 0xDA:
                break
            if marker in sof:
                return int.from_bytes(content[index + 5 : index + 7], "big"), int.from_bytes(content[index + 7 : index + 9], "big")
            index += 1
    return None, None
