from ..services.project_service import get_project


def build_markdown(project_id: str) -> str:
    project = get_project(project_id)
    if not project:
        return ""
    lines = [
        f"# {project['title']}",
        "",
        f"- Status: {project['status']}",
        f"- Style: {project['style']}",
        f"- Aspect Ratio: {project['aspect_ratio']}",
        "",
        "## Story Bible",
        "",
    ]
    bible = project.get("story_bible") or {}
    lines.extend(
        [
            f"Summary: {bible.get('summary', '')}",
            "",
            f"Worldview: {bible.get('worldview', '')}",
            "",
            "## Shots",
            "",
        ]
    )
    for shot in project.get("shots", []):
        lines.extend(
            [
                f"### Shot {shot['shot_index']}: {shot['title']}",
                "",
                f"- Description: {shot['description']}",
                f"- Characters: {', '.join(shot.get('characters', []))}",
                f"- Scene: {shot['scene']}",
                f"- Camera: {shot['camera_motion']}",
                f"- Visual Prompt: {shot['visual_prompt']}",
                f"- Negative Prompt: {shot['negative_prompt']}",
                f"- Audio Prompt: {shot['audio_prompt']}",
                f"- Current Version: {shot.get('current_version_id', 'N/A')}",
            ]
        )
        if shot.get("rag_evidence"):
            lines.append("- RAG Evidence:")
            for item in shot["rag_evidence"]:
                lines.append(f"  - {item.get('label', item.get('kind', 'memory'))}: {item.get('excerpt', '')}")
        if shot.get("versions"):
            lines.append("- Versions:")
            for version in shot["versions"]:
                media = "video" if version.get("video_path") else "keyframes"
                lines.append(f"  - v{version['version_number']} ({media}, {version['created_by']})")
        lines.append("")
    return "\n".join(lines)
