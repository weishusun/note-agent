"""检索之前先让文本模型把问题「读懂」：扩展出检索词；追问时先结合上文改写成一句完整的问题。

两件事都只是锦上添花：模型调用失败时退回原问题，不能因为它让整次问答失败。
"""

import logging
from collections.abc import Sequence
from pathlib import Path

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.extract.client import bind_json, get_expand_model

logger = logging.getLogger(__name__)

_PROMPTS = Path(__file__).parent / "prompts"
MAX_KEYWORDS = 8
# 上限放到标题的最大长度：模型会把库里已有的笔记标题原样当检索词返回
_MIN_LEN, _MAX_LEN = 2, 60
_EMPTY_VOCABULARY = "(知识库里还没有笔记)"
# 改写用得上的上文：最近几轮，每轮的回答只取开头这么多字
REWRITE_TURNS = 3
_REWRITE_ANSWER_CHARS = 300
# 改写出来的问题长得离谱时不采用
_REWRITTEN_MAX_CHARS = 300


class Turn(BaseModel):
    """之前的一轮问答。"""

    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(max_length=20000)


class _Keywords(BaseModel):
    keywords: list[str] = Field(default_factory=list)


class _Rewrite(BaseModel):
    question: str = ""
    keywords: list[str] = Field(default_factory=list)


def expand_question(question: str, vocabulary: Sequence[str] = ()) -> list[str]:
    """vocabulary 是库里已有笔记的标题（可带别名），让模型能直接挑出相关的那几条。

    失败时返回空列表。
    """
    prompt = (_PROMPTS / "expand_zh.md").read_text(encoding="utf-8").format(
        question=question.strip(), vocabulary=_listing(vocabulary)
    )
    try:
        out = bind_json(get_expand_model(), _Keywords).invoke([HumanMessage(content=prompt)])
        parsed = out["parsed"] if isinstance(out, dict) else out
        return _clean(parsed.keywords)
    except Exception as exc:
        logger.warning("检索词扩展失败，退回只用原问题检索: %s", exc)
        return []


def rewrite_question(
    question: str, history: Sequence[Turn], vocabulary: Sequence[str] = ()
) -> tuple[str, list[str]]:
    """追问：结合上文把 question 改写成一句不看上文也能懂的话，顺带给出检索词。返回 (改写后的问题, 检索词)。

    失败时把上一轮的问题和这次的问题拼在一起当作检索用的问题——「它和 RLHF 有什么区别」单独拿去检索，
    连「它」是什么都不知道；带上上一问至少方向是对的。
    """
    question = question.strip()
    recent = list(history)[-REWRITE_TURNS:]
    if not recent:
        return question, expand_question(question, vocabulary)
    prompt = (_PROMPTS / "rewrite_zh.md").read_text(encoding="utf-8").format(
        history="\n\n".join(
            f"用户:{turn.question.strip()}\n回答:{_shorten(turn.answer)}" for turn in recent
        ),
        question=question,
        vocabulary=_listing(vocabulary),
    )
    try:
        out = bind_json(get_expand_model(), _Rewrite).invoke([HumanMessage(content=prompt)])
        parsed = out["parsed"] if isinstance(out, dict) else out
        rewritten = " ".join(parsed.question.split())
        if not rewritten or len(rewritten) > _REWRITTEN_MAX_CHARS:
            rewritten = question
        return rewritten, _clean(parsed.keywords)
    except Exception as exc:
        logger.warning("追问的改写失败，退回把上一问和这一问拼起来检索: %s", exc)
        return f"{recent[-1].question.strip()} {question}", []


def _listing(vocabulary: Sequence[str]) -> str:
    return "\n".join(f"- {entry}" for entry in vocabulary) or _EMPTY_VOCABULARY


def _shorten(answer: str) -> str:
    flat = " ".join(answer.split())
    return flat if len(flat) <= _REWRITE_ANSWER_CHARS else flat[:_REWRITE_ANSWER_CHARS] + "…"


def _clean(raw: Sequence[str]) -> list[str]:
    keywords: list[str] = []
    for word in raw:
        word = word.strip()
        if _MIN_LEN <= len(word) <= _MAX_LEN and word.casefold() not in (
            k.casefold() for k in keywords
        ):
            keywords.append(word)
    return keywords[:MAX_KEYWORDS]
