"""提取层的数据模型。与落盘的模型(vault/models.py)刻意分开:

这里描述的是「模型看懂输入后产出的中间物」,还没落盘、还没变成 Note。
字段长度约束在这里强制;模型原样返回的内容由 extractor._fit 收进这些上限。
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

TITLE_MAX = 60
# 真实接口上模型写的概要普遍在 50–70 字，卡 50 会频繁触发压缩和截断
SUMMARY_MAX = 80
POINT_MAX = 40
POINTS_MAX = 6
ALIASES_MAX = 5
RELATED_MAX = 5
# 一份输入最多拆出几条概念笔记
CONCEPTS_MAX = 8
# 概念笔记的模型补充:最多几条、每条多长
SUPPLEMENT_MAX = 4
SUPPLEMENT_POINT_MAX = 80
# 主题笔记:内容是保留原结构的 Markdown,补充是一小段话
TOPIC_CONTENT_MAX = 4000
TOPIC_SUPPLEMENT_MAX = 300


class NoteDraft(BaseModel):
    """一条概念笔记的草稿。supplement 以外的字段只描述输入里真实存在的内容。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=TITLE_MAX)
    summary: str = Field(min_length=1, max_length=SUMMARY_MAX)
    # 1-6 条:条数必须如实反映输入信息量,只说一件事就一条,不为凑数而重复标题
    points: list[str] = Field(min_length=1, max_length=POINTS_MAX)
    # 同义说法、英文全称、缩写。导航层的一等公民,尽量给全
    aliases: list[str] = Field(default_factory=list, max_length=ALIASES_MAX)
    # 输入里提到的其他概念名,用于后续建双链;不是本条的内容
    related: list[str] = Field(default_factory=list, max_length=RELATED_MAX)
    # 模型用自身知识补充的内容,落盘时单独放进「模型补充」区块(SCHEMA 不变量 14)
    supplement: list[str] = Field(default_factory=list, max_length=SUPPLEMENT_MAX)

    @field_validator("points")
    @classmethod
    def _points_each_within_40(cls, v: list[str]) -> list[str]:
        for p in v:
            if len(p) > POINT_MAX:
                raise ValueError(f"每条要点 ≤{POINT_MAX} 字")
        return v


class TopicDraft(BaseModel):
    """一条主题笔记的草稿:把一份输入作为整体记下来,保留它原来的结构。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=TITLE_MAX)
    summary: str = Field(min_length=1, max_length=SUMMARY_MAX)
    # 保留原结构的 Markdown:表格、带箭头的有序列表、嵌套列表
    content: str = Field(min_length=1, max_length=TOPIC_CONTENT_MAX)
    supplement: str = Field(default="", max_length=TOPIC_SUPPLEMENT_MAX)


class Extraction(BaseModel):
    """一份输入的提取结果(SCHEMA 2.3):只讲一个概念时 topic 为 None。"""

    topic: TopicDraft | None = None
    concepts: list[NoteDraft] = Field(default_factory=list, max_length=CONCEPTS_MAX)
