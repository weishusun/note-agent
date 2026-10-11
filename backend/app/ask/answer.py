"""检索 → 读笔记正文 → 模型作答。正文从磁盘读（SCHEMA 不变量 13：以磁盘为准）。"""

from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.ask.expand import expand_question
from app.extract.client import get_answer_model
from app.search.index import SearchIndex
from app.search.query import note_vocabulary, retrieve_for_question
from app.vault.models import NoteRef
from app.vault.store import VaultStore

_PROMPT_PATH = Path(__file__).parent / "prompts" / "answer_zh.md"


class AskResult(BaseModel):
    answer: str
    # 顺序即回答里的引用编号：sources[0] 对应 [1]
    sources: list[NoteRef]
    # False 表示没检索到任何笔记，回答完全来自模型自身知识
    grounded: bool
    # 模型为这个问题扩展出的检索词；扩展失败时为空
    keywords: list[str]


def answer_question(
    store: VaultStore, index: SearchIndex, question: str, limit: int = 5
) -> AskResult:
    keywords = expand_question(question, note_vocabulary(index))
    sources: list[NoteRef] = []
    blocks: list[str] = []
    for ref in retrieve_for_question(index, question, limit, keywords=keywords):
        try:
            note = store.read_note(ref.path)
        except (FileNotFoundError, ValueError):
            # 索引里有、磁盘上已被用户删掉或改坏：跳过，不让一条坏笔记拖垮整次问答
            continue
        sources.append(ref)
        blocks.append(f"[{len(sources)}] {note.frontmatter.title}\n{note.body.strip()}")

    notes_text = "\n\n".join(blocks) if blocks else "(没有检索到任何笔记)"
    messages = [
        SystemMessage(content=_PROMPT_PATH.read_text(encoding="utf-8")),
        HumanMessage(content=f"# 知识库笔记\n\n{notes_text}\n\n# 问题\n\n{question.strip()}"),
    ]
    reply = get_answer_model().invoke(messages)
    return AskResult(
        answer=_text(reply.content), sources=sources, grounded=bool(sources), keywords=keywords
    )


def _text(content: object) -> str:
    """content 通常是 str；个别模型返回分段列表，只取文本段。"""
    if isinstance(content, str):
        return content.strip()
    parts = [p.get("text", "") for p in content if isinstance(p, dict)]  # type: ignore[union-attr]
    return "".join(parts).strip()
