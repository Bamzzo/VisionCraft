from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.database import connect, to_json, utc_now
from backend.services.memory_service import get_collection, index_project_memory


PROJECT_ID = "eval_project_001"


def ensure_eval_project(project_id: str = PROJECT_ID) -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.execute(
            """
            INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds,
             shot_count_mode, requested_shot_count, review_mode, archived, status,
             routing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                "璃京风雨",
                _source_text(),
                "cinematic historical realism",
                "16:9",
                5,
                "manual",
                5,
                0,
                0,
                "ready_for_review",
                "direct",
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO story_bibles
            (project_id, summary, worldview, style_tags, themes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                "李昭在御书房收到密诏，沈青岚在旧藏书楼查出旧案，玄衣信使把雨巷中的密信送到城门月台。",
                "故事发生在雨季的璃京，宫廷权力、旧案档案和城门离别共同构成视觉锚点。",
                to_json(["historical", "rain", "political mystery"]),
                to_json(["信任", "旧案", "选择"]),
                now,
            ),
        )
        for row in _characters(project_id, now):
            conn.execute(
                """
                INSERT INTO characters
                (id, project_id, name, role, description, visual_prompt, asset_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
        for row in _scenes(project_id, now):
            conn.execute(
                """
                INSERT INTO scenes
                (id, project_id, name, description, visual_prompt, asset_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
        for row in _assets(project_id, now):
            conn.execute(
                """
                INSERT INTO assets
                (id, project_id, type, name, description, prompt, file_path, embedding_ref, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
        for shot, version in _shots(project_id, now):
            conn.execute(
                """
                INSERT INTO shots
                (id, project_id, shot_index, title, description, characters, scene, camera_motion,
                 visual_prompt, negative_prompt, audio_prompt, rag_evidence, status, retry_count,
                 current_version_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                shot,
            )
            conn.execute(
                """
                INSERT INTO shot_versions
                (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
                 first_frame_path, last_frame_path, video_path, video_mode, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                version,
            )
    index_project_memory(project_id)


def load_eval_set(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_memory_rows(project_id: str = PROJECT_ID) -> list[dict]:
    collection = get_collection()
    result = collection.get(where={"project_id": project_id}, include=["documents", "metadatas"])
    rows = []
    for item_id, document, metadata in zip(
        result.get("ids") or [],
        result.get("documents") or [],
        result.get("metadatas") or [],
    ):
        rows.append(
            {
                "id": item_id,
                "label": (metadata or {}).get("label", ""),
                "kind": (metadata or {}).get("kind", ""),
                "document": document or "",
            }
        )
    return rows


def _source_text() -> str:
    return (
        "雨季的璃京，皇帝李昭在御书房收到一封密诏。密诏提到十年前被封存的旧案，"
        "线索藏在旧藏书楼的案卷之间。修史官沈青岚奉命查阅档案，她在青铜灯下发现"
        "被调换的页码。玄衣信使穿过雨巷，将一枚银杏叶作为约定信号送到城门月台。"
        "最终李昭与沈青岚在城门月台会合，决定公开旧案真相。"
    )


def _characters(project_id: str, now: str) -> list[tuple]:
    return [
        (
            "eval_char_lizhao",
            project_id,
            "李昭",
            "emperor",
            "璃京的年轻皇帝，也可称为君主，常在御书房审阅奏章和密诏。",
            "young emperor in imperial study, restrained authority, rain season",
            None,
            now,
        ),
        (
            "eval_char_qinglan",
            project_id,
            "沈青岚",
            "archivist",
            "修史官与女史，负责在旧藏书楼修补档案并查出旧案真相。",
            "female historian archivist, old archive building, bronze lamp",
            None,
            now,
        ),
        (
            "eval_char_messenger",
            project_id,
            "玄衣信使",
            "messenger",
            "穿黑色雨衣的送信人，第一次出现在雨巷，负责传递密信和银杏叶信物。",
            "black clothed messenger in rainy alley, carrying secret letter",
            None,
            now,
        ),
    ]


def _scenes(project_id: str, now: str) -> list[tuple]:
    return [
        ("eval_scene_study", project_id, "御书房", "宫殿深处的书房，李昭在这里收到密诏并审阅奏章。", "imperial study, scrolls, rain outside window", None, now),
        ("eval_scene_alley", project_id, "雨巷", "狭窄潮湿的小巷，玄衣信使在夜雨中传递密信。", "rainy alley, wet stone, secret messenger", None, now),
        ("eval_scene_archive", project_id, "旧藏书楼", "保存旧案档案的楼阁，沈青岚在青铜灯下查出调换页码。", "old archive building, bronze lamp, files", None, now),
        ("eval_scene_gate", project_id, "城门月台", "城门外的高台，银杏叶作为约定信号，李昭和沈青岚最终会合。", "city gate platform, ginkgo leaf, farewell mood", None, now),
    ]


def _assets(project_id: str, now: str) -> list[tuple]:
    return [
        (
            "eval_asset_lamp",
            project_id,
            "scene",
            "青铜灯视觉锚点",
            "旧藏书楼里的青铜灯照亮案卷，是沈青岚发现旧案真相的视觉锚点。",
            "bronze lamp lighting archival papers",
            "/assets/eval_project_001/eval_bronze_lamp.png",
            "eval:asset:lamp",
            now,
        ),
        (
            "eval_asset_leaf",
            project_id,
            "scene",
            "银杏叶视觉锚点",
            "银杏叶是城门月台的约定信号，和离别、会合、公开旧案有关。",
            "ginkgo leaf signal on city gate platform",
            "/assets/eval_project_001/eval_ginkgo_leaf.png",
            "eval:asset:leaf",
            now,
        ),
    ]


def _shots(project_id: str, now: str) -> list[tuple[tuple, tuple]]:
    data = [
        ("eval_shot_1", "eval_version_1", 1, "镜头1 密诏", "李昭在御书房收到密诏，雨水敲打窗棂，奏章散在案头。", ["李昭"], "御书房"),
        ("eval_shot_2", "eval_version_2", 2, "镜头2 雨巷", "玄衣信使第一次出现在雨巷，将密信藏入油纸伞柄。", ["玄衣信使"], "雨巷"),
        ("eval_shot_3", "eval_version_3", 3, "镜头3 案卷", "沈青岚在旧藏书楼翻开案卷，青铜灯照出被调换的页码。", ["沈青岚"], "旧藏书楼"),
        ("eval_shot_4", "eval_version_4", 4, "镜头4 烛火", "李昭读到旧案真相，烛火摇晃，决定召见沈青岚。", ["李昭", "沈青岚"], "御书房"),
        ("eval_shot_5", "eval_version_5", 5, "镜头5 城门", "李昭和沈青岚在城门月台会合，银杏叶落在石阶上。", ["李昭", "沈青岚"], "城门月台"),
    ]
    rows = []
    for shot_id, version_id, index, title, desc, chars, scene in data:
        visual = f"{title}, {desc}, scene {scene}, cinematic historical realism"
        shot = (
            shot_id,
            project_id,
            index,
            title,
            desc,
            to_json(chars),
            scene,
            "slow cinematic movement",
            visual,
            "low quality, watermark",
            "rain ambience and low strings",
            "[]",
            "keyframes_ready",
            0,
            version_id,
            now,
            now,
        )
        version = (
            version_id,
            shot_id,
            1,
            desc,
            visual,
            "low quality, watermark",
            "rain ambience and low strings",
            f"/assets/{project_id}/{shot_id}_first.png",
            f"/assets/{project_id}/{shot_id}_last.png",
            None,
            "t2v",
            "eval_seed",
            now,
        )
        rows.append((shot, version))
    return rows
