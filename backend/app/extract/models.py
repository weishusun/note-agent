"""提取层的数据模型。与录入层(vault/models.py)刻意分开:

这里描述的是「模型看懂输入后产出的中间物」,还没落盘、还没变成 Note。
NoteDraft 的字段长度约束在这里强制(pydantic 层),越界即 ValidationError;
ClassifyResult 保持宽松,分类的合法性(叶子是否存在、suggested_domain 长度)
由 extractor 做后置校验与重试,不在模型层抛,因为它需要「不合法就重试一次」的语义。
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NoteDraft(BaseModel):
    """一条原子知识点的草稿。只描述输入里真实存在的内容,不含分类信息。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=60)
    summary: str = Field(min_length=1, max_length=50)
    # 1-6 条:条数必须如实反映输入信息量,只说一件事就一条,不为凑数而重复标题
    points: list[str] = Field(min_length=1, max_length=6)
    # 同义说法、英文全称、缩写。导航层的一等公民,尽量给全
    aliases: list[str] = Field(default_factory=list, max_length=5)
    # 输入里提到的其他概念名,用于后续建双链;不是本条的内容
    related: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("points")
    @classmethod
    def _points_each_within_40(cls, v: list[str]) -> list[str]:
        for p in v:
            if len(p) > 40:
                raise ValueError("每条要点 ≤40 字")
        return v


class ClassifyResult(BaseModel):
    """对单条 NoteDraft 的分类结果。与 drafts 一一对应。

    宽松模型:字段可缺省,取值合法性由 extractor 后置校验。
    非法分类会被规整成 category=None 的 suggested_domain 路径。
    """

    model_config = ConfigDict(extra="forbid")

    # taxonomy 叶子路径;非法或无匹配则 None
    category: str | None = None
    # category 为 None 时必填,2-8 字;不进 taxonomy,仅作聚类线索
    suggested_domain: str | None = None
    tags: list[str] = Field(default_factory=list)
    # 分类置信度(不是内容置信度)。category 为 None 时恒为 0.0
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class ExtractResult(BaseModel):
    """一次完整提取的聚合物:草稿 + 各自的分类 + token 消耗。

    由编排层 / evals 组装。两个提取函数本身只各管一段,不返回本类型。
    usage 形如 {"input_tokens": int, "output_tokens": int, "model": str}。
    """

    drafts: list[NoteDraft]
    classifications: list[ClassifyResult]
    usage: dict
