"""「我的导图」：使用者自己编辑的思维导图（SCHEMA §8）。

和「目录导图」（按分类实时生成、不落盘）不同，这里的导图是使用者的作品，
一张一个 JSON 文件，放在 vault 的 mindmaps/ 下。节点可以自由摆放，位置存在文件里。
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

TITLE_MAX = 80
NODE_TEXT_MAX = 200
NODE_NOTE_MAX = 5000
NODES_MAX = 2000
# 节点颜色是调色板里的名字，不存色值：深色、浅色模式下各自取色
Color = Literal["blue", "teal", "green", "amber", "orange", "red", "pink", "violet"]

_MAP_ID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_NODE_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class MindmapLink(BaseModel):
    """节点链到的笔记。按 id 认（改名不受影响）；title 是链上时的标题，笔记找不到时显示它。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=40)
    title: str = Field(default="", max_length=TITLE_MAX * 2)


class MindmapNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="", max_length=NODE_TEXT_MAX)
    children: list[str] = Field(default_factory=list)
    # 节点左上角在画布上的位置
    x: float = 0
    y: float = 0
    color: Color | None = None
    # 备注：长一点的文字，点开才显示
    note: str | None = Field(default=None, max_length=NODE_NOTE_MAX)
    link: MindmapLink | None = None
    collapsed: bool = False


class MindmapBody(BaseModel):
    """导图的内容（新建、保存时前端发来的部分）。必须是一棵以 root 为根的树。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=TITLE_MAX)
    root: str
    nodes: dict[str, MindmapNode] = Field(max_length=NODES_MAX)

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("导图要有名字")
        return v

    @model_validator(mode="after")
    def _is_a_tree(self) -> "MindmapBody":
        bad = [node_id for node_id in self.nodes if not _NODE_ID.match(node_id)]
        if bad:
            raise ValueError(f"节点 id 不合法: {bad[:3]}")
        if self.root not in self.nodes:
            raise ValueError("根节点不存在")
        seen: set[str] = set()
        stack = [self.root]
        while stack:
            node_id = stack.pop()
            if node_id in seen:
                raise ValueError(f"节点 {node_id} 出现了不止一次（导图必须是一棵树）")
            seen.add(node_id)
            for child in self.nodes[node_id].children:
                if child not in self.nodes:
                    raise ValueError(f"节点 {node_id} 的子节点 {child} 不存在")
                stack.append(child)
        orphans = set(self.nodes) - seen
        if orphans:
            raise ValueError(f"有节点不在树上: {sorted(orphans)[:3]}")
        return self


class Mindmap(MindmapBody):
    version: Literal[1] = 1
    id: str
    created: datetime
    updated: datetime


class MindmapSummary(BaseModel):
    id: str
    title: str
    updated: datetime
    node_count: int


class MindmapStore:
    def __init__(self, vault_root: Path) -> None:
        self.dir = Path(vault_root) / "mindmaps"

    def _path(self, map_id: str) -> Path:
        # id 是 ULID：只认这个格式，防止拼出 mindmaps/ 以外的路径
        if not _MAP_ID.match(map_id):
            raise ValueError("非法的导图 id")
        return self.dir / f"{map_id}.json"

    def list(self) -> list[MindmapSummary]:
        """最近改过的在前。读不了的文件跳过并记日志。"""
        if not self.dir.is_dir():
            return []
        out = []
        for path in self.dir.glob("*.json"):
            try:
                m = self.get(path.stem)
            except (OSError, ValueError) as exc:
                logger.warning("读不了导图 %s: %s", path.name, exc)
                continue
            out.append(MindmapSummary(id=m.id, title=m.title, updated=m.updated, node_count=len(m.nodes)))
        return sorted(out, key=lambda s: s.updated, reverse=True)

    def get(self, map_id: str) -> Mindmap:
        with open(self._path(map_id), encoding="utf-8") as f:
            return Mindmap.model_validate(json.load(f))

    def save(self, mindmap: Mindmap) -> Mindmap:
        target = self._path(mindmap.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(mindmap.model_dump(mode="json"), ensure_ascii=False, indent=1) + "\n"
        tmp = target.with_name(f"{target.name}.{os.getpid()}.{uuid4().hex}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.replace(tmp, target)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return mindmap

    def delete(self, map_id: str) -> None:
        self._path(map_id).unlink()
