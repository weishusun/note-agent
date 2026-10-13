"""合并两条笔记时,让文本模型对要点和模型补充做语义去重、重写概要(SCHEMA 2.6)。

只管问模型。区块怎么拼、结果要不要采用,由 app/organize/merge.py 决定;这里失败就抛异常,
调用方会退回逐字去重的简单拼接。
"""

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.extract.client import bind_json, get_classify_model
from app.extract.extractor import ExtractError, _as_list, _invoke_structured, _load_prompt, _model_id
from app.extract.models import SUMMARY_MAX

_NONE = "(无)"


class _Merged(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    points: list[str] = Field(default_factory=list)
    supplement: list[str] = Field(default_factory=list)

    _lists = field_validator("points", "supplement", mode="before")(_as_list)


def tidy_merge(
    summary_a: str, summary_b: str, points: list[str], supplement: list[str]
) -> tuple[str, list[str], list[str]]:
    """返回 (概要, 要点, 模型补充)。模型的输出不像样(没有要点、概要为空或超长)时抛 ExtractError。"""
    prompt = _load_prompt("merge_zh.md").format(
        summary_a=summary_a,
        summary_b=summary_b,
        points="\n".join(f"- {p}" for p in points),
        supplement="\n".join(f"- {s}" for s in supplement) or _NONE,
    )
    model = get_classify_model()
    merged, _usage = _invoke_structured(
        bind_json(model, _Merged), [HumanMessage(content=prompt)], _model_id(model)
    )
    summary = " ".join(merged.summary.split())
    kept_points = [p.strip() for p in merged.points if p.strip()]
    kept_supplement = [s.strip() for s in merged.supplement if s.strip()]
    if not kept_points or not summary or len(summary) > SUMMARY_MAX:
        raise ExtractError("合并时模型返回的内容不可用")
    if not supplement:
        # 原来没有模型补充,就不许凭空多出来
        kept_supplement = []
    return summary, kept_points, kept_supplement
