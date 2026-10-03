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


def link_asset_to_path(
    conn,
    project_id: str,
    asset_type: str,
    name: str,
    description: str,
    prompt: str,
    file_path: str,
    embedding_ref: str,
) -> str:
    """登记「已存在的文件」为资产，**同一 (project_id, file_path) 只保留一行**。

    ⚠️ 2026-10-03 修 F4（数据卫生）：这些 linked 行只是**血缘面包屑**——它的 id 从不
    被调用方消费（`keyframe_service._linked_asset` 的返回值就是 `file_path`）。旧实现
    每次都插一条新的，于是**同一张首帧出现 2 条资产行**（1 条带元数据 + 1 条
    `asset_role/source/mime/width/height` 全空），而且同一文件每多挂一个镜头就再多一条
    （实测 2 镜 = 4 行指向同一 `file_path`）。

    现在已存在就直接复用那一行，不再制造空壳；返回该 `file_path` 对应的资产 id。
    """
    existing = conn.execute(
        "SELECT id FROM assets WHERE project_id = ? AND file_path = ? ORDER BY created_at, id LIMIT 1",
        (project_id, file_path),
    ).fetchone()
    if existing:
        return existing["id"]
    asset_id = f"asset_{uuid.uuid4().hex[:10]}"
    conn.execute(
        """
        INSERT INTO assets
        (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (asset_id, project_id, asset_type, name, description, prompt, file_path, embedding_ref, utc_now()),
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
    with connect() as conn:
        return link_asset_to_path(
            conn, project_id, asset_type, name, description, prompt, source_file_path, embedding_ref
        )


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
    """Read common image dimensions without adding an image-processing dependency.

    Returns ``(width, height)``.

    ⚠️ 2026-10-03 修 F3：JPEG 分支此前**把宽高顺序写反了**。JPEG 的 SOF 段里
    **HEIGHT 在 WIDTH 之前**（`FFCx` + 长度2 + 精度1 + **高2** + **宽2**），
    旧代码却把前两个字节当 width 返回，于是所有 JPEG 素材的 `assets.width/height`
    都被交换（实测：真实 1280×720 记成 720×1280）。现在按规范取 HEIGHT/WIDTH。

    ⚠️ 定位方式：**只对可能藏缩略图的 APP 段（APP1/APP2/APP13）整段跳过，其余标记
    一律逐字节前进**。这是被两个相反方向的坑夹出来的结论：
      · 纯逐字节扫描 → 会把 EXIF(APP1) 内嵌缩略图的 SOF 当成主图尺寸；
      · 按段长跳段   → 会把**畸形短段**后面的真 SOF 一起跳过。本项目既有的 158B
        最小 JPEG 夹具正是后者（DQT 声明长度 67、实际只占 65，真 SOF 落在声明段尾
        之前），跳段让它解析失败——而这条夹具是 `register_local_first_frame` 的
        既有契约，红了就是真回归。
    两条路都踩过，所以只保留「APP 段跳段」这一处必要跳段。

    代价说明：EXIF 缩略图这条防护**当前语料里零触发**（全工作区扫描 32,526 张图片，
    多 SOF 文件 0 个），保留它只为将来接入手机实拍图；本次真正被验证的是宽高顺序修正。
    """
    if suffix == ".png" and len(content) >= 24 and content.startswith(b"\x89PNG\r\n\x1a\n"):
        return struct.unpack(">II", content[16:24])
    if suffix in {".jpg", ".jpeg"} and content.startswith(b"\xff\xd8"):
        sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        # 缩略图只可能藏在 APP1(Exif) / APP2(Exif 扩展) / APP13(Photoshop) 段内，
        # 所以只对这三类「整段跳过」，**其余标记一律逐字节前进**。
        # 为什么不按所有段长跳段：段长字段本身可能不可信，而「声明段尾仍在文件内」
        # 也不代表它是对的——本项目 158B 最小 JPEG 夹具的 DQT 声明长度 67、实际只占
        # 65，真 SOF 落在声明段尾**之前**，跳段会把它一起跳过、解析直接失败。
        app_with_thumbnail = {0xE1, 0xE2, 0xED}
        index = 2
        while index + 1 < len(content):
            if content[index] != 0xFF:
                index += 1
                continue
            marker = content[index + 1]
            # 无长度字段的独立标记：TEM / SOI / EOI 与 RSTn
            if marker in {0x01, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                index += 2
                continue
            if marker == 0xDA:  # SOS：压缩数据开始，其后不再有 SOF
                break
            if marker in sof and index + 9 <= len(content):
                height = int.from_bytes(content[index + 5 : index + 7], "big")
                width = int.from_bytes(content[index + 7 : index + 9], "big")
                return width, height
            if marker in app_with_thumbnail and index + 4 <= len(content):
                segment_length = int.from_bytes(content[index + 2 : index + 4], "big")
                if segment_length >= 2 and index + 2 + segment_length <= len(content):
                    index += 2 + segment_length
                    continue
            index += 1
    return None, None
