"""Deterministic medium-text segmentation, events, and storylines. No live LLM."""
from __future__ import annotations

import re

from .adaptation_planner import CONFLICT_RE, analyze_source

TARGET_CHARS = 900
OVERLAP_CHARS = 120
MIN_CHARS = 600
MAX_CHARS = 1200
SHORT_LIMIT = 1500
MEDIUM_LIMIT = 10000
LONG_LIMIT = 100000

# 无章节标记时的强制分章长度。取 10,000 是有原因的：它正好是 P5-A 的上限，
# 也就是"一段文本只要还在 P5-A 的射程内，就不需要被切开"。
CHAPTER_FALLBACK_CHARS = 10000

# 语料里的真实形态是「第一节：纵身亡魔心仍不悔」，行首可能有一个半角空格，
# 文件开头则顶着 BOM。数字用中文数字。标题只取到行尾。
CHAPTER_MARKER_RE = re.compile(
    r"(?m)^[\ufeff\u3000 \t]{0,4}第([一二三四五六七八九十百千零〇0-9]{1,6})节[：:][ \t\u3000]*(.{0,40})"
)


def text_scale(source_text: str) -> str:
    length = len(source_text or "")
    if length <= SHORT_LIMIT:
        return "short"
    if length <= MEDIUM_LIMIT:
        return "medium"
    if length <= LONG_LIMIT:
        return "long"
    return "over_limit"


def scale_label(scale: str) -> str:
    return {
        "short": "短文本：直接改编",
        "medium": "中等文本：先选择故事线，再进行改编",
        "long": "长文本：按章节选择改编范围",
        "over_limit": f"超出上限：最多支持 {LONG_LIMIT:,} 字",
    }.get(scale, "未知规模")


def parse_chapters(source_text: str) -> list[dict]:
    """把原文切成章节树。

    顺序即语义：第 N 章的 ``end_offset`` 就是第 N+1 章的 ``start_offset``，
    中间不允许有缝。理由很直接——"按章节选择改编范围"这个承诺全靠它兑现，
    一旦有个字符同时属于两章（或谁都不属于），用户在界面上勾选的结果
    就没法回溯到确定的一段原文。
    """
    text = source_text or ""
    if not text:
        return []
    marks = list(CHAPTER_MARKER_RE.finditer(text))
    if len(marks) < 2:
        return _fallback_chapters(text)
    bounds = [0] + [mark.start() for mark in marks[1:]] + [len(text)]
    chapters = []
    for index, mark in enumerate(marks, start=1):
        start, end = bounds[index - 1], bounds[index]
        body = text[start:end]
        analysis = analyze_source("", body)
        chapters.append(
            {
                "chapter_index": index,
                "marker": f"第{mark.group(1)}节",
                "title": mark.group(2).strip(),
                "start_offset": start,
                "end_offset": end,
                "char_count": end - start,
                "text": body,
                "summary": (analysis["conflict_line"] or body[:80]).strip(),
                "characters": analysis["names"][:8],
                "places": analysis["places"][:8],
                "source": "chapter_marker",
            }
        )
    return chapters


def _fallback_chapters(text: str) -> list[dict]:
    """没有章节标记时，按固定长度硬切，并吸附到句子边界。"""
    chapters = []
    n = len(text)
    cursor = 0
    index = 1
    while cursor < n:
        stop = min(n, cursor + CHAPTER_FALLBACK_CHARS)
        if stop < n:
            snapped = _forward_boundary(text, stop, min(n, stop + MIN_CHARS))
            if snapped > cursor:
                stop = snapped
        body = text[cursor:stop]
        analysis = analyze_source("", body)
        chapters.append(
            {
                "chapter_index": index,
                "marker": f"第{index}段",
                "title": body[:24].strip() or f"第{index}段",
                "start_offset": cursor,
                "end_offset": stop,
                "char_count": stop - cursor,
                "text": body,
                "summary": (analysis["conflict_line"] or body[:80]).strip(),
                "characters": analysis["names"][:8],
                "places": analysis["places"][:8],
                "source": "length_fallback",
            }
        )
        cursor = stop
        index += 1
    return chapters


def segment_source(source_text: str, *, chapters: list[dict] | None = None) -> list[dict]:
    """分块。

    给了 ``chapters`` 时**逐章独立分块**：块的边界不可能越过章节边界，
    因为每一章都是在自己的区间里从头切起的。这比"切完再判断归属"更可靠——
    后者需要额外的裁剪逻辑，而裁剪本身就可能再引入缝隙。
    """
    text = source_text or ""
    if not text:
        return []
    if not chapters:
        return _segment_range(text, 0, len(text), chapter_index=None)
    chunks: list[dict] = []
    for chapter in chapters:
        chunks.extend(
            _segment_range(
                text,
                int(chapter["start_offset"]),
                int(chapter["end_offset"]),
                chapter_index=int(chapter["chapter_index"]),
            )
        )
    for index, chunk in enumerate(chunks, start=1):
        chunk["chunk_index"] = index
    return chunks


def _segment_range(text: str, start: int, end: int, *, chapter_index: int | None) -> list[dict]:
    n = end
    chunks: list[dict] = []
    index = 1
    cursor = start
    while cursor < n:
        stop = min(n, cursor + TARGET_CHARS)
        if stop < n:
            snapped = _forward_boundary(text, stop, min(n, cursor + MAX_CHARS))
            if snapped > cursor:
                stop = snapped
        if stop - cursor < MIN_CHARS and stop < n:
            stop = _forward_boundary(text, min(n, cursor + MIN_CHARS), min(n, cursor + MAX_CHARS)) or min(n, cursor + MAX_CHARS)
        body = text[cursor:stop]
        analysis = analyze_source("", body)
        chunks.append(
            {
                "chunk_index": index,
                "chapter_index": chapter_index,
                "text": body,
                "start_offset": cursor,
                "end_offset": stop,
                "char_count": stop - cursor,
                "summary": (analysis["conflict_line"] or body[:80]).strip(),
                "characters": analysis["names"],
                "places": analysis["places"],
                "conflict_terms": sorted(set(CONFLICT_RE.findall(body))),
                "source": "mock_segmenter",
            }
        )
        if stop >= n:
            break
        nxt = max(cursor + 1, stop - OVERLAP_CHARS)
        nxt = _backward_boundary(text, nxt, cursor + 1)
        if nxt <= cursor:
            nxt = stop
        cursor = nxt
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
