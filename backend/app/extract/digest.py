"""长资料(长文本、文档、网页正文)的摘要级整理(SCHEMA 2.4):一条主题笔记(摘要 + 大纲)加关键概念。

和 extractor.py 的区别:那边面对一张图或一小段话,要求**忠实保留原结构**;这边面对几千到几十万字,
要求**读完后压缩**。资料放不进一次调用时先分段各自压成要点,再把要点拼起来做最后的整理。
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator

from app.extract.client import bind_json, get_digest_model
from app.extract.extractor import (
    ExtractError,
    _as_list,
    _as_text,
    _DraftBatch,
    _fit,
    _invoke_structured,
    _load_prompt,
    _model_id,
    _WireDraft,
    _WireTopic,
)
from app.extract.models import TOPIC_CONTENT_MAX, Extraction

logger = logging.getLogger(__name__)

# 超过这个长度只处理前面这么多,并在笔记里注明
DOC_MAX_CHARS = 200_000
# 不超过这个长度的资料一次调用读完;更长的分段
SINGLE_PASS_CHARS = 16_000
CHUNK_CHARS = 12_000
# 每一段压成的要点笔记的长度上限(写给模型看的,不硬截)
CHUNK_NOTES_CHARS = 1200
# 分段整理时同时进行的调用数
CHUNK_WORKERS = 3

OUTLINE_MAX = 12
OUTLINE_POINTS_MAX = 5
ABSTRACT_MAX = 600

Progress = Callable[[int, int], None]


class _WireSection(BaseModel):
    heading: str = ""
    points: list[str] = Field(default_factory=list)

    _lists = field_validator("points", mode="before")(_as_list)
    _texts = field_validator("heading", mode="before")(_as_text)


class _WireDigest(BaseModel):
    """模型原样返回的整理结果。和 extractor 的 wire 模型一样:只收形状,长度事后再收。"""

    title: str
    summary: str
    abstract: str
    outline: list[_WireSection]
    concepts: list[_WireDraft] = Field(default_factory=list)

    _texts = field_validator("abstract", mode="before")(_as_text)

    @field_validator("outline", "concepts", mode="before")
    @classmethod
    def _no_items(cls, value: object) -> object:
        return [] if value is None else value

    @field_validator("outline", mode="before")
    @classmethod
    def _plain_headings(cls, value: object) -> object:
        # 模型偶尔把大纲写成字符串数组:每个字符串当一节的小标题
        if isinstance(value, list):
            return [{"heading": item, "points": []} if isinstance(item, str) else item for item in value]
        return value


def digest_document(
    text: str, *, title_hint: str | None = None, on_progress: Progress = lambda done, total: None
) -> tuple[Extraction, dict]:
    """把一份长资料整理成 1 条主题笔记草稿 + 0–8 条概念笔记草稿。返回 (结果, 附带信息)。

    附带信息:{"chars": 资料总字数, "truncated": 是否只处理了前一部分, "chunks": 分了几段}。
    """
    text = text.strip()
    if not text:
        raise ValueError("digest_document 需要非空 text")
    total_chars = len(text)
    truncated = total_chars > DOC_MAX_CHARS
    if truncated:
        logger.warning("资料有 %d 字,只处理前 %d 字", total_chars, DOC_MAX_CHARS)
        text = text[:DOC_MAX_CHARS]

    model = get_digest_model()
    chunks = [text] if len(text) <= SINGLE_PASS_CHARS else split_chunks(text, CHUNK_CHARS)
    if len(chunks) == 1:
        material = "# 资料正文\n" + text
    else:
        on_progress(0, len(chunks))
        done = 0
        # 各段互不依赖，几段同时整理；结果按原文顺序取，任何一段失败整份失败
        with ThreadPoolExecutor(max_workers=CHUNK_WORKERS, thread_name_prefix="digest") as pool:
            futures = [
                pool.submit(_chunk_notes, model, chunk, i, len(chunks))
                for i, chunk in enumerate(chunks, 1)
            ]
            for future in as_completed(futures):
                if future.exception() is None:
                    done += 1
                    on_progress(done, len(chunks))
        notes = [f"## 第 {i} 部分的要点\n{future.result()}" for i, future in enumerate(futures, 1)]
        material = f"# 资料各部分的要点(共 {len(chunks)} 部分,按原文顺序)\n\n" + "\n\n".join(notes)
    if title_hint:
        material = f"文件名:{title_hint}\n\n{material}"

    digest, _usage = _invoke_structured(
        bind_json(model, _WireDigest),
        [SystemMessage(content=_load_prompt("digest_zh.md")), HumanMessage(content=material)],
        _model_id(model),
    )
    content = _content(digest, total_chars if truncated else None)
    batch = _DraftBatch(
        # 长资料的主题笔记不要模型补充：实测模型只会把资料再复述一遍。补充留给概念笔记
        topic=_WireTopic(title=digest.title, summary=digest.summary, content=content),
        concepts=digest.concepts,
    )
    extraction = _fit(batch, keep_topic=True)
    if extraction.topic is None:
        raise ExtractError("模型没有返回这份资料的摘要")
    return extraction, {"chars": total_chars, "truncated": truncated, "chunks": len(chunks)}


def split_chunks(text: str, size: int) -> list[str]:
    """按段落把文本切成每段不超过 size 字的若干段;单个段落超长时硬切。"""
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for paragraph in text.split("\n"):
        while len(paragraph) > size:
            if current:
                chunks.append("\n".join(current))
                current, length = [], 0
            chunks.append(paragraph[:size])
            paragraph = paragraph[size:]
        if length + len(paragraph) + 1 > size and current:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(paragraph)
        length += len(paragraph) + 1
    if any(line.strip() for line in current):
        chunks.append("\n".join(current))
    return chunks


def _chunk_notes(model, chunk: str, index: int, total: int) -> str:
    """把一段压成要点笔记(纯文本)。这一段调用失败时重试一次,仍失败让整份资料失败——少一段的摘要不可信。"""
    prompt = _load_prompt("digest_chunk_zh.md").format(
        index=index, total=total, limit=CHUNK_NOTES_CHARS, text=chunk
    )
    last: Exception | None = None
    for _ in range(2):
        try:
            content = model.invoke([HumanMessage(content=prompt)]).content
            notes = content if isinstance(content, str) else str(content)
            if notes.strip():
                return notes.strip()
            last = ExtractError("模型返回了空内容")
        except Exception as exc:  # 网络、限流等
            last = exc
    raise ExtractError(f"整理第 {index}/{total} 部分时模型调用失败: {last}") from last


def _content(digest: _WireDigest, truncated_from: int | None) -> str:
    """主题笔记的正文:摘要 + 大纲。结构由程序拼,不让模型自己排版。"""
    abstract = " ".join(digest.abstract.split())
    if len(abstract) > ABSTRACT_MAX:
        abstract = abstract[: ABSTRACT_MAX - 1] + "…"
    lines: list[str] = []
    number = 0
    for section in digest.outline[:OUTLINE_MAX]:
        heading = " ".join(section.heading.split()).strip("#* ")
        points = [" ".join(p.split()) for p in section.points if p.strip()][:OUTLINE_POINTS_MAX]
        if not heading and not points:
            continue
        number += 1
        lines.append(f"{number}. **{heading or '（未命名）'}**")
        lines.extend(f"   - {point}" for point in points)
    if not abstract and not lines:
        raise ExtractError("模型没有返回这份资料的摘要")

    parts = []
    if abstract:
        parts.append(f"### 摘要\n{abstract}")
    if lines:
        parts.append("### 大纲\n" + "\n".join(lines))
    if truncated_from is not None:
        parts.append(f"> 这份资料约 {truncated_from} 字,只整理了前 {DOC_MAX_CHARS} 字。")
    content = "\n\n".join(parts)
    # 大纲写得太满时从后往前去掉要点,保住每一节的小标题,不让 _fit 拦腰截断
    while len(content) > TOPIC_CONTENT_MAX and any(line.startswith("   - ") for line in lines):
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].startswith("   - "):
                del lines[i]
                break
        parts[1 if abstract else 0] = "### 大纲\n" + "\n".join(lines)
        content = "\n\n".join(parts)
    return content
