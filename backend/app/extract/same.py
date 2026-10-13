"""判断两条笔记讲的是不是同一个概念（SCHEMA 2.5）。

向量相似度只能说明「内容接近」：同一个概念的两种写法和一对上下游的概念，相似度只差几个点，
靠阈值分不开。所以向量只负责找出候选，是不是重复由文本模型逐对判断。
"""

from dataclasses import dataclass

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, field_validator

from app.extract.client import bind_json, get_classify_model
from app.extract.extractor import ExtractError, _invoke_structured, _load_prompt, _model_id

_TEXT_CHARS = 400


@dataclass(frozen=True)
class Card:
    """判断时给模型看的一条笔记：名字、别名、概要，加一小段正文。"""

    title: str
    aliases: tuple[str, ...] = ()
    summary: str = ""
    text: str = ""

    def render(self) -> str:
        lines = [f"标题:{self.title}"]
        if self.aliases:
            lines.append("别名:" + "、".join(self.aliases))
        if self.summary:
            lines.append(f"概要:{self.summary}")
        if self.text.strip():
            lines.append("内容:" + " ".join(self.text.split())[:_TEXT_CHARS])
        return "\n".join(lines)


class _Verdicts(BaseModel):
    same: list[bool] = Field(default_factory=list)

    @field_validator("same", mode="before")
    @classmethod
    def _loose(cls, value: object) -> object:
        # 模型偶尔把布尔写成 "true" / "是"
        if isinstance(value, list):
            return [v.strip().lower() in ("true", "是", "yes") if isinstance(v, str) else v for v in value]
        return value


def judge_same_concept(pairs: list[tuple[Card, Card]]) -> list[bool]:
    """逐对判断 (新笔记, 已有笔记) 是不是同一个概念，返回与 pairs 等长的列表。

    模型返回的个数对不上时抛 ExtractError——调用方当作「都不是重复」处理，不让录入失败。
    """
    if not pairs:
        return []
    listing = "\n\n".join(
        f"## 第 {i} 对\n【新】\n{new.render()}\n【旧】\n{old.render()}" for i, (new, old) in enumerate(pairs, 1)
    )
    prompt = _load_prompt("same_concept_zh.md").format(count=len(pairs), pairs=listing)
    model = get_classify_model()
    verdicts, _usage = _invoke_structured(
        bind_json(model, _Verdicts), [HumanMessage(content=prompt)], _model_id(model)
    )
    if len(verdicts.same) != len(pairs):
        raise ExtractError(f"判断重复时模型返回了 {len(verdicts.same)} 个结果，应该是 {len(pairs)} 个")
    return verdicts.same
