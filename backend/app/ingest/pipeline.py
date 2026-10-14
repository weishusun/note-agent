"""录入流水线：解析 → 拆分 → 归类 → 写入（落盘、入索引、git 提交）。顺序执行，只新建不修改。

输入分两路（SCHEMA 2.4）：截图和短文本交给 extractor，保留原结构；
长文本、文件和网页先读成纯文本，再交给 digest 做摘要级整理。两路的产出形状相同，之后的步骤共用。
"""

import hashlib
import logging
import subprocess
import time
from collections.abc import Callable

from app.extract.classifier import NoteBrief
from app.extract.digest import digest_document
from app.extract.extractor import _image_mime, extract_drafts
from app.extract.models import Extraction
from app.ingest.assemble import assemble_concept, assemble_topic
from app.ingest.similar import find_similar
from app.ingest.sources import read_document
from app.ingest.web import fetch_page
from app.organize.service import Organizer
from app.search.index import SearchIndex
from app.search.query import find_same_name
from app.search.vectors import VectorIndex
from app.vault import git
from app.vault.models import Note, SourceKind
from app.vault.store import VaultStore, sanitize_filename

logger = logging.getLogger(__name__)

# SCHEMA 2.4：不足此长度算短文本，保留原结构；达到或超过的按长资料做摘要级整理
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
    file: bytes | None = None,
    filename: str | None = None,
    url: str | None = None,
    vectors: VectorIndex | None = None,
    on_stage: Callable[[str], None] = lambda stage: None,
    on_progress: Callable[[int, int], None] = lambda done, total: None,
) -> list[str]:
    """处理一次输入，返回新建笔记的 id：有主题笔记时它排第一，其后是概念笔记。

    kind=file 时给 file 和 filename；kind=url 时给 url；其余给 text 或 image。
    on_progress 只在长资料分段整理时调用：(已整理的段数, 总段数)。
    vectors 给了且配了向量模型时，新的概念笔记还会按内容找疑似重复（SCHEMA 2.5）。
    """
    started = time.perf_counter()
    # 原始输入怎么留底：截图、文件存进 assets/；长文本也存成一个 .txt（正文里放不下）
    asset: tuple[bytes, str, str | None] | None = None  # (内容, 扩展名, 文件名)
    if kind == "file":
        if file is None or not filename:
            raise ValueError("kind=file 需要 file 和 filename")
        on_stage("解析")
        document = read_document(filename, file)
        on_stage("拆分")
        extraction, _info = digest_document(
            document.text, title_hint=document.title, on_progress=on_progress
        )
        asset = (file, document.ext, document.title)
        text = None
    elif kind == "url":
        if not url:
            raise ValueError("kind=url 需要 url")
        on_stage("解析")
        page = fetch_page(url)
        on_stage("拆分")
        if len(page.text) >= SHORT_TEXT_MAX_CHARS:
            extraction, _info = digest_document(
                page.text, title_hint=page.title or None, on_progress=on_progress
            )
        else:
            # 很短的网页和短文本一样处理：保留原结构
            body = f"{page.title}\n\n{page.text}" if page.title else page.text
            extraction, _usage = extract_drafts(None, body)
        # 网页不留底，只记网址；assemble 从 text 里取它
        text = url
    elif image is None and text is not None and len(text.strip()) >= SHORT_TEXT_MAX_CHARS:
        on_stage("拆分")
        extraction, _info = digest_document(text, on_progress=on_progress)
        asset = (text.strip().encode("utf-8"), "txt", None)
    else:
        on_stage("拆分")
        extraction, _usage = extract_drafts(image, text)
        if image is not None:
            asset = (image, _image_mime(image).split("/")[1], None)
    extracted = time.perf_counter()

    on_stage("分类")
    placements = _classify(organizer, extraction)
    classified = time.perf_counter()

    on_stage("写入")
    # 素材放到模型调用成功之后再存，提取失败时不在 assets/ 留孤儿文件
    asset_path = store.save_asset(asset[0], asset[1], name=asset[2]) if asset is not None else None
    ids = _write(
        store,
        index,
        extraction,
        placements,
        kind=kind,
        asset_path=asset_path,
        text=text,
        vectors=vectors,
    )

    # 笔记已落盘，提交失败（如 vault 不是 git 仓库）不应让整次录入算失败
    try:
        git.commit(store.root, f"note: 新建 {len(ids)} 条 (来源 {kind})")
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("vault git 提交失败，笔记已写入但未提交: %s", exc)
    logger.info(
        "录入完成: %d 条, 拆分 %.1fs, 分类 %.1fs, 写入 %.1fs",
        len(ids),
        extracted - started,
        classified - extracted,
        time.perf_counter() - classified,
    )
    return ids


Placement = tuple[str | None, float]


def _classify(organizer: Organizer, extraction: Extraction) -> list[Placement]:
    """这份输入产出的所有笔记在一次调用里一起归类：主题笔记（如果有）排第一。归类失败不会抛，分不上的进待整理。"""
    briefs = [NoteBrief(title=d.title, summary=d.summary, points=d.points) for d in extraction.concepts]
    if extraction.topic is not None:
        briefs.insert(0, NoteBrief(title=extraction.topic.title, summary=extraction.topic.summary))
    return organizer.classify(briefs)


def _write(
    store: VaultStore,
    index: SearchIndex,
    extraction: Extraction,
    placements: list[Placement],
    *,
    kind: SourceKind,
    asset_path: str | None,
    text: str | None,
    vectors: VectorIndex | None = None,
) -> list[str]:
    topic, concepts = extraction.topic, extraction.concepts
    topic_placement = placements.pop(0) if topic is not None else None
    link_targets = {p.stem for p in store.list_notes(include_inbox=True)}
    link_targets.update(sanitize_filename(d.title) for d in concepts)

    # 两种笔记互相链接：概念笔记要写主题笔记的文件名，主题笔记要写概念笔记的文件名。
    # 先按「会分配到的文件名」建概念笔记，再用它们实际的文件名建主题笔记
    topic_stem = store.next_stem(topic.title) if topic is not None else None
    concept_notes = [
        assemble_concept(
            draft,
            category=category,
            confidence=confidence,
            kind=kind,
            asset_path=asset_path,
            text=text,
            topic_stem=topic_stem,
            link_targets=link_targets,
        )
        for draft, (category, confidence) in zip(concepts, placements)
    ]
    # 疑似重复在落盘之前就查好：只和**已有的**笔记比，同一次录入产出的笔记之间不互相比。
    # 先比名字；名字没撞上的，再按内容找（SCHEMA 2.5）
    by_name = [find_same_name(index, draft.title) for draft in concepts]
    by_content, new_vectors = find_similar(
        store, vectors, concept_notes, skip=[ref is not None for ref in by_name]
    )
    for note, same_name, similar, vector in zip(concept_notes, by_name, by_content, new_vectors):
        duplicate = same_name or similar
        if duplicate is not None:
            note.frontmatter = note.frontmatter.model_copy(update={"duplicate_of": duplicate.id})
        store.create_note(note)
        # 先存向量再入索引：索引一变动后台就会去补算，这时它该看到向量已经在了
        if vectors is not None and vector is not None:
            vectors.store(note, vector)
        index.upsert_note(note)

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
    return ids
