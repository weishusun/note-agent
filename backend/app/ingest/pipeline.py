"""录入流水线：拆分 → 归类 → 写入（落盘、入索引、git 提交）。顺序执行，只新建不修改。"""

import hashlib
import logging
import subprocess
import time
from collections.abc import Callable

from app.extract.classifier import NoteBrief
from app.extract.extractor import _image_mime, extract_drafts
from app.ingest.assemble import assemble_concept, assemble_topic
from app.organize.service import Organizer
from app.search.index import SearchIndex
from app.search.query import find_same_name
from app.vault import git
from app.vault.models import Note, SourceKind
from app.vault.store import VaultStore, sanitize_filename

logger = logging.getLogger(__name__)

# SCHEMA 2.4：不足此长度才算短文本，更长的走 kind=file（暂未支持）
SHORT_TEXT_MAX_CHARS = 2000


def input_hash(kind: str, payload: bytes) -> str:
    """幂等键（SCHEMA 不变量 8）：只看 kind 与内容，filename 不参与。"""
    return hashlib.sha256(kind.encode("utf-8") + b"\0" + payload).hexdigest()


def run_ingest(
    store: VaultStore,
    index: SearchIndex,
    organizer: Organizer,
    *,
    kind: SourceKind,
    text: str | None = None,
    image: bytes | None = None,
    on_stage: Callable[[str], None] = lambda stage: None,
) -> list[str]:
    """处理一次输入，返回新建笔记的 id：有主题笔记时它排第一，其后是概念笔记。"""
    started = time.perf_counter()
    on_stage("拆分")
    extraction, _usage = extract_drafts(image, text)
    topic, concepts = extraction.topic, extraction.concepts
    extracted = time.perf_counter()

    on_stage("分类")
    # 这份输入产出的所有笔记在一次调用里一起归类；归类失败不会抛，分不上的进待整理
    briefs = [NoteBrief(title=d.title, summary=d.summary, points=d.points) for d in concepts]
    if topic is not None:
        briefs.insert(0, NoteBrief(title=topic.title, summary=topic.summary))
    placements = organizer.classify(briefs)
    topic_placement = placements.pop(0) if topic is not None else None
    classified = time.perf_counter()

    on_stage("写入")
    # 素材放到模型调用成功之后再存，提取失败时不在 assets/ 留孤儿文件
    asset_path = None
    if image is not None:
        asset_path = store.save_asset(image, _image_mime(image).split("/")[1])
    link_targets = {p.stem for p in store.list_notes(include_inbox=True)}
    link_targets.update(sanitize_filename(d.title) for d in concepts)

    # 两种笔记互相链接：概念笔记要写主题笔记的文件名，主题笔记要写概念笔记的文件名。
    # 先按「会分配到的文件名」建概念笔记，再用它们实际的文件名建主题笔记
    topic_stem = store.next_stem(topic.title) if topic is not None else None
    # 疑似重复在落盘之前就查好：只和**已有的**笔记比，同一次录入产出的笔记之间不互相比
    duplicates = [find_same_name(index, draft.title) for draft in concepts]
    concept_notes: list[Note] = []
    for draft, (category, confidence), duplicate in zip(concepts, placements, duplicates):
        note = assemble_concept(
            draft,
            category=category,
            confidence=confidence,
            kind=kind,
            asset_path=asset_path,
            text=text,
            topic_stem=topic_stem,
            link_targets=link_targets,
            duplicate_of=duplicate.id if duplicate is not None else None,
        )
        store.create_note(note)
        index.upsert_note(note)
        concept_notes.append(note)

    ids = [note.frontmatter.id for note in concept_notes]
    if topic is not None and topic_placement is not None:
        note = assemble_topic(
            topic,
            category=topic_placement[0],
            confidence=topic_placement[1],
            kind=kind,
            asset_path=asset_path,
            text=text,
            concept_stems=[n.path.stem for n in concept_notes if n.path is not None],
        )
        actual_stem = store.create_note(note).stem
        index.upsert_note(note)
        ids.insert(0, note.frontmatter.id)
        if actual_stem != topic_stem:
            # 极少见：两次录入同时进行，预测的文件名被别人占了。把概念笔记里的链接改过来
            for concept in concept_notes:
                if concept.path is not None:
                    fixed = store.rewrite_note(
                        concept.path,
                        body=concept.body.replace(f"[[{topic_stem}]]", f"[[{actual_stem}]]"),
                    )
                    index.upsert_note(fixed)

    # 笔记已落盘，提交失败（如 vault 不是 git 仓库）不应让整次录入算失败
    try:
        git.commit(store.root, f"note: 新建 {len(ids)} 条 (来源 {kind})")
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.warning("vault git 提交失败，笔记已写入但未提交: %s", exc)
    logger.info(
        "录入完成: %d 条, 拆分 %.1fs, 分类 %.1fs, 写入 %.1fs",
        len(ids),
        extracted - started,
        classified - extracted,
        time.perf_counter() - classified,
    )
    return ids
