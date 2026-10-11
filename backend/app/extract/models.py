"""提取层的数据模型。与落盘的模型(vault/models.py)刻意分开:

这里描述的是「模型看懂输入后产出的中间物」,还没落盘、还没变成 Note。
NoteDraft 的字段长度约束在这里强制;模型原样返回的内容由 extractor._fit_drafts 收进这些上限。
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator


TITLE_MAX = 60
SUMMARY_MAX = 50
POINT_MAX = 40
POINTS_MAX = 6
ALIASES_MAX = 5
RELATED_MAX = 5
DRAFTS_MAX = 5


class NoteDraft(BaseModel):
    """一条原子知识点的草稿。只描述输入里真实存在的内容,不含分类信息。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=TITLE_MAX)
    summary: str = Field(min_length=1, max_length=SUMMARY_MAX)
    # 1-6 条:条数必须如实反映输入信息量,只说一件事就一条,不为凑数而重复标题
    points: list[str] = Field(min_length=1, max_length=POINTS_MAX)
    # 同义说法、英文全称、缩写。导航层的一等公民,尽量给全
    aliases: list[str] = Field(default_factory=list, max_length=ALIASES_MAX)
    # 输入里提到的其他概念名,用于后续建双链;不是本条的内容
    related: list[str] = Field(default_factory=list, max_length=RELATED_MAX)

    @field_validator("points")
    @classmethod
    def _points_each_within_40(cls, v: list[str]) -> list[str]:
        for p in v:
            if len(p) > POINT_MAX:
                raise ValueError(f"每条要点 ≤{POINT_MAX} 字")
        return v
