"""模型补充：用模型自己的知识，给一条笔记写一段详细的补充（SCHEMA 2.1「模型补充」区块）。

从提取里单独拆出来的一步：提取那一次调用要同时管十来个字段，补充只能挤成几条短句，
超长还会被截断。这里专门调一次，写 600–1000 字、结构自由的 Markdown，不截断。
只管问模型、整理输出；写进哪条笔记由 app/organize/supplement.py 负责。
"""

import logging
import re

from langchain_core.messages import HumanMessage

from app.extract.client import get_supplement_model
from app.extract.extractor import _load_prompt

logger = logging.getLogger(__name__)

# 提示词要的是 600–1000 字。短于下限多半是模型没好好写；
# 上限远高于目标，只防失控——超了在段落边界处截断，不加省略号
SUPPLEMENT_MIN_CHARS = 150
SUPPLEMENT_MAX_CHARS = 3000
# 笔记正文太长时只给模型看前面这些（长文档的主题笔记有摘要和大纲，够它了解在讲什么）
_BODY_MAX_CHARS = 6000

_KIND = {"topic": "主题", "concept": "概念"}
_FENCE = re.compile(r"^```(?:markdown|md)?\s*\n(.*)\n```\s*$", re.S)
_HEADING = re.compile(r"^#{1,6}\s+")
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
# 模型偶尔会把总标题、说明行或开场白也写出来
_PREAMBLE = re.compile(r"^(#+\s*)?(模型补充|以下(内容|是)|好的[，,。])")
# 界面不渲染 LaTeX：提示词要求别用，漏网的换成代码。$$…$$ / \[…\] 独占几行的是公式块，
# $…$ / \(…\) 是行内公式（两头紧贴非空白，免得把「$5 到 $10」这种当成公式）
_MATH_BLOCK = re.compile(r"^([ \t]*)(?:\$\$|\\\[)[ \t]*\n(.*?)\n[ \t]*(?:\$\$|\\\])[ \t]*$", re.M | re.S)
_MATH_INLINE = re.compile(r"\$\$(\S(?:[^$\n]*?\S)?)\$\$|\$(\S(?:[^$\n]*?\S)?)\$|\\\((.+?)\\\)")


class SupplementError(RuntimeError):
    pass


def write_supplement(
    title: str, summary: str, body: str, *, note_type: str = "concept", aliases: list[str] | None = None
) -> str:
    """返回补充的 Markdown 正文（不含「## 模型补充」标题和说明行）。模型调用失败或写得不像样时抛异常。"""
    if len(body) > _BODY_MAX_CHARS:
        body = body[:_BODY_MAX_CHARS] + "\n……（后面还有，略）"
    prompt = _load_prompt("supplement_zh.md").format(
        kind=_KIND.get(note_type, "概念"),
        title=title,
        aliases=f"别名：{'、'.join(aliases)}\n" if aliases else "",
        summary=summary,
        body=body.strip() or "（正文为空）",
    )
    reply = get_supplement_model().invoke([HumanMessage(content=prompt)])
    content = reply.content if isinstance(reply.content, str) else str(reply.content)
    return clean_supplement(content)


def clean_supplement(text: str) -> str:
    """把模型的输出收拾成能直接放进「模型补充」区块的 Markdown。"""
    text = text.replace("\r\n", "\n").strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()

    text = _MATH_BLOCK.sub(
        lambda m: f"{m.group(1)}```\n{m.group(1)}{m.group(2).strip()}\n{m.group(1)}```", text
    )
    text = _MATH_INLINE.sub(lambda m: f"`{next(g for g in m.groups() if g)}`", text)

    lines = text.split("\n")
    while lines and (not lines[0].strip() or _PREAMBLE.match(lines[0].strip()) or lines[0].lstrip().startswith(">")):
        lines.pop(0)
    cleaned = []
    for line in lines:
        # 区块本身是二级标题：里面的标题一律降成三级，免得把笔记切开
        if _HEADING.match(line):
            line = "### " + _HEADING.sub("", line, count=1)
        # 补充里不建双链：模型不知道知识库里有哪些笔记，链出去多半是断的
        line = _WIKILINK.sub(lambda m: m.group(2) or m.group(1), line)
        cleaned.append(line.rstrip())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned)).strip()

    if len(text) < SUPPLEMENT_MIN_CHARS:
        raise SupplementError(f"模型写的补充太短（{len(text)} 字）：{text[:100]}")
    if len(text) > SUPPLEMENT_MAX_CHARS:
        cut = text.rfind("\n\n", 0, SUPPLEMENT_MAX_CHARS)
        logger.warning("模型补充有 %d 字，在第 %d 字处的段落边界截断", len(text), cut)
        text = text[: cut if cut > SUPPLEMENT_MIN_CHARS else SUPPLEMENT_MAX_CHARS].rstrip()
    return text
