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
SourceKind = Literal["screenshot", "text", "url", "file", "generated"]


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    ref: str


class NoteFrontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 笔记的稳定标识符，创建时生成、永不变更（SCHEMA 2.2）。path 是位置不是身份：
    # 用户在 Obsidian 里改标题会重命名文件，path 变，id 不变。
    # 为 null 只允许一种情形：用户直接在 Obsidian 手建的笔记。
    # 读取时不得为无 id 的笔记补写 id，那是修改已有笔记，违反不变量 5。
    # id 由 store.create_note 在写入层生成，模型层不生成，否则读取无 id 的笔记
    # 会被静默补写：default_factory 会在解析时当场捏一个新 ULID，读两次得到两个值。
    id: str | None = None
    title: str = Field(min_length=1, max_length=60)
    category: str | None
    # 全系统唯一允许模型自由生成的字段，不进 taxonomy，只作聚类线索（SCHEMA 3.1）
    suggested_domain: str | None
    tags: list[str] = Field(max_length=6)
    aliases: list[str]
    summary: str = Field(min_length=1, max_length=50)
    status: Status
    verified: bool
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
    def _cross_field_rules(self) -> "NoteFrontmatter":
        if self.category is None:
            if self.status != "inbox":
                raise ValueError("category 为 null 时 status 必须是 inbox")
            if self.suggested_domain is None:
                raise ValueError("category 为 null 时 suggested_domain 必填")
            if not 2 <= len(self.suggested_domain) <= 8:
                raise ValueError("suggested_domain 必须是 2–8 字")
        elif self.suggested_domain is not None:
            raise ValueError("category 有值时 suggested_domain 必须为 null")
        # verified 与 source.kind 相互独立，不得互相推导（SCHEMA 2.2）。
        # 「创建时一律 false」由 store.create_note 在写入层强制，模型层允许 True，
        # 因为读回已人工核实的笔记时需要
        return self


class Note(BaseModel):
    frontmatter: NoteFrontmatter
    body: str
    # 相对 vault 根的路径，如 notes/RAG 检索增强生成.md；创建/读取后由 store 填充
    path: Path | None = None


class NoteRef(BaseModel):
    # null 表示 Obsidian 手建、无 id 的笔记（SCHEMA 第 5 节）
    id: str | None
    path: str
    title: str
    category: str | None
    suggested_domain: str | None
    tags: list[str]
    summary: str
    status: Status
    verified: bool
    updated: datetime
    confidence: float

    @classmethod
    def from_note(cls, note: Note) -> "NoteRef":
        if note.path is None:
            raise ValueError("note.path 未设置，无法生成 NoteRef")
        fm = note.frontmatter
        return cls(
            id=fm.id,
            path=note.path.as_posix(),
            title=fm.title,
            category=fm.category,
            suggested_domain=fm.suggested_domain,
            tags=fm.tags,
            summary=fm.summary,
            status=fm.status,
            verified=fm.verified,
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
