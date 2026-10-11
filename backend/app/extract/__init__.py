"""提取层:把用户输入(截图/短文本)看懂、拆成草稿(extractor),再给草稿归类(classifier)。

纯读+算,不写 vault。落盘与编排在 app/ingest,分类树的维护在 app/organize。
"""

from app.extract.classifier import NoteBrief, Placement, classify_notes
from app.extract.extractor import ExtractError, extract_drafts
from app.extract.models import NoteDraft

__all__ = [
    "ExtractError",
    "NoteBrief",
    "NoteDraft",
    "Placement",
    "classify_notes",
    "extract_drafts",
]
