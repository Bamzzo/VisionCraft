"""Deterministic short-text adaptation planner. No live LLM calls."""
from __future__ import annotations

import re

SENTENCE_RE = re.compile(r"[^。！？!?\n]+[。！？!?\n]?")
NAME_RE = re.compile(r"[\u4e00-\u9fff]{2,4}(?=说|道|问|看|走|想|笑|冷|怒|叹|低声)")
PLACE_RE = re.compile(r"(?:在|于)([\u4e00-\u9fff]{2,8}?(?:山|谷|城|镇|村|殿|府|屋|房|洞|林|河|湖|街|巷|阁|堂))")
CONFLICT_RE = re.compile(r"却|但是|然而|冲突|仇|恨|杀|逃|战|死|危机|选择|背叛|争夺")


def plan_adaptations(title: str, source_text: str, style: str = "", duration_seconds: int = 5) -> list[dict]:
    analysis = analyze_source(title, source_text)
    excerpts = analysis["excerpts"]
    names = analysis["names"]
    hero = names[0] if names else "主角"
    conflict_line = analysis["conflict_line"]
    last_line = excerpts[-1]["text"] if excerpts else source_text[:80]
    mid = excerpts[len(excerpts) // 2] if excerpts else excerpts[0]
    templates = [
        {
            "title": f"冲突优先：{hero}正面迎上",
            "rationale": "抓住原文里转折最强的一句，把短片压在对峙与行动上，适合 30 秒内建立冲突。",
            "protagonist_goal": f"{hero}必须立刻回应眼前的压力，不能再回避。",
            "conflict": conflict_line,
            "ending_orientation": "以对峙未完作为悬念，留下下一拍动作空间。",
            "excerpt": analysis["conflict_excerpt"],
        },
        {
            "title": f"人物抉择：{hero}的内心转向",
            "rationale": "以人物动机为主轴，用中段依据呈现犹豫与决定，适合强调情绪曲线。",
            "protagonist_goal": f"{hero}想守住对自己重要的东西，却必须付出代价。",
            "conflict": f"{hero}的目标与现实阻碍发生错位。",
            "ending_orientation": "以一次明确选择收束，不解释全部后果。",
            "excerpt": mid,
        },
        {
            "title": f"悬念收束：从结尾倒推{title or '故事'}",
            "rationale": "用原文末句作为短片落点，倒推开场与中段，适合留下余味。",
            "protagonist_goal": f"{hero}要在时间耗尽前看清真相或完成最后一步。",
            "conflict": last_line[:80] or conflict_line,
            "ending_orientation": "停在未解的画面或一句未说完的话上。",
            "excerpt": excerpts[-1] if excerpts else analysis["conflict_excerpt"],
        },
    ]
    shot_count = 4 if len(source_text) < 600 else 5 if len(source_text) < 1200 else 6
    shot_count = max(4, min(8, shot_count))
    options = []
    for index, item in enumerate(templates[: 3 if len(source_text) >= 40 else 2], start=1):
        excerpt = item["excerpt"]
        options.append(
            {
                "option_index": index,
                "title": item["title"],
                "rationale": item["rationale"],
                "protagonist_goal": item["protagonist_goal"],
                "conflict": item["conflict"],
                "ending_orientation": item["ending_orientation"],
                "suggested_duration_seconds": min(60, max(30, shot_count * duration_seconds)),
                "suggested_shot_count": shot_count,
                "source_excerpt": excerpt["text"],
                "source_start": excerpt["start"],
                "source_end": excerpt["end"],
                "source": "mock_planner",
            }
        )
    return options


def plan_story_bible(title: str, source_text: str, style: str, option: dict) -> dict:
    analysis = analyze_source(title, source_text)
    hero = analysis["names"][0] if analysis["names"] else "主角"
    support = analysis["names"][1] if len(analysis["names"]) > 1 else "关键人物"
    place = analysis["places"][0] if analysis["places"] else "主要场景"
    place_b = analysis["places"][1] if len(analysis["places"]) > 1 else "转折空间"
    excerpt = option.get("source_excerpt") or analysis["conflict_excerpt"]["text"]
    characters = [
        {
            "name": hero,
            "role": "主角",
            "identity": "推动短片行动的核心人物",
            "appearance": f"与原文气质一致，{style or '电影感写实'}，服装与神态保持连续",
            "motivation": option.get("protagonist_goal") or f"{hero}必须完成一次无法回头的行动",
            "invariant": f"{hero}的身份关系与核心外形特征不得改写",
        },
        {
            "name": support,
            "role": "对手/见证者",
            "identity": f"与{hero}形成阻力或对照的人物",
            "appearance": "轮廓可辨、不抢主角服装色彩",
            "motivation": "逼出主角的选择，或成为选择的代价",
            "invariant": "与主角的关系方向不得在后续镜头中无故翻转",
        },
    ]
    scenes = [
        {
            "name": place,
            "environment": "开场空间，建立人物孤立与压力",
            "time": "故事起始时刻",
            "visuals": f"{style or '电影感写实'}，稳定光源，空间可辨",
            "invariant": f"{place}的空间结构与时间设定不得跳切丢失",
        },
        {
            "name": place_b,
            "environment": "冲突或抉择发生的转折空间",
            "time": "短片中后段",
            "visuals": "光比加强，运动更明显",
            "invariant": "转折空间的天气与时段须与开场可对得上",
        },
    ]
    logline = f"{hero}在{place}面对「{option.get('conflict', '')[:24]}」，必须做出无法收回的决定。"
    summary = (
        f"短片改编《{title}》，采用方案「{option.get('title')}」。"
        f"主角目标：{option.get('protagonist_goal')}。"
        f"依据原文：「{excerpt[:60]}」。"
    )
    return {
        "logline": logline,
        "adaptation_summary": summary,
        "summary": summary,
        "worldview": f"影像世界服从原文气质与方案取向：{option.get('ending_orientation')}。视觉风格：{style or '电影感写实'}。",
        "emotion_curve": "压抑建立 → 冲突加压 → 抉择瞬间 → 余味停顿",
        "themes": ["选择", "压力", "关系"],
        "style_tags": [style or "cinematic", "短片节奏", "人物连续"],
        "protagonist": hero,
        "protagonist_goal": option.get("protagonist_goal") or "",
        "obstacle": option.get("conflict") or "",
        "character_cards": characters,
        "scene_cards": scenes,
        "visual_style": style or "cinematic clean realism",
        "consistency_constraints": "同一角色服装、发色、年龄段与关键道具在全部镜头保持一致；场景空间方向不无故翻转。",
        "option_id": option.get("id"),
        "source": "mock_planner",
        "review_status": "draft",
    }


def plan_storyboard(
    title: str,
    source_text: str,
    style: str,
    option: dict,
    bible: dict,
    shot_count: int | None = None,
) -> list[dict]:
    analysis = analyze_source(title, source_text)
    excerpts = analysis["excerpts"] or [analysis["conflict_excerpt"]]
    if shot_count is None:
        count = int(option.get("suggested_shot_count") or 4)
        count = max(4, min(8, count))
    else:
        count = max(1, min(12, int(shot_count)))
    characters = bible.get("character_cards") or []
    scenes = bible.get("scene_cards") or []
    hero = (characters[0] or {}).get("name") if characters else "主角"
    support = (characters[1] or {}).get("name") if len(characters) > 1 else hero
    place_a = (scenes[0] or {}).get("name") if scenes else "开场空间"
    place_b = (scenes[1] or {}).get("name") if len(scenes) > 1 else place_a
    motions = ["缓慢推进", "固定全景", "手持跟随", "过肩揭示", "横向移动", "高角度停顿"]
    purposes = ["建立人物与空间", "显露压力来源", "升级冲突", "做出选择", "承受代价", "留下余味"]
    shots = []
    for index in range(count):
        excerpt = excerpts[index % len(excerpts)]
        at_turn = index >= count // 2
        scene = place_b if at_turn else place_a
        people = [hero] if index == 0 else [hero, support]
        action = _shot_action(index, count, hero, option)
        purpose = purposes[min(index, len(purposes) - 1)]
        shots.append(
            {
                "shot_index": index + 1,
                "title": f"镜头 {index + 1}：{purpose}",
                "narrative_purpose": purpose,
                "characters": people,
                "scene": scene,
                "action_text": action,
                "camera_motion": motions[index % len(motions)],
                "duration_seconds": 5,
                "visual_prompt": (
                    f"{style or 'cinematic'}, {action}, scene {scene}, characters {', '.join(people)}, "
                    f"consistent costume, {motions[index % len(motions)]}"
                ),
                "bible_character": hero,
                "bible_scene": scene,
                "source_excerpt": excerpt["text"],
                "source_start": excerpt["start"],
                "source_end": excerpt["end"],
                "source_type": "auto_draft",
                "review_status": "draft",
            }
        )
    return shots


def analyze_source(title: str, source_text: str) -> dict:
    text = (source_text or "").strip() or title or "未命名故事"
    excerpts = []
    for match in SENTENCE_RE.finditer(text):
        piece = match.group(0).strip()
        if len(piece) < 2:
            continue
        excerpts.append({"text": piece[:180], "start": match.start(), "end": match.end()})
    if not excerpts:
        excerpts = [{"text": text[:180], "start": 0, "end": min(180, len(text))}]
    names = []
    for name in NAME_RE.findall(text):
        if name not in names and name not in {"我们", "他们", "自己", "什么"}:
            names.append(name)
    if not names and title:
        names = [title[:4]]
    places = []
    for match in PLACE_RE.findall(text):
        if match not in places:
            places.append(match)
    conflict_excerpt = excerpts[0]
    for item in excerpts:
        if CONFLICT_RE.search(item["text"]):
            conflict_excerpt = item
            break
    return {
        "excerpts": excerpts,
        "names": names[:4],
        "places": places[:4],
        "conflict_excerpt": conflict_excerpt,
        "conflict_line": conflict_excerpt["text"][:80],
    }


def _shot_action(index: int, count: int, hero: str, option: dict) -> str:
    if index == 0:
        return f"{hero}进入场景，气氛尚未点破。"
    if index == count - 1:
        return f"短片停在方案取向：{option.get('ending_orientation')}"
    if index == count // 2:
        return f"{hero}直面冲突：{str(option.get('conflict') or '')[:40]}"
    return f"{hero}继续推进目标：{str(option.get('protagonist_goal') or '')[:40]}"
