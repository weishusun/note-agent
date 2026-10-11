"""数据契约模型，与 docs/SCHEMA.md 2.2 节（笔记）和 3.1 节（分类树）一一对应。

本模块只做结构性约束。分类是否登记在 taxonomy.yaml 不在这里校验（需要读文件）。
"""

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Status = Literal["active", "inbox", "archived"]
SourceKind = Literal["screenshot", "text", "url", "file", "generated"]

CATEGORY_NAME_MAX = 12
CATEGORY_SEP = "/"


def clean_category_name(name: str) -> str:
    """按 SCHEMA 3.1 的命名规则规整一层分类名，不合规抛 ValueError。"""
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("分类名不能为空")
    if CATEGORY_SEP in cleaned:
        raise ValueError(f"分类名里不能有「{CATEGORY_SEP}」: {cleaned}")
    if len(cleaned) > CATEGORY_NAME_MAX:
        raise ValueError(f"分类名最长 {CATEGORY_NAME_MAX} 个字: {cleaned}")
    return cleaned


def split_category(path: str) -> tuple[str, str]:
    """把「大块/小块」拆成两段并各自规整。不是恰好两段就抛 ValueError。"""
    parts = (path or "").split(CATEGORY_SEP)
    if len(parts) != 2:
        raise ValueError(f"分类必须是「大块{CATEGORY_SEP}小块」两层: {path!r}")
    return clean_category_name(parts[0]), clean_category_name(parts[1])


def join_category(parent: str, child: str) -> str:
    return f"{parent}{CATEGORY_SEP}{child}"


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    ref: str


class NoteFrontmatter(BaseModel):
    # ignore 而不是 forbid：v3 及以前的笔记带着 suggested_domain / verified 两个旧键，
    # 读取时忽略，下次改写时自然消失（SCHEMA 2.2「旧字段的兼容」）
    model_config = ConfigDict(extra="ignore")

    # 笔记的稳定标识符，创建时生成、永不变更。由 store.create_note 在写入层生成：
    # 模型层给默认值会在读取无 id 的笔记时当场捏一个新的，读两次得到两个值。
    id: str | None = None
    title: str = Field(min_length=1, max_length=60)
    category: str | None
    # 使用者手动指定过分类：重新归类必须跳过（SCHEMA 3.3）
    category_locked: bool = False
    tags: list[str] = Field(default_factory=list, max_length=6)
    aliases: list[str] = Field(default_factory=list)
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
    def _cross_field_rules(self) -> "NoteFrontmatter":
        if self.category is None:
            if self.status != "inbox":
                raise ValueError("category 为 null 时 status 必须是 inbox")
        else:
            # 这里只要求是两段非空；长度等命名规则在写入路径上校验，
            # 读取时从宽——不能因为手工改出的一个长名字就读不了整条笔记
            parts = self.category.split(CATEGORY_SEP)
            if len(parts) != 2 or not all(p.strip() for p in parts):
                raise ValueError(f"category 必须是「大块{CATEGORY_SEP}小块」两层")
        return self


class Note(BaseModel):
    frontmatter: NoteFrontmatter
    body: str
    # 相对 vault 根的路径，如 notes/RAG 检索增强生成.md；创建/读取后由 store 填充
    path: Path | None = None


class NoteRef(BaseModel):
    # null 表示手工放进 vault、无 id 的笔记（SCHEMA 第 5 节）
    id: str | None
    path: str
    title: str
    category: str | None
    category_locked: bool
    tags: list[str]
    summary: str
    status: Status
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
            category_locked=fm.category_locked,
            tags=fm.tags,
            summary=fm.summary,
            status=fm.status,
            updated=fm.updated,
            confidence=fm.confidence,
        )


class TaxonomyChild(BaseModel):
    """小块。extra=ignore：兼容 v1 格式里的 label 键。"""

    model_config = ConfigDict(extra="ignore")

    locked: bool = False


class TaxonomyParent(BaseModel):
    """大块。"""

    model_config = ConfigDict(extra="ignore")

    locked: bool = False
    children: dict[str, TaxonomyChild] = Field(default_factory=dict)

    @field_validator("children", mode="before")
    @classmethod
    def _none_children(cls, v: object) -> object:
        # yaml 里「小块名:」后面什么都不写会解析成 None
        if isinstance(v, dict):
            return {name: ({} if node is None else node) for name, node in v.items()}
        return {} if v is None else v


class Taxonomy(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int = 2
    updated: str = ""
    categories: dict[str, TaxonomyParent] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

    @field_validator("updated", mode="before")
    @classmethod
    def _date_to_str(cls, v: object) -> str:
        # yaml 会把 2026-07-30 解析成 date 对象
        return str(v)

    @field_validator("categories", mode="before")
    @classmethod
    def _none_nodes(cls, v: object) -> object:
        if isinstance(v, dict):
            return {name: ({} if node is None else node) for name, node in v.items()}
        return {} if v is None else v

    def leaves(self) -> list[str]:
        return [
            join_category(parent, child)
            for parent, node in self.categories.items()
            for child in node.children
        ]


class TreeChild(BaseModel):
    name: str
    path: str
    locked: bool
    count: int


class TreeParent(BaseModel):
    name: str
    locked: bool
    count: int
    children: list[TreeChild]


class Tree(BaseModel):
    """GET /api/tree 的响应（SCHEMA 第 5 节）。"""

    total: int
    inbox: int
    categories: list[TreeParent]
