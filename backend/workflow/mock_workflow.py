import time
import uuid

from ..database import connect, to_json, utc_now
from ..providers.image_provider import ImageAssetRequest, generate_image_asset
from ..providers.llm_provider import ProviderError, generate_story_plan, live_llm_available
from ..services.job_service import update_job
from ..services.project_service import clear_generated_project_data, compute_shot_count, save_story_bible, update_project_status
from ..services.asset_service import create_linked_asset
from ..schemas import ProjectCreate


AGENT_STEPS = [
    ("Narrative Planner", 12, "Analyzing source text and building story bible"),
    ("Visual Director", 28, "Extracting characters, scenes, and visual rules"),
    ("Asset Generator", 46, "Creating baseline visual assets"),
    ("Key Animator", 68, "Drafting storyboard shots and keyframe prompts"),
    ("Visual Critic", 82, "Checking narrative and visual consistency"),
    ("Sequence Assembler", 96, "Preparing export-ready production package"),
]


def run_mock_workflow(project_id: str, job_id: str) -> None:
    try:
        update_project_status(project_id, "running")
        update_job(job_id, "running", 3, "Workflow started")
        clear_generated_project_data(project_id)

        with connect() as conn:
            project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not project:
            update_job(job_id, "failed", 0, "Project not found", "Project not found")
            return

        payload = ProjectCreate(
            title=project["title"],
            source_text=project["source_text"],
            style=project["style"],
            aspect_ratio=project["aspect_ratio"],
            duration_seconds=project["duration_seconds"],
            shot_count_mode=project["shot_count_mode"],
            requested_shot_count=project["requested_shot_count"],
        )

        for _, progress, message in AGENT_STEPS:
            update_job(job_id, "running", progress, message)
            time.sleep(0.25)

        shot_count = compute_shot_count(payload)
        story_data = _build_story_data(payload)
        if live_llm_available():
            update_job(job_id, "running", 36, f"Calling live LLM provider for {project['routing_mode']} story planning")
            try:
                story_data = _normalize_live_story_data(generate_story_plan(payload, shot_count), payload)
            except ProviderError as exc:
                update_job(job_id, "running", 40, f"Live LLM failed, fallback to mock planner: {exc}")
        save_story_bible(
            project_id,
            story_data["summary"],
            story_data["worldview"],
            story_data["style_tags"],
            story_data["themes"],
        )

        character_assets = _insert_characters(project_id, story_data["characters"])
        scene_assets = _insert_scenes(project_id, story_data["scenes"])
        _insert_shots(project_id, payload, story_data, character_assets, scene_assets)

        update_project_status(project_id, "ready_for_review")
        update_job(job_id, "completed", 100, "Storyboard package ready for review")
    except Exception as exc:  # pragma: no cover - logged for local workflow resilience
        update_project_status(project_id, "failed")
        update_job(job_id, "failed", 100, "Workflow failed", str(exc))


def _build_story_data(payload: ProjectCreate) -> dict:
    text = payload.source_text.strip()
    compact = " ".join(text.replace("\n", " ").split())
    seed = compact[:180] if compact else payload.title
    tone = payload.style
    return {
        "summary": f"《{payload.title}》被改编为一组具备连续叙事的影视镜头，核心冲突围绕人物选择、环境压力与情绪转折展开。",
        "worldview": f"故事发生在由原文气质驱动的影像世界中。视觉风格采用 {tone}，重点保留文本中的情绪线索：{seed}",
        "style_tags": [tone, "cinematic", "controlled composition", "consistent character design"],
        "themes": ["选择", "记忆", "关系", "命运"],
        "characters": [
            {
                "name": "主角",
                "role": "protagonist",
                "description": "推动故事行动的人物，外表克制，情绪在细节中逐渐显露。",
                "visual_prompt": f"main character, restrained expression, {tone}, consistent costume",
            },
            {
                "name": "关键人物",
                "role": "supporting",
                "description": "影响主角决定的人物，承担叙事转折与情绪推动功能。",
                "visual_prompt": f"supporting character, memorable silhouette, {tone}",
            },
        ],
        "scenes": [
            {
                "name": "开场空间",
                "description": "承载故事开端的主要空间，强调氛围建立和人物孤立感。",
                "visual_prompt": f"establishing environment, cinematic lighting, {tone}",
            },
            {
                "name": "转折空间",
                "description": "剧情发生变化的空间，适合使用光影反差和运动镜头。",
                "visual_prompt": f"turning point location, dramatic contrast, {tone}",
            },
        ],
        "shots": [],
    }


def _normalize_live_story_data(plan: dict, payload: ProjectCreate) -> dict:
    fallback = _build_story_data(payload)
    return {
        "summary": plan.get("summary") or fallback["summary"],
        "worldview": plan.get("worldview") or fallback["worldview"],
        "style_tags": plan.get("style_tags") or fallback["style_tags"],
        "themes": plan.get("themes") or fallback["themes"],
        "characters": plan.get("characters") or fallback["characters"],
        "scenes": plan.get("scenes") or fallback["scenes"],
        "shots": plan.get("shots") or [],
    }


def _insert_characters(project_id: str, characters: list[dict]) -> dict[str, str]:
    result = {}
    accents = ["#2563eb", "#14b8a6"]
    with connect() as conn:
        conn.execute("DELETE FROM characters WHERE project_id = ?", (project_id,))
    for index, character in enumerate(characters):
        asset_id = generate_image_asset(
            ImageAssetRequest(
                project_id=project_id,
                asset_type="character",
                name=character["name"],
                description=character["description"],
                prompt=character["visual_prompt"],
                accent=accents[index % len(accents)],
            )
        )
        char_id = f"char_{uuid.uuid4().hex[:10]}"
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO characters
                (id, project_id, name, role, description, visual_prompt, asset_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    char_id,
                    project_id,
                    character["name"],
                    character["role"],
                    character["description"],
                    character["visual_prompt"],
                    asset_id,
                    utc_now(),
                ),
            )
        result[character["name"]] = asset_id
    return result


def _insert_scenes(project_id: str, scenes: list[dict]) -> dict[str, str]:
    result = {}
    accents = ["#7c3aed", "#f59e0b"]
    with connect() as conn:
        conn.execute("DELETE FROM scenes WHERE project_id = ?", (project_id,))
    for index, scene in enumerate(scenes):
        asset_id = generate_image_asset(
            ImageAssetRequest(
                project_id=project_id,
                asset_type="scene",
                name=scene["name"],
                description=scene["description"],
                prompt=scene["visual_prompt"],
                accent=accents[index % len(accents)],
            )
        )
        scene_id = f"scene_{uuid.uuid4().hex[:10]}"
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO scenes
                (id, project_id, name, description, visual_prompt, asset_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scene_id,
                    project_id,
                    scene["name"],
                    scene["description"],
                    scene["visual_prompt"],
                    asset_id,
                    utc_now(),
                ),
            )
        result[scene["name"]] = asset_id
    return result


def _insert_shots(
    project_id: str,
    payload: ProjectCreate,
    story_data: dict,
    character_assets: dict[str, str],
    scene_assets: dict[str, str],
    evidence_by_index: dict[int, list[dict]] | None = None,
) -> None:
    shot_count = compute_shot_count(payload)
    motions = ["slow push in", "locked-off wide shot", "handheld follow", "over-shoulder reveal", "lateral tracking", "high-angle pause"]
    now = utc_now()
    previous_last_path = ""
    with connect() as conn:
        conn.execute("DELETE FROM shots WHERE project_id = ?", (project_id,))
    for index in range(shot_count):
        shot_id = f"shot_{uuid.uuid4().hex[:10]}"
        version_id = f"version_{uuid.uuid4().hex[:10]}"
        scene = story_data["scenes"][index % len(story_data["scenes"])]["name"]
        character_names = [item["name"] for item in story_data["characters"][: 1 + (index % 2)]]
        live_shot = story_data.get("shots", [])[index] if index < len(story_data.get("shots", [])) else {}
        title = live_shot.get("title") or f"镜头 {index + 1}"
        description = live_shot.get("description") or _shot_description(payload.title, index, shot_count)
        character_names = live_shot.get("characters") or character_names
        scene = live_shot.get("scene") or scene
        camera_motion = live_shot.get("camera_motion") or motions[index % len(motions)]
        visual_prompt = live_shot.get("visual_prompt") or (
            f"{payload.style}, {description}, scene: {scene}, "
            f"characters: {', '.join(character_names)}, aspect ratio {payload.aspect_ratio}, "
            "consistent visual anchors"
        )
        rag_evidence = (evidence_by_index or {}).get(index + 1, [])
        if rag_evidence:
            evidence_prompt = " ".join(f"{item['label']}: {item['excerpt']}" for item in rag_evidence)
            visual_prompt = f"{visual_prompt}. RAG story evidence: {evidence_prompt}"
        negative_prompt = live_shot.get("negative_prompt") or "low quality, inconsistent face, broken hands, unreadable composition"
        audio_prompt = live_shot.get("audio_prompt") or "subtle ambience, controlled emotional rhythm, cinematic sound bed"
        if previous_last_path:
            first_asset = create_linked_asset(
                project_id,
                "first-frame",
                f"Shot {index + 1} First Frame",
                f"Continuity frame inherited from Shot {index} last frame. {description}",
                visual_prompt,
                previous_last_path,
                f"continuity:strict:from-shot-{index}",
            )
        else:
            first_asset = generate_image_asset(
                ImageAssetRequest(
                    project_id=project_id,
                    asset_type="first-frame",
                    name=f"Shot {index + 1} First Frame",
                    description=description,
                    prompt=visual_prompt,
                    accent="#2563eb",
                )
            )
        last_asset = generate_image_asset(
            ImageAssetRequest(
                project_id=project_id,
                asset_type="last-frame",
                name=f"Shot {index + 1} Last Frame",
                description=f"Emotional continuation of: {description}",
                prompt=visual_prompt,
                accent="#14b8a6",
            )
        )
        with connect() as conn:
            first_path = _asset_path(conn, first_asset)
            last_path = _asset_path(conn, last_asset)
            previous_last_path = last_path
            conn.execute(
                """
                INSERT INTO shots
                (id, project_id, shot_index, title, description, characters, scene, camera_motion,
                 visual_prompt, negative_prompt, audio_prompt, rag_evidence, status, retry_count, current_version_id,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    shot_id,
                    project_id,
                    index + 1,
                    title,
                    description,
                    to_json(character_names),
                    scene,
                    camera_motion,
                    visual_prompt,
                    negative_prompt,
                    audio_prompt,
                    to_json(rag_evidence),
                    "keyframes_ready",
                    0,
                    version_id,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO shot_versions
                (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
                 first_frame_path, last_frame_path, video_path, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    shot_id,
                    1,
                    description,
                    visual_prompt,
                    negative_prompt,
                    audio_prompt,
                    first_path,
                    last_path,
                    None,
                    "mock_workflow",
                    now,
                ),
            )


def _asset_path(conn, asset_id: str) -> str:
    row = conn.execute("SELECT file_path FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return row["file_path"] if row else ""


def _shot_description(title: str, index: int, total: int) -> str:
    beats = [
        "主角被置于故事开端的环境中，画面建立时间、空间和情绪基调。",
        "关键人物或线索进入画面，人物关系开始产生压力。",
        "主角意识到局面发生变化，镜头强调细节、眼神和动作停顿。",
        "冲突被推向更明确的位置，空间压迫感和叙事悬念增强。",
        "人物做出选择，画面从外部动作转向内部情绪。",
        "片段以余韵收束，为后续章节留下视觉和情绪钩子。",
    ]
    if total <= len(beats):
        return beats[index]
    return f"《{title}》第 {index + 1} 个叙事节点，保持人物一致性并推进故事节奏。"
