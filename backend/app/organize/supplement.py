"""给一条笔记写（或重写）「模型补充」区块：录入后自动跑一次，阅读区也可以点「重新补充」。

问模型在 app/extract/supplement.py；这里负责读笔记、换掉区块、写回、更新索引和提交。
笔记的其他区块一个字不动。
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Literal

from app.extract import supplement as supplement_writer
from app.ingest.assemble import SUPPLEMENT_HEADING, SUPPLEMENT_MARKER
from app.organize.merge import Section, parse_sections, render_sections
from app.organize.service import NoteNotFound, Organizer
from app.search.query import all_refs, find_by_id
from app.vault.models import Note, NoteRef

logger = logging.getLogger(__name__)

_SOURCE = "## 原始输入"
# 补充区块放在这些区块前面（与录入时拼出来的笔记一致：要点 / 内容之后、关联之前）
_AFTER_SUPPLEMENT = ("## 关联", "## 概念", _SOURCE)
# 一次录入的几条笔记同时补，别一下子打太多请求
_PARALLEL = 3
# 比旧版主题笔记补充的上限（300 字）长出一截才算补充技能写的
_WRITTEN_MIN_CHARS = 400


def material_of(body: str) -> str:
    """笔记里来自使用者资料的部分：去掉旧的模型补充和原始输入（截图链接、网址对模型没用）。"""
    kept = [s for s in parse_sections(body) if s[0] not in (SUPPLEMENT_HEADING, _SOURCE)]
    return render_sections(kept).strip()


def replace_supplement(body: str, text: str) -> str:
    """把正文里的模型补充区块换成 text；没有就插在关联 / 概念 / 原始输入之前（都没有就放最后）。"""
    block: Section = (SUPPLEMENT_HEADING, [SUPPLEMENT_MARKER, "", *text.strip().split("\n")])
    sections = parse_sections(body)
    for i, (heading, _lines) in enumerate(sections):
        if heading == SUPPLEMENT_HEADING:
            sections[i] = block
            return render_sections(sections)
    at = next((i for i, (h, _l) in enumerate(sections) if h in _AFTER_SUPPLEMENT), len(sections))
    sections.insert(at, block)
    return render_sections(sections)


BatchScope = Literal["outdated", "all"]


def supplement_state(body: str) -> Literal["missing", "old", "written"]:
    """missing：没有模型补充；old：提取时写的短补充；written：补充技能写的成段内容。

    旧的补充是几条短句（概念笔记，列表）或一小段话（主题笔记，最多 300 字）；
    补充技能写的是 600–1000 字的成段内容。按「是不是全是列表项」和长度区分。
    """
    for heading, lines in parse_sections(body):
        if heading == SUPPLEMENT_HEADING:
            content = [line for line in lines if line.strip() and line.strip() != SUPPLEMENT_MARKER]
            if not content:
                return "missing"
            if all(line.strip().startswith("- ") for line in content):
                return "old"
            return "written" if len("\n".join(content)) >= _WRITTEN_MIN_CHARS else "old"
    return "missing"


def is_digest_topic(note: Note) -> bool:
    """长资料（文件、网页、长文本）的主题笔记：正文是摘要和大纲，补充只会把资料再讲一遍，不补。"""
    fm = note.frontmatter
    if fm.type != "topic":
        return False
    return fm.source.kind in ("file", "url") or "\n### 摘要" in f"\n{note.body}"


class Supplementer:
    def __init__(self, organizer: Organizer) -> None:
        self.organizer = organizer
        self.store = organizer.store
        self.index = organizer.index

    def supplement(self, note_id: str) -> NoteRef:
        """给这条笔记写模型补充并存盘。模型失败时抛异常，笔记不变。"""
        ref = find_by_id(self.index, note_id)
        if ref is None:
            raise NoteNotFound(note_id)
        note = self.store.read_note(ref.path)
        fm = note.frontmatter
        text = supplement_writer.write_supplement(
            fm.title, fm.summary, material_of(note.body), note_type=fm.type, aliases=fm.aliases
        )
        # 模型写了十几秒：这期间使用者可能改过、挪过这条笔记，按 id 重新找、重新读再换区块
        ref = find_by_id(self.index, note_id)
        if ref is None:
            raise NoteNotFound(note_id)
        current = self.store.read_note(ref.path)
        updated = self.store.rewrite_note(
            ref.path,
            body=replace_supplement(current.body, text),
            updated=datetime.now().astimezone(),
        )
        self.index.upsert_note(updated)
        self.organizer._commit(f"note: 模型补充「{updated.frontmatter.title}」")
        return find_by_id(self.index, note_id) or ref

    def candidates(self, scope: BatchScope = "outdated") -> list[str]:
        """批量补充要补哪些笔记（按路径排序）。

        outdated：还没有补充、或者只有提取时写的短句的；all：全部重写。长资料的主题笔记都不算。
        """
        ids = []
        for ref in all_refs(self.index):
            try:
                note = self.store.read_note(ref.path)
            except (FileNotFoundError, ValueError):
                continue
            if is_digest_topic(note):
                continue
            if scope == "all" or supplement_state(note.body) != "written":
                ids.append(ref.id)
        return ids

    def supplement_many(
        self, note_ids: list[str], on_progress: Callable[[int, int], None] | None = None
    ) -> tuple[list[str], list[str]]:
        """几条一起补（并行）。返回 (补好了的 id, 失败原因)；已经删掉的笔记跳过，不算失败。"""
        done: list[str] = []
        errors: list[str] = []
        finished = 0
        if on_progress:
            on_progress(0, len(note_ids))

        def one(note_id: str) -> None:
            nonlocal finished
            try:
                done.append(self.supplement(note_id).id)
            except NoteNotFound:
                logger.info("要补充的笔记 %s 已经不在了，跳过", note_id)
            except Exception as exc:
                logger.exception("给笔记 %s 写模型补充失败", note_id)
                errors.append(str(exc)[:200])
            finally:
                finished += 1
                if on_progress:
                    on_progress(finished, len(note_ids))

        with ThreadPoolExecutor(max_workers=_PARALLEL) as pool:
            list(pool.map(one, note_ids))
        return done, errors
