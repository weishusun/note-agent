"""把一条笔记的正文并进另一条（SCHEMA 2.6「合并」）。纯函数，不读写 vault。

正文按 `## ` 区块拆开，同名区块逐个合并，区块的去留和顺序由程序决定——所以「模型补充」里的内容
不可能被并进「要点」（不变量 14）。只有「要点」和「模型补充」两个列表会交给文本模型做语义去重，
那一步失败时退回逐字去重的简单拼接。
"""

from collections.abc import Callable, Collection

from app.ingest.assemble import SUPPLEMENT_HEADING, SUPPLEMENT_MARKER

_EMPTY = "（暂无）"
_POINTS = "## 要点"
_SOURCE = "## 原始输入"
# 这两个区块里是指向别的笔记的链接列表
_LINK_SECTIONS = ("## 关联", "## 概念")

Section = tuple[str | None, list[str]]
# (概要A, 概要B, 要点, 模型补充) -> (概要, 要点, 模型补充)；失败时抛异常
Tidy = Callable[[str, str, list[str], list[str]], tuple[str, list[str], list[str]]]


def parse_sections(body: str) -> list[Section]:
    """按二级标题拆成 [(标题行或 None, 该区块的行)]。第一个标题之前的内容归在 None 下。"""
    sections: list[Section] = [(None, [])]
    for line in body.replace("\r\n", "\n").strip("\n").split("\n"):
        if line.startswith("## "):
            sections.append((line.strip(), []))
        else:
            sections[-1][1].append(line.rstrip())
    return [(heading, _trim(lines)) for heading, lines in sections if heading or _trim(lines)]


def _trim(lines: list[str]) -> list[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _union(first: list[str], second: list[str]) -> list[str]:
    """first 原样保留，second 里没出现过的非空行接在后面。"""
    seen = {line.strip() for line in first if line.strip()}
    merged = list(first)
    for line in second:
        if line.strip() and line.strip() not in seen:
            seen.add(line.strip())
            merged.append(line)
    return merged


def _bullets(lines: list[str]) -> list[str]:
    return [line.strip()[2:].strip() for line in lines if line.strip().startswith("- ")]


def merge_bodies(
    kept: str,
    merged: str,
    *,
    kept_summary: str,
    merged_summary: str,
    drop_links_to: tuple[str, ...] = (),
    tidy: Tidy | None = None,
) -> tuple[str, str]:
    """把 merged 的正文并进 kept 的正文，返回 (新正文, 新概要)。

    drop_links_to：这两条笔记自己的文件名。合并后的笔记不该在「关联」里链接到自己或已经消失的那条。
    tidy 给了就用它对要点和模型补充做语义去重、并重写概要；它抛异常时保留简单拼接的结果。
    """
    a, b = parse_sections(kept), parse_sections(merged)
    b_by_heading = {heading: lines for heading, lines in b}
    result: list[Section] = []
    for heading, lines in a:
        extra = b_by_heading.pop(heading, [])
        result.append((heading, _merge_section(heading, lines, extra, drop_links_to)))
    # kept 里没有的区块接在后面，但「原始输入」始终留在最后
    tail = [section for section in result if section[0] == _SOURCE]
    result = [section for section in result if section[0] != _SOURCE]
    for heading, lines in b:
        if heading in b_by_heading:
            target = tail if heading == _SOURCE else result
            target.append((heading, _merge_section(heading, [], lines, drop_links_to)))
    result += tail

    summary = kept_summary
    if tidy is not None:
        index = {heading: i for i, (heading, _lines) in enumerate(result)}
        points = _bullets(result[index[_POINTS]][1]) if _POINTS in index else []
        supplement = (
            _bullets(result[index[SUPPLEMENT_HEADING]][1]) if SUPPLEMENT_HEADING in index else []
        )
        # 只有两边都贡献了要点、且区块里全是列表项时才值得交给模型整理
        if points and _is_plain_list(result, index, _POINTS) and _points_from_both(a, b):
            try:
                summary, points, supplement_out = tidy(
                    kept_summary, merged_summary, points, supplement
                )
                result[index[_POINTS]] = (_POINTS, [f"- {p}" for p in points])
                if SUPPLEMENT_HEADING in index and _is_plain_list(
                    result, index, SUPPLEMENT_HEADING, skip=(SUPPLEMENT_MARKER,)
                ):
                    result[index[SUPPLEMENT_HEADING]] = (
                        SUPPLEMENT_HEADING,
                        [SUPPLEMENT_MARKER, "", *[f"- {s}" for s in supplement_out]],
                    )
            except Exception:
                summary = kept_summary

    return render_sections(result), summary


def _merge_section(
    heading: str | None, lines: list[str], extra: list[str], drop_links_to: tuple[str, ...]
) -> list[str]:
    if heading == SUPPLEMENT_HEADING:
        # 固定的说明行只留一份，放在最前面
        content = _union(
            [line for line in lines if line.strip() != SUPPLEMENT_MARKER],
            [line for line in extra if line.strip() != SUPPLEMENT_MARKER],
        )
        return [SUPPLEMENT_MARKER, "", *_trim(content)]
    merged = _union(lines, extra)
    if heading in _LINK_SECTIONS:
        dead = {f"- [[{stem}]]" for stem in drop_links_to}
        merged = [line for line in merged if line.strip() not in dead and line.strip() != _EMPTY]
        return merged or [_EMPTY]
    return merged


def _is_plain_list(
    sections: list[Section], index: dict, heading: str, skip: tuple[str, ...] = ()
) -> bool:
    lines = [line for line in sections[index[heading]][1] if line.strip() and line.strip() not in skip]
    return bool(lines) and all(line.strip().startswith("- ") for line in lines)


def _points_from_both(a: list[Section], b: list[Section]) -> bool:
    return all(any(heading == _POINTS and _bullets(lines) for heading, lines in s) for s in (a, b))


def render_sections(sections: list[Section]) -> str:
    blocks = []
    for heading, lines in sections:
        text = "\n".join(lines)
        blocks.append(f"{heading}\n{text}" if heading else text)
    return "\n\n".join(block.rstrip() for block in blocks) + "\n"


def drop_link_lines(body: str, stems: Collection[str]) -> str:
    """去掉单独成行的「- [[stem]]」列表项——一条笔记不该在链接列表里指向自己。"""
    dead = {f"- [[{stem}]]" for stem in stems}
    if not any(line.strip() in dead for line in body.splitlines()):
        return body
    sections = parse_sections(body)
    for i, (heading, lines) in enumerate(sections):
        kept = [line for line in lines if line.strip() not in dead]
        if heading in _LINK_SECTIONS and not _trim(kept):
            kept = [_EMPTY]
        sections[i] = (heading, kept)
    return render_sections(sections)


def dedupe_link_lines(body: str) -> str:
    """去掉同一个区块里重复的链接列表项。合并后「- [[B]]」被改写成「- [[A]]」，可能和原有的那行撞上。"""
    sections = parse_sections(body)
    changed = False
    for i, (heading, lines) in enumerate(sections):
        seen: set[str] = set()
        kept: list[str] = []
        for line in lines:
            key = line.strip()
            if key.startswith("- [[") and key.endswith("]]") and key in seen:
                changed = True
                continue
            seen.add(key)
            kept.append(line)
        sections[i] = (heading, kept)
    return render_sections(sections) if changed else body
