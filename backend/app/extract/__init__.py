"""提取层:把用户输入(截图/短文本)看懂、拆成原子草稿、给出分类建议。

纯读+算,不写 vault、不接 LangGraph。落盘与编排是后续阶段的事。
"""

from app.extract.extractor import ExtractError, classify, extract_drafts
from app.extract.models import ClassifyResult, ExtractResult, NoteDraft

__all__ = [
    "ExtractError",
    "classify",
    "extract_drafts",
    "ClassifyResult",
    "ExtractResult",
    "NoteDraft",
]
