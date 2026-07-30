"""index.py / query.py：中文全文检索、重建、排序、过滤。"""

from app.search.index import SearchIndex
from app.search.query import search
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


def test_remove_note(store: VaultStore, index: SearchIndex) -> None:
    note = _create_and_index(store, index)
    assert note.path is not None
    index.remove_note(note.path.as_posix())
    assert search(index, "检索增强") == []
