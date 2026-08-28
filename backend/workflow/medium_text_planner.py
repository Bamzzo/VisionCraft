"""Deterministic medium-text segmentation, events, and storylines. No live LLM."""
from __future__ import annotations

from .adaptation_planner import CONFLICT_RE, analyze_source

TARGET_CHARS = 900
OVERLAP_CHARS = 120
MIN_CHARS = 600
MAX_CHARS = 1200
SHORT_LIMIT = 1500
MEDIUM_LIMIT = 10000


def text_scale(source_text: str) -> str:
    length = len(source_text or "")
    if length <= SHORT_LIMIT:
        return "short"
    if length <= MEDIUM_LIMIT:
        return "medium"
    return "long"


def scale_label(scale: str) -> str:
    return {
        "short": "短文本：直接改编",
        "medium": "中等文本：先选择故事线，再进行改编",
        "long": "长文本：章节检索尚未实现（P5-B）",
    }.get(scale, "未知规模")


def segment_source(source_text: str) -> list[dict]:
    text = source_text or ""
    n = len(text)
    if n == 0:
        return []
    chunks = []
    start = 0
    index = 1
    while start < n:
        end = min(n, start + TARGET_CHARS)
        if end < n:
            snapped = _forward_boundary(text, end, min(n, start + MAX_CHARS))
            if snapped > start:
                end = snapped
        if end - start < MIN_CHARS and end < n:
            end = _forward_boundary(text, min(n, start + MIN_CHARS), min(n, start + MAX_CHARS)) or min(n, start + MAX_CHARS)
        body = text[start:end]
        analysis = analyze_source("", body)
        chunks.append(
            {
                "chunk_index": index,
                "text": body,
                "start_offset": start,
                "end_offset": end,
                "char_count": end - start,
                "summary": (analysis["conflict_line"] or body[:80]).strip(),
                "characters": analysis["names"],
                "places": analysis["places"],
                "conflict_terms": sorted(set(CONFLICT_RE.findall(body))),
                "source": "mock_segmenter",
            }
        )
        if end >= n:
            break
        nxt = max(start + 1, end - OVERLAP_CHARS)
        nxt = _backward_boundary(text, nxt, start + 1)
        if nxt <= start:
            nxt = end
        start = nxt
        index += 1
    return chunks


def extract_events(source_text: str, chunks: list[dict]) -> list[dict]:
    events = []
    for chunk in chunks:
        analysis = analyze_source("", chunk["text"])
        excerpts = analysis["excerpts"] or [{"text": chunk["text"][:120], "start": 0, "end": min(120, len(chunk["text"]))}]
        conflict = analysis["conflict_excerpt"]
        hero = analysis["names"][0] if analysis["names"] else "主角"
        place = analysis["places"][0] if analysis["places"] else "未标明地点"
        local_start = conflict["start"] if conflict else excerpts[0]["start"]
        local_end = conflict["end"] if conflict else excerpts[0]["end"]
        abs_start = chunk["start_offset"] + local_start
        abs_end = chunk["start_offset"] + local_end
        quote = source_text[abs_start:abs_end] or conflict.get("text") or excerpts[0]["text"]
        events.append(
            {
                "event_index": len(events) + 1,
                "title": f"片段{chunk['chunk_index']}：{hero}在{place}",
                "summary": (chunk.get("summary") or quote)[:160],
                "characters": analysis["names"][:4],
                "places": analysis["places"][:4],
                "goal": f"{hero}推进当前段落中的行动",
                "conflict": analysis["conflict_line"] or quote[:80],
                "outcome": excerpts[-1]["text"][:80] if excerpts else "",
                "chunk_indexes": [chunk["chunk_index"]],
                "source_excerpt": quote[:180],
                "source_start": abs_start,
                "source_end": abs_end,
                "importance": 0.55 + (0.08 if CONFLICT_RE.search(quote or "") else 0) + min(0.2, chunk["chunk_index"] / 50),
                "source": "mock_event_extractor",
            }
        )
    return events


def plan_storylines(title: str, source_text: str, chunks: list[dict], events: list[dict]) -> list[dict]:
    if not events:
        return []
    names = []
    for event in events:
        for name in event.get("characters") or []:
            if name not in names:
                names.append(name)
    hero = names[0] if names else "主角"
    conflict_events = [item for item in events if CONFLICT_RE.search(item.get("conflict") or item.get("source_excerpt") or "")]
    if len(conflict_events) < 2:
        conflict_events = events[:: max(1, len(events) // 3)] or events[:2]
    goal_events = events[: max(2, int(len(events) * 0.6))]
    reveal_events = events[max(0, len(events) - max(2, int(len(events) * 0.45))) :]
    templates = [
        {
            "title": f"主角目标线：{hero}要完成的事",
            "rationale": "按时间顺序抓住人物要做成的事，适合 45 秒把动机说清楚。",
            "events": goal_events,
            "pace": 45,
            "turning": goal_events[len(goal_events) // 2]["summary"] if goal_events else "",
            "ending": goal_events[-1]["outcome"] if goal_events else "",
            "conflict": goal_events[0]["conflict"] if goal_events else "",
            "goal": f"{hero}沿着前半段事件把目标推进到底。",
        },
        {
            "title": f"冲突升级线：压力如何压到{hero}",
            "rationale": "只保留带转折/冲突词的事件，适合 30 秒对峙。",
            "events": conflict_events[: max(2, len(conflict_events))],
            "pace": 30,
            "turning": (conflict_events[len(conflict_events) // 2]["summary"] if conflict_events else ""),
            "ending": "对峙未完，留下下一拍。",
            "conflict": conflict_events[0]["conflict"] if conflict_events else "",
            "goal": f"{hero}必须立刻回应不断升级的阻力。",
        },
        {
            "title": f"悬念揭示线：倒推《{title or '故事'}》的落点",
            "rationale": "用后段事件倒推短片结尾，适合 60 秒留下余味。",
            "events": reveal_events,
            "pace": 60,
            "turning": reveal_events[0]["summary"] if reveal_events else "",
            "ending": reveal_events[-1]["outcome"] if reveal_events else "",
            "conflict": reveal_events[0]["conflict"] if reveal_events else "",
            "goal": f"{hero}在时间耗尽前看清最后的结果。",
        },
    ]
    unique = []
    seen = set()
    for offset, item in enumerate(templates):
        chosen = item["events"] or events[:2]
        key = tuple(event["event_index"] for event in chosen)
        if not key:
            continue
        if key in seen and len(events) >= 3:
            chosen = events[offset % 2 :: 2] or events[-2:]
            key = tuple(event["event_index"] for event in chosen)
        if key in seen:
            continue
        seen.add(key)
        unique.append({**item, "events": chosen})
    if len(unique) < 2 and len(events) >= 2:
        unique = [{**templates[0], "events": events[: max(2, len(events) // 2)]}, {**templates[1], "events": events[len(events) // 2 :]}]
    lines = []
    for index, item in enumerate(unique[:3], start=1):
        chosen = item["events"]
        excerpt = chosen[0]["source_excerpt"]
        chunk_indexes = sorted({idx for event in chosen for idx in event.get("chunk_indexes") or []})
        lines.append(
            {
                "storyline_index": index,
                "title": item["title"],
                "rationale": item["rationale"],
                "protagonist": hero,
                "protagonist_goal": item["goal"],
                "conflict": item["conflict"],
                "turning_point": item["turning"],
                "ending_orientation": item["ending"],
                "event_indexes": [event["event_index"] for event in chosen],
                "chunk_indexes": chunk_indexes,
                "source_excerpt": excerpt,
                "suggested_duration_seconds": item["pace"],
                "suggested_shot_count": 4 if item["pace"] == 30 else 5 if item["pace"] == 45 else 6,
                "source": "mock_storyline_planner",
            }
        )
    return lines


def coverage_ok(source_text: str, chunks: list[dict]) -> bool:
    covered = [False] * len(source_text or "")
    for chunk in chunks:
        for index in range(int(chunk["start_offset"]), int(chunk["end_offset"])):
            if 0 <= index < len(covered):
                covered[index] = True
    return bool(covered) and all(covered)


def _forward_boundary(text: str, index: int, limit: int) -> int:
    for pos in range(index, limit):
        if text[pos] in "。！？!?\n":
            return pos + 1
    return limit


def _backward_boundary(text: str, index: int, floor: int) -> int:
    for pos in range(index, floor - 1, -1):
        if pos <= floor:
            return floor
        if text[pos - 1] in "。！？!?\n":
            return pos
    return floor
