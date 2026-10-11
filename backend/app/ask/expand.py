"""把问题扩展成检索词，弥补纯字面检索「换个说法就搜不到」的缺口。"""

import logging
from collections.abc import Sequence
from pathlib import Path

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.extract.client import bind_json, get_expand_model

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "expand_zh.md"
MAX_KEYWORDS = 8
# 上限放到标题的最大长度：模型会把库里已有的笔记标题原样当检索词返回
_MIN_LEN, _MAX_LEN = 2, 60
_EMPTY_VOCABULARY = "(知识库里还没有笔记)"


class _Keywords(BaseModel):
    keywords: list[str] = Field(default_factory=list)


def expand_question(question: str, vocabulary: Sequence[str] = ()) -> list[str]:
    """vocabulary 是库里已有笔记的标题（可带别名），让模型能直接挑出相关的那几条。

    失败时返回空列表：扩展只是锦上添花，不能因为它让整次问答失败。
    """
    prompt = _PROMPT_PATH.read_text(encoding="utf-8").format(
        question=question.strip(),
        vocabulary="\n".join(f"- {entry}" for entry in vocabulary) or _EMPTY_VOCABULARY,
    )
    try:
        out = bind_json(get_expand_model(), _Keywords).invoke([HumanMessage(content=prompt)])
        parsed = out["parsed"] if isinstance(out, dict) else out
        raw = parsed.keywords
    except Exception as exc:
        logger.warning("检索词扩展失败，退回只用原问题检索: %s", exc)
        return []

    keywords: list[str] = []
    for word in raw:
        word = word.strip()
        if _MIN_LEN <= len(word) <= _MAX_LEN and word.casefold() not in (
            k.casefold() for k in keywords
        ):
            keywords.append(word)
    return keywords[:MAX_KEYWORDS]
