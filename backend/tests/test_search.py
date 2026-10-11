"""index.py / query.py：中文全文检索、重建、排序、过滤。"""

import sqlite3

import pytest

from app.search.index import SCHEMA_VERSION, SearchIndex
from app.search.query import (
    _RANK_ALIAS_EXACT,
    _RANK_TITLE_EXACT,
    _recall_exact,
    all_refs,
    categories_in_use,
    category_counts,
    find_by_id,
    list_recent,
    refs_with_category,
    search,
    status_counts,
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
        category="计算机基础/算法",
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
        category="计算机基础/算法",
        tags=["动态规划"],
        aliases=[],
        summary="把大问题拆成重叠子问题。",
    )

    only_rag = search(index, "", category="AI工程/RAG")
    assert [r.title for r in only_rag] == ["RAG 检索增强生成"]

    only_dp = search(index, "", tag="动态规划")
    assert [r.title for r in only_dp] == ["动态规划入门"]

    none = search(index, "检索增强", category="计算机基础/算法")
    assert none == []


def test_rebuild_after_deleting_db(store: VaultStore, index: SearchIndex) -> None:
    _create_and_index(store, index)
    _create_and_index(
        store,
        index,
        title="动态规划入门",
        category="计算机基础/算法",
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
        category="AI工程/Agent",
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
        category="AI工程/Agent",
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


def test_rebuild_preserves_category_and_lock(store: VaultStore, index: SearchIndex) -> None:
    plain = _create_and_index(store, index)
    pinned = _create_and_index(store, index, title="动态规划入门", category="计算机基础/算法", aliases=[])
    assert plain.path is not None and pinned.path is not None
    # 锁定只能事后产生（创建时一律不锁）
    store.update_frontmatter(pinned.path, category_locked=True)

    index.db_path.unlink()
    assert index.rebuild_all() == 2

    with index.connect() as conn:
        rows = {
            path: (category, locked)
            for path, category, locked in conn.execute(
                "SELECT path, category, category_locked FROM notes"
            )
        }
    assert rows[plain.path.as_posix()] == ("AI工程/RAG", 0)
    assert rows[pinned.path.as_posix()] == ("计算机基础/算法", 1)


def test_old_index_schema_is_rebuilt_but_ingest_log_survives(
    store: VaultStore, index: SearchIndex
) -> None:
    """表结构版本不符：notes / notes_fts 丢弃重建，ingest_log 保留（SCHEMA 第 4 节）。"""
    index.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(index.db_path)
    conn.executescript(
        """
        CREATE TABLE notes (path TEXT PRIMARY KEY, suggested_domain TEXT, verified INTEGER);
        CREATE TABLE ingest_log (input_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL, result TEXT NOT NULL);
        CREATE VIRTUAL TABLE notes_fts USING fts5(path UNINDEXED, title, tokenize = 'trigram');
        INSERT INTO notes VALUES ('notes/旧.md', '人工智能', 0);
        INSERT INTO ingest_log VALUES ('abc', '2026-10-11', '{"created": ["01X"], "skipped": false}');
        """
    )
    conn.close()

    note = _create_and_index(store, index)  # 新结构下能正常写入

    with index.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert [p for (p,) in conn.execute("SELECT path FROM notes")] == [note.path.as_posix()]
    assert index.get_ingest_log("abc") == ["01X"]
    assert [r.title for r in search(index, "检索增强")] == ["RAG 检索增强生成"]


def test_search_returns_id_and_rebuild_preserves_it(
    store: VaultStore, index: SearchIndex
) -> None:
    """NoteRef.id 来自索引的 id 列；删库重建后仍是落盘的那个 id，不重新生成。"""
    note = _create_and_index(store, index)
    created_id = note.frontmatter.id
    assert created_id is not None

    assert [r.id for r in search(index, "检索增强")] == [created_id]

    index.db_path.unlink()
    index.rebuild_all()
    assert [r.id for r in search(index, "检索增强")] == [created_id]


def test_notes_without_id_are_indexed_as_null(store: VaultStore, index: SearchIndex) -> None:
    """Obsidian 手建的笔记没有 id：多条都能入索引（UNIQUE 允许多个 NULL），检索返回 null。"""
    for title in ("手建笔记甲", "手建笔记乙"):
        note = make_note(title=title, aliases=[])
        note.path = store.create_note(note)
        note.frontmatter = note.frontmatter.model_copy(update={"id": None})
        index.upsert_note(note)

    results = search(index, "手建笔记")
    assert len(results) == 2
    assert [r.id for r in results] == [None, None]


@pytest.fixture
def categorized(store: VaultStore, index: SearchIndex) -> dict[str, str]:
    """四条笔记：两条在 AI工程/RAG，一条在 计算机基础/算法，一条待整理（带着模型猜的分类）。"""
    specs = {
        "RAG 入门": dict(category="AI工程/RAG"),
        "向量检索": dict(category="AI工程/RAG"),
        "动态规划": dict(category="计算机基础/算法"),
        "拿不准的笔记": dict(category="AI工程/Agent", status="inbox", confidence=0.4),
    }
    ids = {}
    for day, (title, extra) in enumerate(specs.items(), start=1):
        note = make_note(title=title, aliases=[], **extra)
        stamp = note.frontmatter.updated.replace(day=day)
        note.frontmatter = note.frontmatter.model_copy(update={"updated": stamp})
        store.create_note(note)
        index.upsert_note(note)
        ids[title] = note.frontmatter.id
    return ids


def test_category_counts_only_count_active(categorized: dict, index: SearchIndex) -> None:
    assert category_counts(index) == {"AI工程/RAG": 2, "计算机基础/算法": 1}
    assert status_counts(index) == (4, 1, 0)
    # 待整理笔记上模型猜的分类也算「在用」，清理空分类时不能把它删了
    assert categories_in_use(index) == {"AI工程/RAG", "计算机基础/算法", "AI工程/Agent"}


def test_list_recent_filters(categorized: dict, index: SearchIndex) -> None:
    def titles(**kwargs) -> list[str]:
        return [r.title for r in list_recent(index, **kwargs)]

    assert titles() == ["拿不准的笔记", "动态规划", "向量检索", "RAG 入门"]  # 新的在前，含待整理
    assert titles(category="AI工程/RAG") == ["向量检索", "RAG 入门"]
    assert titles(category="AI工程") == ["向量检索", "RAG 入门"]  # 大块：列它下面所有小块，只列 active
    assert titles(category="计算机基础") == ["动态规划"]
    assert titles(category="AI") == []  # 前缀相同的别的名字不能混进来
    assert titles(status="inbox") == ["拿不准的笔记"]
    assert titles(limit=1) == ["拿不准的笔记"]


def test_lookups_by_id_and_category(categorized: dict, index: SearchIndex) -> None:
    ref = find_by_id(index, categorized["动态规划"])
    assert ref is not None and (ref.title, ref.category_locked) == ("动态规划", False)
    assert find_by_id(index, "不存在的id") is None

    assert [r.title for r in refs_with_category(index, "AI工程/Agent")] == ["拿不准的笔记"]
    assert [r.title for r in refs_with_category(index, "AI工程/RAG")] == ["RAG 入门", "向量检索"]
    assert len(all_refs(index)) == 4


def test_remove_note(store: VaultStore, index: SearchIndex) -> None:
    note = _create_and_index(store, index)
    assert note.path is not None
    index.remove_note(note.path.as_posix())
    assert search(index, "检索增强") == []
