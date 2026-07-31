"""index.py / query.py：中文全文检索、重建、排序、过滤。"""

import pytest

from app.search.index import SearchIndex
from app.search.query import (
    _RANK_ALIAS_EXACT,
    _RANK_TITLE_EXACT,
    _recall_exact,
    distinct_suggested_domains,
    search,
)
from app.vault.store import VaultStore
from tests.conftest import make_note


def _create_and_index(store: VaultStore, index: SearchIndex, **kwargs):
    note = make_note(**kwargs)
    store.create_note(note)
    index.upsert_note(note)
    return note


def test_chinese_fulltext_search(store: VaultStore, index: SearchIndex) -> None:
    _create_and_index(store, index)  # 标题「RAG 检索增强生成」
    _create_and_index(
        store,
        index,
        title="动态规划入门",
        category="cs-fundamentals/algorithm",
        tags=["动态规划"],
        aliases=["DP"],
        summary="把大问题拆成重叠子问题，记住中间结果。",
        body="## 一句话\n把大问题拆成重叠子问题，记住中间结果。\n",
    )

    results = search(index, "检索增强")
    assert [r.title for r in results] == ["RAG 检索增强生成"]

    # 正文命中也算（trigram 对正文做了索引）
    results = search(index, "重叠子问题")
    assert [r.title for r in results] == ["动态规划入门"]


def test_exact_title_beats_fts(store: VaultStore, index: SearchIndex) -> None:
    _create_and_index(
        store,
        index,
        title="向量数据库选型",
        summary="按召回率与运维成本选向量数据库。",
        aliases=[],
        body="## 一句话\n按召回率与运维成本选向量数据库。\n\n## 要点\n- 向量数据库是 RAG 的常见载体\n",
    )
    _create_and_index(
        store,
        index,
        title="向量数据库",
        summary="存向量并做近邻检索的数据库。",
        aliases=["vector db"],
        body="## 一句话\n存向量并做近邻检索的数据库。\n",
    )

    results = search(index, "向量数据库")
    assert results[0].title == "向量数据库"  # 精确匹配排最前
    assert {r.title for r in results} == {"向量数据库", "向量数据库选型"}


def test_alias_exact_match(store: VaultStore, index: SearchIndex) -> None:
    _create_and_index(store, index, aliases=["检索增强", "retrieval augmented generation"])
    results = search(index, "retrieval augmented generation")
    assert [r.title for r in results] == ["RAG 检索增强生成"]


def test_category_and_tag_filter(store: VaultStore, index: SearchIndex) -> None:
    _create_and_index(store, index)
    _create_and_index(
        store,
        index,
        title="动态规划入门",
        category="cs-fundamentals/algorithm",
        tags=["动态规划"],
        aliases=[],
        summary="把大问题拆成重叠子问题。",
    )

    only_rag = search(index, "", category="ai-engineering/rag")
    assert [r.title for r in only_rag] == ["RAG 检索增强生成"]

    only_dp = search(index, "", tag="动态规划")
    assert [r.title for r in only_dp] == ["动态规划入门"]

    none = search(index, "检索增强", category="cs-fundamentals/algorithm")
    assert none == []


def test_rebuild_after_deleting_db(store: VaultStore, index: SearchIndex) -> None:
    _create_and_index(store, index)
    _create_and_index(
        store,
        index,
        title="动态规划入门",
        category="cs-fundamentals/algorithm",
        tags=["动态规划"],
        aliases=[],
        summary="把大问题拆成重叠子问题。",
    )
    assert search(index, "检索增强")

    index.db_path.unlink()
    assert not index.db_path.exists()

    count = index.rebuild_all()
    assert count == 2
    assert [r.title for r in search(index, "检索增强")] == ["RAG 检索增强生成"]
    assert [r.title for r in search(index, "动态规划入门")] == ["动态规划入门"]


def test_inbox_hidden_by_default_and_ranked_last(store: VaultStore, index: SearchIndex) -> None:
    _create_and_index(
        store,
        index,
        title="缓存失效策略",
        summary="讨论缓存失效的几种触发方式。",
        aliases=[],
    )
    # inbox 笔记标题就是查询词本身（精确匹配、最高档），仍必须排在 active 之后
    _create_and_index(
        store,
        index,
        title="缓存失效",
        category=None,
        status="inbox",
        confidence=0.3,
        suggested_domain="缓存策略",
        summary="低置信度的缓存失效碎片。",
        aliases=[],
    )

    assert [r.title for r in search(index, "缓存失效")] == ["缓存失效策略"]

    with_inbox = search(index, "缓存失效", include_inbox=True)
    assert [r.title for r in with_inbox] == ["缓存失效策略", "缓存失效"]


def test_rebuild_keeps_inbox_notes(store: VaultStore, index: SearchIndex) -> None:
    _create_and_index(store, index)
    _create_and_index(
        store,
        index,
        title="检索增强低置信碎片",
        category=None,
        status="inbox",
        confidence=0.3,
        suggested_domain="检索技术",
        aliases=[],
    )

    index.db_path.unlink()
    count = index.rebuild_all()

    assert count == 2  # rebuild 扫描 notes/ 和 inbox/
    titles = [r.title for r in search(index, "检索增强", include_inbox=True)]
    assert titles == ["RAG 检索增强生成", "检索增强低置信碎片"]
    assert [r.title for r in search(index, "检索增强")] == ["RAG 检索增强生成"]


def test_limit_caps_results(store: VaultStore, index: SearchIndex) -> None:
    for i in range(25):
        _create_and_index(
            store,
            index,
            title=f"缓存条目笔记 {i + 1:02d}",
            summary="批量生成的缓存条目测试笔记。",
            aliases=[],
        )

    assert len(search(index, "缓存条目")) == 20  # 默认 limit=20
    assert len(search(index, "缓存条目", limit=5)) == 5


def test_search_never_reads_note_files(
    store: VaultStore, index: SearchIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回归护栏：NoteRef 完全从索引构造（SCHEMA 第 4 节），search 不得回读笔记文件。"""
    for i in range(25):
        _create_and_index(
            store,
            index,
            title=f"缓存条目笔记 {i + 1:02d}",
            summary="批量生成的缓存条目测试笔记。",
            aliases=[],
        )

    read_calls: list[object] = []

    def spy_read_note(self: VaultStore, rel_path: object) -> None:
        read_calls.append(rel_path)
        raise AssertionError("search 不允许读笔记文件")

    monkeypatch.setattr(VaultStore, "read_note", spy_read_note)

    results = search(index, "缓存条目")
    assert len(results) == 20
    assert read_calls == []


def test_alias_match_is_exact_not_substring(store: VaultStore, index: SearchIndex) -> None:
    """别名档必须是等值比较："RAG" 不得因 LIKE 大小写不敏感的子串匹配误中 "storage"。"""
    with_alias = _create_and_index(
        store, index, title="检索增强生成", aliases=["RAG"]
    )
    with_storage = _create_and_index(
        store,
        index,
        title="存储引擎选型",
        category="ai-engineering/agent",
        tags=[],
        aliases=["storage"],
        summary="对比几类存储引擎的取舍。",
        body="## 一句话\n对比几类存储引擎的取舍。\n",
    )

    # "storage" 的 trigram 含 "rag"，允许它经 FTS 档召回，但只能排在别名精确档之后
    results = search(index, "RAG")
    assert [r.title for r in results] == ["检索增强生成", "存储引擎选型"]

    # 别名档本身绝不允许命中 "storage"
    hits: dict[str, tuple[int, float]] = {}
    with index.connect() as conn:
        _recall_exact(conn, "RAG", hits)
    assert with_storage.path is not None and with_alias.path is not None
    assert with_storage.path.as_posix() not in hits
    assert hits[with_alias.path.as_posix()] == (_RANK_ALIAS_EXACT, 0.0)


def test_exact_match_is_case_insensitive(store: VaultStore, index: SearchIndex) -> None:
    """SCHEMA 不变量 12：精确档（标题、别名）大小写不敏感。"""
    note = _create_and_index(
        store,
        index,
        title="LangGraph 状态机",
        category="ai-engineering/agent",
        tags=["LangGraph", "状态管理"],
        aliases=["RAG"],
        summary="用 LangGraph 建模带状态的执行图。",
        body="## 一句话\n用 LangGraph 建模带状态的执行图。\n",
    )
    assert note.path is not None
    path = note.path.as_posix()

    with index.connect() as conn:
        title_hits: dict[str, tuple[int, float]] = {}
        _recall_exact(conn, "langgraph 状态机", title_hits)
        assert title_hits[path] == (_RANK_TITLE_EXACT, 0.0)

        alias_hits: dict[str, tuple[int, float]] = {}
        _recall_exact(conn, "rag", alias_hits)
        assert alias_hits[path] == (_RANK_ALIAS_EXACT, 0.0)

    # 端到端也能命中
    assert [r.title for r in search(index, "langgraph 状态机")] == ["LangGraph 状态机"]


def test_rebuild_preserves_suggested_domain_and_verified(
    store: VaultStore, index: SearchIndex
) -> None:
    active = _create_and_index(store, index)  # 创建时 verified 被强制 false
    generated = _create_and_index(
        store,
        index,
        title="角动量算符的对易关系",
        category=None,
        status="inbox",
        confidence=0.3,
        suggested_domain="量子力学",
        kind="generated",
        tags=[],
        aliases=[],
        summary="三个角动量分量互不对易。",
        body="## 要点\n（待补充）\n\n## 待核实\n> 以下内容由模型生成，尚未核实。\n\n- [Lx, Ly] = iħLz\n",
    )
    assert active.path is not None and generated.path is not None

    # 人工核实 active 笔记：手工把文件里的 verified 翻成 true
    target = store.root / active.path
    text = target.read_text(encoding="utf-8")
    target.write_text(text.replace("verified: false", "verified: true"), encoding="utf-8")

    index.db_path.unlink()
    assert index.rebuild_all() == 2

    with index.connect() as conn:
        rows = dict(
            conn.execute("SELECT path, suggested_domain FROM notes").fetchall()
        )
        assert rows[generated.path.as_posix()] == "量子力学"
        assert rows[active.path.as_posix()] is None
        verified = dict(conn.execute("SELECT path, verified FROM notes").fetchall())
        assert verified[active.path.as_posix()] == 1  # 人工核实过的重建后仍为 true
        assert verified[generated.path.as_posix()] == 0


def test_distinct_suggested_domains(store: VaultStore, index: SearchIndex) -> None:
    """按计数倒序,只统计 inbox 且 suggested_domain 非空的笔记。"""
    for i in range(3):
        _create_and_index(
            store,
            index,
            title=f"量子碎片 {i}",
            category=None,
            status="inbox",
            confidence=0.3,
            suggested_domain="量子力学",
            tags=[],
            aliases=[],
            summary="量子力学的碎片。",
        )
    _create_and_index(
        store,
        index,
        title="缓存碎片",
        category=None,
        status="inbox",
        confidence=0.3,
        suggested_domain="缓存策略",
        tags=[],
        aliases=[],
        summary="缓存的碎片。",
    )
    # active 笔记(有分类、suggested_domain 为 None)不计入
    _create_and_index(store, index)

    assert distinct_suggested_domains(index) == [("量子力学", 3), ("缓存策略", 1)]


def test_remove_note(store: VaultStore, index: SearchIndex) -> None:
    note = _create_and_index(store, index)
    assert note.path is not None
    index.remove_note(note.path.as_posix())
    assert search(index, "检索增强") == []
