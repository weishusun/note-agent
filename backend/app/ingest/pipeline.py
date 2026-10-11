"""录入流水线：拆分 → 归类 → 写入（落盘、入索引、git 提交）。顺序执行，只新建不修改。"""

import hashlib
import logging
import subprocess
import time
from collections.abc import Callable

from app.extract.classifier import NoteBrief
from app.extract.extractor import _image_mime, extract_drafts
from app.ingest.assemble import assemble_note
from app.organize.service import Organizer
from app.search.index import SearchIndex
from app.vault import git
from app.vault.models import SourceKind
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
    """处理一次输入，返回新建笔记的 id（顺序与草稿一致）。"""
    started = time.perf_counter()
    on_stage("拆分")
    drafts, _usage = extract_drafts(image, text)
    extracted = time.perf_counter()

    on_stage("分类")
    # 这份输入产出的所有草稿在一次调用里一起归类；归类失败不会抛，分不上的进待整理
    placements = organizer.classify(
        [NoteBrief(title=d.title, summary=d.summary, points=d.points) for d in drafts]
    )

    classified = time.perf_counter()

    on_stage("写入")
    # 素材放到模型调用成功之后再存，提取失败时不在 assets/ 留孤儿文件
    asset_path = None
    if image is not None:
        asset_path = store.save_asset(image, _image_mime(image).split("/")[1])
    link_targets = {p.stem for p in store.list_notes(include_inbox=True)}
    link_targets.update(sanitize_filename(d.title) for d in drafts)

    ids: list[str] = []
    for draft, (category, confidence) in zip(drafts, placements):
        note = assemble_note(
            draft,
            category=category,
            confidence=confidence,
            kind=kind,
            asset_path=asset_path,
            text=text,
            link_targets=link_targets,
        )
        store.create_note(note)
        index.upsert_note(note)
        ids.append(note.frontmatter.id)

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
