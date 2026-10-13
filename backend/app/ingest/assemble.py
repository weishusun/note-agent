"""草稿 + 归类结果 → Note。纯函数，不读写 vault；两种笔记的正文结构见 SCHEMA 2.3。"""

from collections.abc import Collection, Sequence
from datetime import datetime

from app.extract.models import NoteDraft, TopicDraft
from app.organize.service import placement_status
from app.vault.models import Note, NoteFrontmatter, NoteType, SourceKind, SourceRef
from app.vault.store import sanitize_filename

_EMPTY_RELATED = "（暂无）"
_TEXT_REF_CHARS = 100
_LONG_TEXT_EXCERPT_CHARS = 200
# 「模型补充」区块固定的第一行（SCHEMA 不变量 14）：模型自己的知识不能和使用者的资料长得一样
SUPPLEMENT_HEADING = "## 模型补充"
SUPPLEMENT_MARKER = "> 以下内容由模型根据自身知识补充，不是来自你的资料。"


def assemble_topic(
    topic: TopicDraft,
    *,
    category: str | None,
    confidence: float,
    kind: SourceKind,
    asset_path: str | None = None,
    text: str | None = None,
    concept_stems: Sequence[str] = (),
    now: datetime | None = None,
) -> Note:
    """主题笔记：保留原结构的内容、本次一起产出的概念笔记、模型补充、原始输入。

    concept_stems 是那些概念笔记**实际落盘**的文件名（不含 .md），由调用方在建完它们之后传入。
    """
    source, original = _source(kind, asset_path, text)
    sections = [f"## 内容\n{topic.content}"]
    if concept_stems:
        sections.append("## 概念\n" + "\n".join(f"- [[{stem}]]" for stem in concept_stems))
    if topic.supplement:
        sections.append(_supplement_block(topic.supplement))
    sections.append(f"## 原始输入\n{original}")
    return _note("topic", topic.title, topic.summary, [], sections, category, confidence, source, now)


def assemble_concept(
    draft: NoteDraft,
    *,
    category: str | None,
    confidence: float,
    kind: SourceKind,
    asset_path: str | None = None,
    text: str | None = None,
    topic_stem: str | None = None,
    link_targets: Collection[str] = (),
    duplicate_of: str | None = None,
    now: datetime | None = None,
) -> Note:
    """概念笔记：要点、模型补充、关联。duplicate_of 是疑似与之重复的已有笔记的 id（SCHEMA 2.5）。

    topic_stem 是它出自的主题笔记的文件名；有主题笔记时原始输入只放在主题笔记里，
    没有时（输入只讲一个概念）放在这条的末尾。link_targets 是「真实存在或本批即将创建」
    的笔记文件名，related 里对不上的概念不写链接。
    """
    source, original = _source(kind, asset_path, text)
    sections = ["## 要点\n" + "\n".join(f"- {p}" for p in draft.points)]
    if draft.supplement:
        sections.append(_supplement_block("\n".join(f"- {s}" for s in draft.supplement)))
    sections.append("## 关联\n" + _related_block(draft, topic_stem, link_targets))
    if topic_stem is None:
        sections.append(f"## 原始输入\n{original}")
    note = _note(
        "concept", draft.title, draft.summary, draft.aliases, sections, category, confidence, source, now
    )
    note.frontmatter = note.frontmatter.model_copy(update={"duplicate_of": duplicate_of})
    return note


def _note(
    note_type: NoteType,
    title: str,
    summary: str,
    aliases: list[str],
    sections: list[str],
    category: str | None,
    confidence: float,
    source: SourceRef,
    now: datetime | None,
) -> Note:
    """category 为 None 或置信度不够时进待整理。id 留给 store.create_note 定。"""
    status, confidence = placement_status(category, confidence)
    stamp = now if now is not None else datetime.now().astimezone()
    return Note(
        frontmatter=NoteFrontmatter(
            type=note_type,
            title=title,
            category=category,
            aliases=aliases,
            summary=summary,
            status=status,
            confidence=confidence,
            created=stamp,
            updated=stamp,
            source=source,
        ),
        body="\n\n".join(sections) + "\n",
    )


def _supplement_block(content: str) -> str:
    return f"{SUPPLEMENT_HEADING}\n{SUPPLEMENT_MARKER}\n\n{content}"


def _source(kind: SourceKind, asset_path: str | None, text: str | None) -> tuple[SourceRef, str]:
    if kind == "screenshot":
        if not asset_path:
            raise ValueError("kind=screenshot 需要 asset_path")
        return SourceRef(kind=kind, ref=asset_path), f"![[{asset_path}]]"
    if kind == "url":
        # 网页不留底：text 里传的是网址，来源和原始输入都只记它
        url = (text or "").strip()
        if not url:
            raise ValueError("kind=url 需要网址")
        return SourceRef(kind=kind, ref=url), url
    if kind == "file":
        if not asset_path:
            raise ValueError("kind=file 需要 asset_path")
        return SourceRef(kind=kind, ref=asset_path), f"![[{asset_path}]]"
    if kind == "text":
        stripped = (text or "").strip()
        if not stripped:
            raise ValueError("kind=text 需要非空 text")
        source = SourceRef(kind=kind, ref=stripped[:_TEXT_REF_CHARS])
        if asset_path:
            # 长文本：全文存成了素材文件，正文里只引开头一小段
            excerpt = stripped[:_LONG_TEXT_EXCERPT_CHARS]
            quoted = "\n".join(f"> {line}".rstrip() for line in excerpt.splitlines())
            return source, f"{quoted}……\n\n全文：![[{asset_path}]]"
        quoted = "\n".join(f"> {line}".rstrip() for line in stripped.splitlines())
        return source, quoted
    raise ValueError(f"暂不支持的来源: {kind}")


def _related_block(draft: NoteDraft, topic_stem: str | None, link_targets: Collection[str]) -> str:
    # 链接与 Windows 文件名都不分大小写，按 casefold 对齐后写回真实文件名
    by_key = {stem.casefold(): stem for stem in link_targets}
    own = sanitize_filename(draft.title).casefold()
    # 第一条是它出自的主题笔记
    links: list[str] = [topic_stem] if topic_stem else []
    for name in draft.related:
        key = sanitize_filename(name).casefold()
        stem = by_key.get(key)
        if stem is None or key == own or stem in links:
            continue
        links.append(stem)
    if not links:
        return _EMPTY_RELATED
    return "\n".join(f"- [[{stem}]]" for stem in links)
