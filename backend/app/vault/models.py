"""数据契约模型，与 docs/SCHEMA.md 2.2 节一一对应。

注意：category 是否存在于 taxonomy 不在这里校验（需要读文件），
由 taxonomy.TaxonomyManager.is_valid_category 返回可判定结果，调用方决定落 inbox。
本模块只做结构性约束。
"""

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Status = Literal["active", "inbox", "archived"]
SourceKind = Literal["screenshot", "text", "url"]


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    ref: str


class NoteFrontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=60)
    category: str | None
    tags: list[str] = Field(max_length=6)
    aliases: list[str]
    summary: str = Field(min_length=1, max_length=50)
    status: Status
    confidence: float = Field(ge=0.0, le=1.0)
    created: datetime
    updated: datetime
    source: SourceRef

    @field_validator("created", "updated")
    @classmethod
    def _require_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("必须是带时区的 ISO8601 时间")
        return v

    @model_validator(mode="after")
    def _category_null_only_when_inbox(self) -> "NoteFrontmatter":
        if self.category is None and self.status != "inbox":
            raise ValueError("category 为 null 时 status 必须是 inbox")
        return self


class Note(BaseModel):
    frontmatter: NoteFrontmatter
    body: str
    # 相对 vault 根的路径，如 notes/RAG 检索增强生成.md；创建/读取后由 store 填充
    path: Path | None = None


class NoteRef(BaseModel):
    path: str
    title: str
    category: str | None
    tags: list[str]
    summary: str
    updated: datetime
    confidence: float

    @classmethod
    def from_note(cls, note: Note) -> "NoteRef":
        if note.path is None:
            raise ValueError("note.path 未设置，无法生成 NoteRef")
        fm = note.frontmatter
        return cls(
            path=note.path.as_posix(),
            title=fm.title,
            category=fm.category,
            tags=fm.tags,
            summary=fm.summary,
            updated=fm.updated,
            confidence=fm.confidence,
        )


class TaxonomyLeaf(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str


class TaxonomyParent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    children: dict[str, TaxonomyLeaf]


class Taxonomy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    updated: str
    categories: dict[str, TaxonomyParent]
    tags: list[str]

    @field_validator("updated", mode="before")
    @classmethod
    def _date_to_str(cls, v: object) -> str:
        # yaml 会把 2026-07-30 解析成 date 对象
        return str(v)
