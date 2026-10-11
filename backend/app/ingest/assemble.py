"""草稿 + 归类结果 → Note。纯函数，不读写 vault；正文结构见 SCHEMA 2.3。"""

from collections.abc import Collection
from datetime import datetime

from app.extract.models import NoteDraft
from app.organize.service import placement_status
from app.vault.models import Note, NoteFrontmatter, SourceKind, SourceRef
from app.vault.store import sanitize_filename

_EMPTY_RELATED = "（暂无）"
_TEXT_REF_CHARS = 100


def assemble_note(
    draft: NoteDraft,
    *,
    category: str | None,
    confidence: float,
    kind: SourceKind,
    asset_path: str | None = None,
    text: str | None = None,
    link_targets: Collection[str] = (),
    now: datetime | None = None,
) -> Note:
    """link_targets 是「真实存在或本批即将创建」的笔记文件名（不含 .md），
    related 里对不上的概念不写链接。category 为 None 或置信度不够时进待整理。
    id 留给 store.create_note 定。"""
    source, original = _source(kind, asset_path, text)
    status, confidence = placement_status(category, confidence)
    stamp = now if now is not None else datetime.now().astimezone()

    body = "\n\n".join(
        [
            f"## 一句话\n{draft.summary}",
            "## 要点\n" + "\n".join(f"- {p}" for p in draft.points),
            "## 关联\n" + _related_block(draft, link_targets),
            f"## 原始输入\n{original}",
        ]
    )
    return Note(
        frontmatter=NoteFrontmatter(
            title=draft.title,
            category=category,
            aliases=draft.aliases,
            summary=draft.summary,
            status=status,
            confidence=confidence,
            created=stamp,
            updated=stamp,
            source=source,
        ),
        body=body + "\n",
    )


def _source(kind: SourceKind, asset_path: str | None, text: str | None) -> tuple[SourceRef, str]:
    if kind == "screenshot":
        if not asset_path:
            raise ValueError("kind=screenshot 需要 asset_path")
        return SourceRef(kind=kind, ref=asset_path), f"![[{asset_path}]]"
    if kind == "text":
        stripped = (text or "").strip()
        if not stripped:
            raise ValueError("kind=text 需要非空 text")
        quoted = "\n".join(f"> {line}".rstrip() for line in stripped.splitlines())
        return SourceRef(kind=kind, ref=stripped[:_TEXT_REF_CHARS]), quoted
    raise ValueError(f"暂不支持的来源: {kind}")


def _related_block(draft: NoteDraft, link_targets: Collection[str]) -> str:
    # Obsidian 链接与 Windows 文件名都不分大小写，按 casefold 对齐后写回真实文件名
    by_key = {stem.casefold(): stem for stem in link_targets}
    own = sanitize_filename(draft.title).casefold()
    links: list[str] = []
    for name in draft.related:
        key = sanitize_filename(name).casefold()
        stem = by_key.get(key)
        if stem is None or key == own or stem in links:
            continue
        links.append(stem)
    if not links:
        return _EMPTY_RELATED
    return "\n".join(f"- [[{stem}]]" for stem in links)
