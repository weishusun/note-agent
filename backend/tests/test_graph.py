"""笔记之间的链接：links 表的维护、GET /api/graph。"""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.organize.editor import NoteEditor
from app.organize.service import Organizer
from app.search.graph import note_graph
from app.search.index import SCHEMA_VERSION, SearchIndex, link_targets
from app.vault.store import VaultStore
from tests.conftest import make_note


def _add(store: VaultStore, index: SearchIndex, title: str, body: str = "正文\n", **kw):
    note = make_note(title=title, aliases=[], body=body, **kw)
    store.create_note(note)
    index.upsert_note(note)
    return note


def _links(index: SearchIndex) -> list[tuple[str, str]]:
    with index.connect() as conn:
        return sorted(conn.execute("SELECT source, target FROM links").fetchall())


def _edges(graph) -> list[tuple[str, str]]:
    return [(e.source, e.target) for e in graph.edges]


def _nodes(graph) -> dict[str, bool]:
    return {n.path: n.outside for n in graph.nodes}


# ---------- 从正文里取链接 ----------


def test_link_targets_reads_wiki_links_but_not_embeds() -> None:
    body = (
        "## 关联\n- [[SFT]]\n- [[SFT|监督微调阶段]]\n- [[ 预训练 ]]\n"
        "见 [[RLHF]] 和 [[SFT]]。\n\n## 原始输入\n![[assets/a.png]]\n全文：![[assets/讲义.pdf]]\n[[]]\n"
    )
    assert link_targets(body) == ["SFT", "预训练", "RLHF"]


# ---------- links 表跟着笔记走 ----------


def test_links_follow_upsert_remove_and_rebuild(store: VaultStore, index: SearchIndex) -> None:
    topic = _add(store, index, "训练流程", body="## 概念\n- [[SFT]]\n- [[RLHF]]\n")
    _add(store, index, "SFT", body="## 关联\n- [[训练流程]]\n")
    assert _links(index) == [
        ("notes/SFT.md", "训练流程"),
        ("notes/训练流程.md", "RLHF"),
        ("notes/训练流程.md", "SFT"),
    ]

    # 正文改了：这条笔记的链接整体换掉
    index.upsert_note(store.rewrite_note(topic.path, body="## 概念\n- [[SFT]]\n- [[预训练]]\n"))
    assert ("notes/训练流程.md", "RLHF") not in _links(index)
    assert ("notes/训练流程.md", "预训练") in _links(index)

    index.remove_note("notes/SFT.md")
    assert [source for source, _ in _links(index)] == ["notes/训练流程.md"] * 2

    assert index.rebuild_all() == 2  # 文件还在：全量重建把它的链接补回来
    assert ("notes/SFT.md", "训练流程") in _links(index)


def test_index_from_an_older_schema_gets_the_links_table(store: VaultStore, index: SearchIndex) -> None:
    index.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(index.db_path)
    conn.executescript(
        """
        CREATE TABLE notes (path TEXT PRIMARY KEY, title TEXT);
        CREATE TABLE ingest_log (input_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL, result TEXT NOT NULL);
        CREATE VIRTUAL TABLE notes_fts USING fts5(path UNINDEXED, title, tokenize = 'trigram');
        INSERT INTO ingest_log VALUES ('abc', '2026-10-13', '{"created": ["01X"], "skipped": false}');
        PRAGMA user_version = 6;
        """
    )
    conn.close()

    _add(store, index, "SFT", body="- [[预训练]]\n")

    with index.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert _links(index) == [("notes/SFT.md", "预训练")]
    assert index.get_ingest_log("abc") == ["01X"]  # 幂等日志不随表结构升级丢掉


# ---------- 图 ----------


@pytest.fixture
def vault(store: VaultStore, index: SearchIndex) -> None:
    """两个大块。训练流程 ↔ SFT 互相链接（应并成一条线），SFT → 快速排序 跨了大块。"""
    _add(store, index, "训练流程", category="AI工程/RAG", body="## 概念\n- [[SFT]]\n- [[RLHF]]\n")
    _add(store, index, "SFT", category="AI工程/RAG", body="- [[训练流程]]\n- [[快速排序]]\n- [[sft]]\n")
    _add(store, index, "RLHF", category="AI工程/Agent", body="- [[不存在的笔记]]\n")
    _add(store, index, "快速排序", category="计算机基础/算法", body="- [[归并排序]]\n")
    _add(store, index, "归并排序", category="计算机基础/算法")
    _add(store, index, "孤立的笔记", category="计算机基础/数据结构")
    _add(store, index, "拿不准的", category="AI工程/RAG", status="inbox", body="- [[SFT]]\n")
    _add(store, index, "归档的", category="AI工程/RAG", status="archived", body="- [[SFT]]\n")


def test_whole_graph_has_every_note_and_undirected_deduped_edges(index: SearchIndex, vault: None) -> None:
    graph = note_graph(index)

    assert sorted(_nodes(graph)) == [
        "notes/RLHF.md",
        "notes/SFT.md",
        "notes/孤立的笔记.md",
        "notes/归并排序.md",
        "notes/快速排序.md",
        "notes/拿不准的.md",  # 待整理的也在全图里
        "notes/训练流程.md",
    ]  # 归档的不在
    assert not any(_nodes(graph).values())
    # 互相链接只算一条；指向不存在的笔记、指向自己（[[sft]] 大小写不同也是自己）的不算
    assert _edges(graph) == [
        ("notes/RLHF.md", "notes/训练流程.md"),
        ("notes/SFT.md", "notes/快速排序.md"),
        ("notes/SFT.md", "notes/拿不准的.md"),
        ("notes/SFT.md", "notes/训练流程.md"),
        ("notes/归并排序.md", "notes/快速排序.md"),
    ]
    node = next(n for n in graph.nodes if n.path == "notes/SFT.md")
    assert (node.title, node.type, node.category, node.status) == ("SFT", "concept", "AI工程/RAG", "active")
    assert len(node.id) == 26


def test_graph_of_a_parent_category_includes_linked_notes_from_outside(
    index: SearchIndex, vault: None
) -> None:
    graph = note_graph(index, "AI工程")

    # 块里的三条 + 和它们直接相连的块外笔记（标 outside）。待整理的不算在分类里，所以也是 outside
    assert _nodes(graph) == {
        "notes/RLHF.md": False,
        "notes/SFT.md": False,
        "notes/训练流程.md": False,
        "notes/快速排序.md": True,
        "notes/拿不准的.md": True,
    }
    # 块外笔记彼此之间的线（快速排序 ↔ 归并排序）不取
    assert _edges(graph) == [
        ("notes/RLHF.md", "notes/训练流程.md"),
        ("notes/SFT.md", "notes/快速排序.md"),
        ("notes/SFT.md", "notes/拿不准的.md"),
        ("notes/SFT.md", "notes/训练流程.md"),
    ]


def test_graph_of_a_leaf_category(index: SearchIndex, vault: None) -> None:
    graph = note_graph(index, "AI工程/Agent")
    assert _nodes(graph) == {"notes/RLHF.md": False, "notes/训练流程.md": True}
    assert _edges(graph) == [("notes/RLHF.md", "notes/训练流程.md")]

    # 「AI工程/RAG」不能把「AI工程/RAG进阶」也算进去
    assert _nodes(note_graph(index, "计算机基础/数据结构")) == {"notes/孤立的笔记.md": False}
    empty = note_graph(index, "没有这个分类")
    assert (empty.nodes, empty.edges) == ([], [])


def test_edges_follow_renames_and_deletions(
    store: VaultStore, index: SearchIndex, organizer: Organizer, vault: None
) -> None:
    editor = NoteEditor(organizer)
    sft = next(n for n in note_graph(index).nodes if n.title == "SFT")

    editor.edit(sft.id, title="监督微调")  # 文件改名，别处的链接跟着改
    assert ("notes/监督微调.md", "notes/训练流程.md") in _edges(note_graph(index))

    editor.delete(sft.id)
    graph = note_graph(index)
    assert "notes/监督微调.md" not in _nodes(graph)
    assert _edges(graph) == [
        ("notes/RLHF.md", "notes/训练流程.md"),
        ("notes/归并排序.md", "notes/快速排序.md"),
    ]


def test_api_graph(vault_root: Path, store: VaultStore, index: SearchIndex, vault: None) -> None:
    api = TestClient(create_app(vault_root))

    whole = api.get("/api/graph").json()
    assert len(whole["nodes"]) == 7 and len(whole["edges"]) == 5
    assert set(whole["nodes"][0]) == {"path", "id", "title", "type", "category", "status", "outside"}
    assert set(whole["edges"][0]) == {"source", "target"}

    scoped = api.get("/api/graph", params={"category": "计算机基础"}).json()
    assert {n["title"]: n["outside"] for n in scoped["nodes"]} == {
        "快速排序": False,
        "归并排序": False,
        "孤立的笔记": False,
        "SFT": True,
    }
    assert api.get("/api/graph", params={"category": ""}).json() == whole
