"""使用者对单条笔记的主动操作：编辑、删除、手写新建（SCHEMA 2.6）。

这些操作会改写或删除已有笔记，并顺带把全库里指向它的链接、指向它的「疑似重复」标记一起理顺。
每次成功后一次 vault 的 git 提交。
"""

import logging
from datetime import datetime

from app.extract.classifier import NoteBrief
from app.extract.models import ALIASES_MAX, SUMMARY_MAX, TITLE_MAX
from app.organize.service import NoteNotFound, Organizer, placement_status
from app.search.query import find_by_id, refs_with_duplicate_of
from app.vault.models import Note, NoteFrontmatter, NoteRef, SourceRef, split_category
from app.vault.store import sanitize_filename

logger = logging.getLogger(__name__)

# 手写的别名比模型给的宽松一些
MANUAL_ALIASES_MAX = ALIASES_MAX * 2
BODY_MAX = 20000


def _clean_title(title: str) -> str:
    title = (title or "").strip()
    if not title:
        raise ValueError("标题不能为空")
    if len(title) > TITLE_MAX:
        raise ValueError(f"标题最长 {TITLE_MAX} 个字")
    return title


def _clean_summary(summary: str) -> str:
    summary = " ".join((summary or "").split())
    if not summary:
        raise ValueError("一句话概要不能为空")
    if len(summary) > SUMMARY_MAX:
        raise ValueError(f"一句话概要最长 {SUMMARY_MAX} 个字")
    return summary


def _clean_aliases(aliases: list[str], title: str) -> list[str]:
    kept: list[str] = []
    for alias in aliases:
        alias = (alias or "").strip()
        if alias and alias.casefold() != title.casefold() and alias not in kept:
            kept.append(alias)
    if len(kept) > MANUAL_ALIASES_MAX:
        raise ValueError(f"别名最多 {MANUAL_ALIASES_MAX} 个")
    return kept


def _clean_body(body: str) -> str:
    body = (body or "").replace("\r\n", "\n").strip()
    if len(body) > BODY_MAX:
        raise ValueError(f"正文最长 {BODY_MAX} 个字")
    return body + "\n"


class NoteEditor:
    def __init__(self, organizer: Organizer) -> None:
        self.organizer = organizer
        self.store = organizer.store
        self.index = organizer.index

    def _ref(self, note_id: str) -> NoteRef:
        ref = find_by_id(self.index, note_id)
        if ref is None:
            raise NoteNotFound(note_id)
        return ref

    def edit(
        self,
        note_id: str,
        *,
        title: str | None = None,
        summary: str | None = None,
        aliases: list[str] | None = None,
        body: str | None = None,
    ) -> NoteRef:
        """只改给了的字段。改标题会重命名文件，并把全库里指向旧文件名的链接改过来。"""
        ref = self._ref(note_id)
        changes: dict[str, object] = {}
        new_title = ref.title
        if title is not None:
            new_title = _clean_title(title)
            changes["title"] = new_title
        if summary is not None:
            changes["summary"] = _clean_summary(summary)
        if aliases is not None:
            changes["aliases"] = _clean_aliases(aliases, new_title)
        new_body = _clean_body(body) if body is not None else None
        changes["updated"] = datetime.now().astimezone()

        try:
            note = self.store.rewrite_note(ref.path, body=new_body, **changes)
        except FileNotFoundError:
            raise NoteNotFound(note_id) from None

        old_stem = note.path.stem if note.path is not None else ""
        if sanitize_filename(new_title) != sanitize_filename(ref.title):
            # 文件名跟着标题走（SCHEMA 2.1）
            new_stem = self.store.next_stem(new_title)
            new_path = self.store.rename_note(ref.path, new_stem)
            self.index.remove_note(ref.path)
            note.path = new_path
            for linked in self.store.replace_links(old_stem, new_stem):
                self.index.upsert_note(linked)
            # 它自己的正文里也可能有指向自己的链接，重读一遍拿到改写后的正文
            note = self.store.read_note(new_path)
        self.index.upsert_note(note)
        self.organizer._commit(f"note: 编辑「{note.frontmatter.title}」")
        return self._ref(note_id)

    def delete(self, note_id: str) -> None:
        """删掉笔记文件；别的笔记里指向它的链接退化成纯文字，指向它的疑似重复标记清空。"""
        ref = self._ref(note_id)
        try:
            self.store.delete_note(ref.path)
        except FileNotFoundError:
            # 文件已经不在了（比如在应用之外被删）：把索引里的残留清掉，同样算删除成功
            logger.warning("删除 %s 时文件已不存在，只清理索引", ref.path)
        self.index.remove_note(ref.path)
        self.forget(ref, replacement_stem=None)
        self.organizer._prune()
        self.organizer._commit(f"note: 删除「{ref.title}」")

    def forget(self, ref: NoteRef, replacement_stem: str | None) -> None:
        """一条笔记不在了之后的收尾：改写指向它的链接，清空指向它的疑似重复标记。

        replacement_stem 给了（合并时）链接改指那条笔记，否则退化成纯文字。
        """
        stem = ref.path.rsplit("/", 1)[-1].removesuffix(".md")
        for linked in self.store.replace_links(stem, replacement_stem):
            self.index.upsert_note(linked)
        if ref.id is None:
            return
        for marked in refs_with_duplicate_of(self.index, ref.id):
            try:
                self.index.upsert_note(self.store.rewrite_note(marked.path, duplicate_of=None))
            except (FileNotFoundError, ValueError) as exc:
                logger.warning("清除 %s 的疑似重复标记失败: %s", marked.path, exc)

    def create(
        self,
        *,
        title: str,
        summary: str,
        body: str,
        aliases: list[str] | None = None,
        category: str | None = None,
    ) -> NoteRef:
        """手写新建一条概念笔记。给了分类按手动指定处理（锁定），没给就自动归类。"""
        title = _clean_title(title)
        summary = _clean_summary(summary)
        aliases = _clean_aliases(aliases or [], title)
        body = _clean_body(body)

        if category:
            split_category(category)  # 名字不合规在建任何东西之前就报错
            category = self.organizer.taxonomy.ensure(category, locked=True)
            status, confidence = "active", 1.0
        else:
            (category, guessed), = self.organizer.classify([NoteBrief(title=title, summary=summary)])
            status, confidence = placement_status(category, guessed)

        now = datetime.now().astimezone()
        note = Note(
            frontmatter=NoteFrontmatter(
                type="concept",
                title=title,
                category=category,
                aliases=aliases,
                summary=summary,
                status=status,
                confidence=confidence,
                created=now,
                updated=now,
                source=SourceRef(kind="manual", ref="手写"),
            ),
            body=body,
        )
        rel = self.store.create_note(note)
        if confidence == 1.0 and status == "active" and category:
            # create_note 一律不锁；使用者亲手选的分类要锁上
            note = self.store.update_frontmatter(rel, category_locked=True)
        self.index.upsert_note(note)
        self.organizer._commit(f"note: 手写新建「{title}」")
        return NoteRef.from_note(note)
