"""笔记之间的链接关系（SCHEMA 第 5 节 GET /api/graph）：关系图谱和思维导图都从这里取数据。

连线来自正文里的 [[双链]]，索引在 links 表里（一条笔记写了哪些文件名）。这里把文件名对回笔记，
两个方向并成一条无向的连线。
"""

from pathlib import PurePosixPath

from pydantic import BaseModel

from app.search.index import SearchIndex
from app.vault.models import NoteType, Status


class GraphNode(BaseModel):
    # path 是节点的键：旧笔记可能没有 id
    path: str
    id: str | None
    title: str
    type: NoteType
    category: str | None
    status: Status
    # 不在所选分类里、只是和里面的某条笔记有链接。没选分类时全为 False
    outside: bool = False


class GraphEdge(BaseModel):
    source: str
    target: str


class Graph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


def note_graph(index: SearchIndex, category: str | None = None) -> Graph:
    """category 是「大块」或「大块/小块」时，只取其中 status=active 的笔记，外加和它们直接相连的块外笔记
    （标 outside，块外笔记彼此之间的连线不取）。不给 category：全部未归档的笔记和它们之间的连线。
    """
    with index.connect() as conn:
        rows = conn.execute(
            "SELECT path, id, type, title, category, status FROM notes"
            " WHERE status != 'archived' ORDER BY path"
        ).fetchall()
        links = conn.execute("SELECT source, target FROM links").fetchall()

    notes = {
        path: GraphNode(path=path, id=note_id, type=note_type, title=title, category=cat, status=status)
        for path, note_id, note_type, title, cat, status in rows
    }
    # 链接里写的是文件名。Windows 上文件名不分大小写，按 casefold 对齐
    by_stem = {PurePosixPath(path).stem.casefold(): path for path in notes}

    pairs: set[tuple[str, str]] = set()
    for source, target in links:
        other = by_stem.get(target.casefold())
        if source in notes and other is not None and other != source:
            pairs.add((source, other) if source < other else (other, source))

    if category:
        inside = {path for path, node in notes.items() if _in_scope(node, category)}
        pairs = {pair for pair in pairs if pair[0] in inside or pair[1] in inside}
        shown = inside | {path for pair in pairs for path in pair}
    else:
        inside = shown = set(notes)

    return Graph(
        nodes=[
            notes[path].model_copy(update={"outside": path not in inside}) for path in sorted(shown)
        ],
        edges=[GraphEdge(source=a, target=b) for a, b in sorted(pairs)],
    )


def _in_scope(node: GraphNode, category: str) -> bool:
    """与 GET /api/notes?category= 同一套规则：只算 active 的；给大块时含它下面所有小块。"""
    if node.status != "active" or node.category is None:
        return False
    return node.category == category or (
        "/" not in category and node.category.startswith(category + "/")
    )
