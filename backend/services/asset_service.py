import html
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
