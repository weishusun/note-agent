"""自动归类：给一批笔记各定一个「大块/小块」（SCHEMA 3.2）。

一次模型调用处理一整批，模型能看到当前整棵分类树，可以沿用已有分类，也可以新建。
本模块只负责问模型并把回答规整成结构；名字与已有分类对齐、登记新分类、决定进不进待整理，
都是调用方（app/organize）的事。
"""

import logging

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.extract.client import bind_json, get_classify_model
from app.extract.extractor import _invoke_structured, _load_prompt, _model_id
from app.vault.models import Tree, clean_category_name, join_category

logger = logging.getLogger(__name__)


class NoteBrief(BaseModel):
    """归类时给模型看的笔记摘要。"""

    title: str
    summary: str
    points: list[str] = Field(default_factory=list)


class Placement(BaseModel):
    """一条笔记的归类结果。parent / child 为 None 表示没分上。"""

    parent: str | None = None
    child: str | None = None
    confidence: float = 0.0

    @property
    def category(self) -> str | None:
        if self.parent is None or self.child is None:
            return None
        return join_category(self.parent, self.child)


class _WireItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int
    parent: str = ""
    child: str = ""
    confidence: float = 0.0

    @field_validator("parent", "child", mode="before")
    @classmethod
    def _none_to_empty(cls, v: object) -> object:
        return "" if v is None else v

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, v: object) -> float:
        try:
            return min(1.0, max(0.0, float(v)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0


class _WireBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[_WireItem] = Field(default_factory=list)


def classify_notes(briefs: list[NoteBrief], tree: Tree) -> list[Placement]:
    """返回与 briefs 等长、顺序一致的归类结果。

    模型漏掉某几条时带着「漏了哪几条」重问一次；还是没有的那几条返回空 Placement，
    不让一条漏答拖垮整批。模型调用本身失败（解析不了、超时）则抛异常，由调用方兜底。
    """
    if not briefs:
        return []
    model = get_classify_model()
    runnable = bind_json(model, _WireBatch)
    model_id = _model_id(model)
    messages = [HumanMessage(content=_prompt(briefs, tree))]

    batch, _usage = _invoke_structured(runnable, messages, model_id)
    answered = _by_index(batch, len(briefs))
    missing = [i for i in range(1, len(briefs) + 1) if i not in answered]
    if missing:
        hint = (
            f"你上一次的输出漏掉了第 {'、'.join(map(str, missing))} 条笔记。"
            f"请重新输出全部 {len(briefs)} 条,items 里每条笔记恰好出现一次。"
        )
        batch, _usage = _invoke_structured(
            runnable, [*messages, HumanMessage(content=hint)], model_id
        )
        answered = {**answered, **_by_index(batch, len(briefs))}

    return [_placement(answered.get(i)) for i in range(1, len(briefs) + 1)]


def _by_index(batch: BaseModel, count: int) -> dict[int, _WireItem]:
    """同一个 index 出现多次时取第一次；范围外的丢弃。"""
    answered: dict[int, _WireItem] = {}
    for item in batch.items:  # type: ignore[attr-defined]
        if 1 <= item.index <= count and item.index not in answered:
            answered[item.index] = item
    return answered


def _placement(item: _WireItem | None) -> Placement:
    if item is None:
        return Placement()
    try:
        return Placement(
            parent=clean_category_name(item.parent),
            child=clean_category_name(item.child),
            confidence=item.confidence,
        )
    except ValueError as exc:
        logger.warning("模型给的分类名不合规，这条进待整理: %s", exc)
        return Placement()


def _prompt(briefs: list[NoteBrief], tree: Tree) -> str:
    return _load_prompt("classify_tree_zh.md").format(
        tree=_render_tree(tree), notes=_render_notes(briefs), count=len(briefs)
    )


def _render_tree(tree: Tree) -> str:
    if not tree.categories:
        return "(还没有任何分类,由你来建第一批)"
    lines = []
    for parent in tree.categories:
        children = "、".join(f"{c.name}({c.count} 条)" for c in parent.children) or "(还没有小块)"
        lines.append(f"- {parent.name}: {children}")
    return "\n".join(lines)


def _render_notes(briefs: list[NoteBrief]) -> str:
    blocks = []
    for i, brief in enumerate(briefs, 1):
        lines = [f"{i}. 标题:{brief.title}", f"   概要:{brief.summary}"]
        if brief.points:
            lines.append("   要点:" + ";".join(brief.points))
        blocks.append("\n".join(lines))
    return "\n".join(blocks)
