"""检索 → 读笔记正文 → 模型作答。正文从磁盘读（SCHEMA 不变量 13：以磁盘为准）。

检索是字面和向量两路合并（app/search/hybrid.py）；向量那一路不可用时只剩字面检索，问答照常。
追问时先结合上文把问题改写成一句完整的话再去检索（app/ask/expand.py），回答时把上文一并交给模型。
回答是流式生成的（ask_events）：检索完先交出来源，回答一段一段地交；answer_question 是收拢成一次返回的版本。
"""

import re
from collections.abc import Iterator, Sequence
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.ask.expand import Turn, expand_question, rewrite_question
from app.extract.client import get_answer_model
from app.search.hybrid import retrieve, vocabulary
from app.search.index import SearchIndex
from app.search.vectors import VectorIndex
from app.vault.models import NoteRef
from app.vault.store import VaultStore

_PROMPT_PATH = Path(__file__).parent / "prompts" / "answer_zh.md"
# 交给问答模型的上文：最近几轮，每轮的回答最多这么多字
HISTORY_TURNS = 4
HISTORY_ANSWER_CHARS = 1500
_CITATION = re.compile(r"\s*\[\d+\]")


class AskResult(BaseModel):
    answer: str
    # 顺序即回答里的引用编号：sources[0] 对应 [1]
    sources: list[NoteRef]
    # False 表示没检索到任何笔记，回答完全来自模型自身知识
    grounded: bool
    # 模型为这个问题扩展出的检索词；扩展失败时为空
    keywords: list[str]
    # 追问时：结合上文改写出来、实际拿去检索的那句话。不是追问、或改写后和原问题一样时为 None
    rewritten: str | None = None


def answer_question(
    store: VaultStore,
    index: SearchIndex,
    question: str,
    limit: int = 5,
    vectors: VectorIndex | None = None,
    history: Sequence[Turn] = (),
) -> AskResult:
    """一次答完：把 ask_events 的事件收拢成一个结果。"""
    found: dict = {}
    for event, data in ask_events(store, index, question, limit, vectors, history):
        if event == "sources":
            found = data
        elif event == "done":
            return AskResult(answer=data["answer"], **found)
    raise RuntimeError("问答没有正常结束")


def ask_events(
    store: VaultStore,
    index: SearchIndex,
    question: str,
    limit: int = 5,
    vectors: VectorIndex | None = None,
    history: Sequence[Turn] = (),
) -> Iterator[tuple[str, dict]]:
    """边检索边作答，依次产出 (事件名, 数据)：sources 一次，delta 若干次，最后 done（SCHEMA §5 POST /api/ask/stream）。

    模型调用失败时直接抛异常，由调用方决定怎么告诉界面。
    """
    question = question.strip()
    history = list(history)
    # 挑词表用的那句话：追问时单看这一问不知道在说什么，带上上一问
    probe = f"{history[-1].question.strip()} {question}" if history else question
    query = vectors.embed_query(probe) if vectors is not None else None
    words = vocabulary(index, vectors, query)
    if history:
        search_for, keywords = rewrite_question(question, history, words)
        if query is not None and search_for != probe:
            again = vectors.embed_query(search_for)  # type: ignore[union-attr]
            if again is not None:
                query = again
    else:
        search_for, keywords = question, expand_question(question, words)
    # 问题的向量没算出来就不再去算第二次：接口多半还是不通，白等一个超时
    usable = vectors if query is not None else None

    sources: list[NoteRef] = []
    blocks: list[str] = []
    for ref in retrieve(index, usable, search_for, limit, keywords=keywords, query=query):
        try:
            note = store.read_note(ref.path)
        except (FileNotFoundError, ValueError):
            # 索引里有、磁盘上已被用户删掉或改坏：跳过，不让一条坏笔记拖垮整次问答
            continue
        sources.append(ref)
        # 概要只存在 frontmatter 里（SCHEMA 2.3），要单独带上；正文里的「模型补充」区块自带标记
        blocks.append(
            f"[{len(sources)}] {note.frontmatter.title}\n"
            f"概要：{note.frontmatter.summary}\n{note.body.strip()}"
        )
    yield "sources", {
        "sources": sources,
        "grounded": bool(sources),
        "keywords": keywords,
        "rewritten": search_for if history and search_for != question else None,
    }

    notes_text = "\n\n".join(blocks) if blocks else "(没有检索到任何笔记)"
    messages: list = [SystemMessage(content=_PROMPT_PATH.read_text(encoding="utf-8"))]
    for turn in history[-HISTORY_TURNS:]:
        messages.append(HumanMessage(content=turn.question.strip()))
        messages.append(AIMessage(content=_past_answer(turn.answer)))
    messages.append(
        HumanMessage(content=f"# 知识库笔记\n\n{notes_text}\n\n# 问题\n\n{question}")
    )
    pieces: list[str] = []
    for chunk in get_answer_model().stream(messages):
        text = _text(chunk.content)
        if text:
            pieces.append(text)
            yield "delta", {"text": text}
    yield "done", {"answer": _drop_dangling_citations("".join(pieces).strip(), len(sources))}


# 前面紧挨着字母数字的不算引用：那是 arr[0] 这样的代码
_MARKER = re.compile(r" ?(?<![A-Za-z0-9_])\[(\d{1,2})\]")


def _drop_dangling_citations(answer: str, count: int) -> str:
    """去掉编号对不上任何一条笔记的引用。

    真实接口上见过：一条笔记都没检索到，模型在「模型补充」那段里照样标了 [1][2][3]——
    读的人会以为那几句话有出处。
    """
    return _MARKER.sub(lambda m: m.group(0) if 1 <= int(m.group(1)) <= count else "", answer)


def _past_answer(answer: str) -> str:
    """之前的回答交给模型当上文时：去掉引用编号（那些编号对应的是当时检索到的笔记，和这次的对不上），截短。"""
    text = _CITATION.sub("", answer).strip()
    return text if len(text) <= HISTORY_ANSWER_CHARS else text[:HISTORY_ANSWER_CHARS] + "…"


def _text(content: object) -> str:
    """content 通常是 str；个别模型返回分段列表，只取文本段。流式的一段段文字原样拼接，不去空白。"""
    if isinstance(content, str):
        return content
    parts = [p.get("text", "") for p in content if isinstance(p, dict)]  # type: ignore[union-attr]
    return "".join(parts)
