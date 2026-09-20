"""角色与场景的视觉锚点。

锚点是"一致性"的产品机制。数据模型从一开始就留了位置
（`characters.asset_id` / `scenes.asset_id`，前端也留了预览位），但在这之前
没有任何流程会写入这两个字段——实测 7 个项目 12 个角色全是 NULL。

锚点与镜头参考图（`shot_versions.reference_frame_path`）是两件事：
参考图属于单个镜头，锚点属于角色或场景，可以供多个镜头取用。
"""
from __future__ import annotations

from typing import Any

from ..database import connect

ANCHOR_KINDS = {"character", "scene"}

_TABLE = {"character": "characters", "scene": "scenes"}
_LABEL = {"character": "角色", "scene": "场景"}
# 占位图（create_placeholder_svg）没有 mime_type，只能靠 type 判断是不是图。
_IMAGE_TYPES = {"reference", "character-anchor", "scene-anchor", "first-frame", "image", "keyframe"}


class AnchorError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def _require_kind(kind: str) -> str:
    value = (kind or "").strip()
    if value not in ANCHOR_KINDS:
        raise AnchorError("INVALID_KIND", "锚点类型无效。请使用 character 或 scene。")
    return value


def _require_project(project_id: str) -> None:
    with connect() as conn:
        row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise AnchorError("PROJECT_NOT_FOUND", "项目不存在。", status_code=404)


def _find_target(project_id: str, kind: str, ref: str) -> Any:
    """按 id 或名称定位角色/场景。名称是 Bible 卡片与 characters 表的既有对接口径。"""
    table = _TABLE[kind]
    value = (ref or "").strip()
    if not value:
        raise AnchorError("TARGET_REQUIRED", f"请指定要挂载锚点的{_LABEL[kind]}。")
    with connect() as conn:
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (value,)).fetchone()
        if row is None:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE project_id = ? AND name = ?", (project_id, value)
            ).fetchone()
        foreign = None
        if row is None:
            foreign = conn.execute(f"SELECT project_id FROM {table} WHERE id = ?", (value,)).fetchone()
    if row is None:
        if foreign and foreign["project_id"] != project_id:
            raise AnchorError("TARGET_MISMATCH", "该角色或场景不属于当前项目。")
        raise AnchorError("TARGET_NOT_FOUND", f"当前项目里找不到这个{_LABEL[kind]}。", status_code=404)
    if row["project_id"] != project_id:
        raise AnchorError("TARGET_MISMATCH", "该角色或场景不属于当前项目。")
    return row


def _require_image_asset(project_id: str, asset_id: str) -> Any:
    value = (asset_id or "").strip()
    if not value:
        raise AnchorError("ASSET_REQUIRED", "请先选择一张参考图。")
    with connect() as conn:
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (value,)).fetchone()
    if row is None:
        raise AnchorError("ASSET_NOT_FOUND", "参考图不存在。", status_code=404)
    if row["project_id"] != project_id:
        raise AnchorError("ASSET_MISMATCH", "该素材不属于当前项目。")
    mime = (row["mime_type"] or "").lower()
    if not mime.startswith("image/") and (row["type"] or "") not in _IMAGE_TYPES:
        raise AnchorError("ASSET_NOT_IMAGE", "锚点只能使用图片素材。")
    return row


def _payload(kind: str, row: Any, file_path: str | None) -> dict:
    return {
        "kind": kind,
        "id": row["id"],
        "name": row["name"],
        "asset_id": row["asset_id"],
        "file_path": file_path if row["asset_id"] else None,
    }


def attach_anchor(project_id: str, *, kind: str, target: str, asset_id: str) -> dict:
    _require_project(project_id)
    resolved_kind = _require_kind(kind)
    row = _find_target(project_id, resolved_kind, target)
    asset = _require_image_asset(project_id, asset_id)
    table = _TABLE[resolved_kind]
    with connect() as conn:
        conn.execute(f"UPDATE {table} SET asset_id = ? WHERE id = ?", (asset["id"], row["id"]))
    return {
        "kind": resolved_kind,
        "id": row["id"],
        "name": row["name"],
        "asset_id": asset["id"],
        "file_path": asset["file_path"],
        "previous_asset_id": row["asset_id"],
    }


def detach_anchor(project_id: str, *, kind: str, target: str) -> dict:
    """只解除关联，不删除素材文件——删素材是不可逆动作，不放在这个接口里。"""
    _require_project(project_id)
    resolved_kind = _require_kind(kind)
    row = _find_target(project_id, resolved_kind, target)
    table = _TABLE[resolved_kind]
    with connect() as conn:
        conn.execute(f"UPDATE {table} SET asset_id = NULL WHERE id = ?", (row["id"],))
    return {"kind": resolved_kind, "id": row["id"], "name": row["name"], "asset_id": None,
            "previous_asset_id": row["asset_id"]}


def list_anchors(project_id: str) -> list[dict]:
    _require_project(project_id)
    items: list[dict] = []
    with connect() as conn:
        paths = {
            row["id"]: row["file_path"]
            for row in conn.execute("SELECT id, file_path FROM assets WHERE project_id = ?", (project_id,)).fetchall()
        }
        for kind in ("character", "scene"):
            table = _TABLE[kind]
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE project_id = ? ORDER BY created_at", (project_id,)
            ).fetchall()
            for row in rows:
                items.append(_payload(kind, row, paths.get(row["asset_id"])))
    return items
