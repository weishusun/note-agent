"""检索。三路召回取并集：① title/aliases 精确匹配 ② category 过滤 ③ FTS5 全文。

排序：title 精确匹配 > 别名精确匹配 > FTS bm25。这一版不接 LLM 重排。
include_inbox=True 时 status=inbox 的笔记参与召回，且必须排在所有 active 笔记之后。
category / tag 参数作用于所有召回结果做过滤；q 为空时退化为按分类/标签浏览。
NoteRef 完全从索引构造，不回读笔记文件（SCHEMA 第 4 节）。
"""

import json
import re
import sqlite3
from collections.abc import Sequence

from app.search.index import SearchIndex
from app.vault.models import NoteRef

_RANK_TITLE_EXACT = 0
_RANK_ALIAS_EXACT = 1
_RANK_FTS = 2
_RANK_BROWSE = 3

_ROW_COLUMNS = (
    "path, id, type, title, category, category_locked, tags, aliases, summary, status,"
    " confidence, duplicate_of, updated"
)


def search(
    index: SearchIndex,
    q: str,
    category: str | None = None,
    tag: str | None = None,
    include_inbox: bool = False,
    limit: int = 20,
) -> list[NoteRef]:
    q = (q or "").strip()
    # path -> (rank, bm25 分数)，同一条命中多路时保留最优
    hits: dict[str, tuple[int, float]] = {}

    with index.connect() as conn:
        if q:
            _recall_exact(conn, q, hits)
            _recall_fts(conn, q, hits)
        elif category or tag:
            for (path,) in conn.execute("SELECT path FROM notes"):
                hits[path] = (_RANK_BROWSE, 0.0)
        rows = _fetch_rows(conn, list(hits))

    scored: list[tuple[tuple[int, int, float, str], NoteRef]] = []
    for path, (rank, score) in hits.items():
        row = rows.get(path)
        if row is None:
            continue
        status = row["status"]
        if status == "inbox" and not include_inbox:
            continue
        if category is not None and row["category"] != category:
            continue
        if tag is not None and tag not in row["tags"]:
            continue
        inbox_last = 1 if status == "inbox" else 0
        scored.append(((inbox_last, rank, score, path), _to_ref(row)))

    scored.sort(key=lambda item: item[0])
    return [ref for _key, ref in scored[:limit]]


def list_inbox(index: SearchIndex) -> list[NoteRef]:
    """GET /api/inbox：全部 status=inbox 的笔记，按 updated 倒序。"""
    with index.connect() as conn:
        cursor = conn.execute(
            f"SELECT {_ROW_COLUMNS} FROM notes WHERE status = 'inbox' ORDER BY updated DESC"
        )
        return [_to_ref(row) for row in _dict_rows(cursor)]


def retrieve_for_question(
    index: SearchIndex, question: str, limit: int = 5, keywords: Sequence[str] = ()
) -> list[NoteRef]:
    """给问答用的召回：输入是一句自然语言问题，不是关键词。

    search() 把 q 当整句短语去匹配，问句几乎必然零命中，所以这里换一套，按优先级：
    ① 问题里**包含**某条笔记的标题 ② 问题里包含它的别名，或某个 keyword 正好就是它的标题
    ③ keywords（模型为问题扩展出的检索词）与标题/别名互相包含，命中的词越多越靠前
    ④ 把问题切成三字片段、连同 keywords 做 FTS OR 查询。
    inbox 笔记照常参与（冷启动时所有笔记都在 inbox），只排除 archived。
    """
    question = (question or "").strip()
    if not question:
        return []
    keywords = [k.strip() for k in keywords if len(k.strip()) >= 2]
    hits: dict[str, tuple[int, float]] = {}
    with index.connect() as conn:
        for (path,) in conn.execute(
            "SELECT path FROM notes"
            " WHERE length(title) >= 2 AND instr(lower(?), lower(title)) > 0",
            (question,),
        ):
            _better(hits, path, _Q_RANK_TITLE, 0.0)
        for (path,) in conn.execute(
            "SELECT DISTINCT notes.path FROM notes, json_each(notes.aliases)"
            " WHERE length(json_each.value) >= 2"
            " AND instr(lower(?), lower(json_each.value)) > 0",
            (question,),
        ):
            _better(hits, path, _Q_RANK_ALIAS, 0.0)

        matched: dict[str, int] = {}
        for keyword in keywords:
            # 检索词就是某条笔记的标题：那是模型对着库里的标题列表挑出来的，和别名命中同等可信
            for (path,) in conn.execute(
                "SELECT path FROM notes WHERE title = ? COLLATE NOCASE", (keyword,)
            ):
                _better(hits, path, _Q_RANK_ALIAS, 0.0)
            paths = {
                path
                for sql in (_KEYWORD_TITLE_SQL, _KEYWORD_ALIAS_SQL)
                for (path,) in conn.execute(sql, (keyword, keyword))
            }
            for path in paths:
                matched[path] = matched.get(path, 0) + 1
        for path, count in matched.items():
            _better(hits, path, _Q_RANK_KEYWORD, -float(count))

        terms = _question_terms(question)
        terms += [k.replace('"', "") for k in keywords if len(k) >= 3 and k not in terms]
        if terms:
            match = " OR ".join(f'"{t}"' for t in terms)
            for path, score in conn.execute(
                "SELECT path, bm25(notes_fts) FROM notes_fts WHERE notes_fts MATCH ?"
                " ORDER BY bm25(notes_fts) LIMIT ?",
                (match, _QUESTION_FTS_CANDIDATES),
            ):
                _better(hits, path, _Q_RANK_FTS, score)
        rows = _fetch_rows(conn, list(hits))

    scored = [
        ((rank, score, path), _to_ref(rows[path]))
        for path, (rank, score) in hits.items()
        if path in rows and rows[path]["status"] != "archived"
    ]
    scored.sort(key=lambda item: item[0])
    return [ref for _key, ref in scored[:limit]]


def list_recent(
    index: SearchIndex,
    limit: int = 50,
    category: str | None = None,
    status: str | None = None,
    duplicates: bool = False,
) -> list[NoteRef]:
    """GET /api/notes：最近更新的笔记，新的在前，不含 archived。

    category 是「大块/小块」时只列该小块，只给「大块」时列它下面所有小块；都只列 active 的。
    status="inbox" 只列待整理的；duplicates=True 只列疑似重复的。都不给：全部笔记，含待整理。
    """
    where, params = "status != 'archived'", []
    if duplicates:
        where += " AND duplicate_of IS NOT NULL"
    elif status == "inbox":
        where = "status = 'inbox'"
    elif category:
        where = "status = 'active' AND "
        if "/" in category:
            where += "category = ?"
            params.append(category)
        else:
            # 不用 LIKE：分类名里可能有 % 和 _
            where += "substr(category, 1, ?) = ?"
            params += [len(category) + 1, category + "/"]
    with index.connect() as conn:
        cursor = conn.execute(
            f"SELECT {_ROW_COLUMNS} FROM notes WHERE {where}"
            " ORDER BY updated DESC, path LIMIT ?",
            (*params, limit),
        )
        return [_to_ref(row) for row in _dict_rows(cursor)]


def note_vocabulary(index: SearchIndex, limit: int = 200) -> list[str]:
    """知识库的「词表」：最近更新的若干条笔记的标题，别名附在括号里。

    给检索词扩展当参照——模型不知道库里用的是哪些词，就只能凭空猜同义词。
    """
    with index.connect() as conn:
        rows = conn.execute(
            "SELECT title, aliases FROM notes WHERE status != 'archived'"
            " ORDER BY updated DESC, path LIMIT ?",
            (limit,),
        ).fetchall()
    entries: list[str] = []
    for title, aliases_json in rows:
        aliases = [a for a in json.loads(aliases_json) if a != title]
        entries.append(f"{title}（{'、'.join(aliases)}）" if aliases else title)
    return entries


_Q_RANK_TITLE, _Q_RANK_ALIAS, _Q_RANK_KEYWORD, _Q_RANK_FTS = range(4)

# 检索词与标题/别名**互相包含**即命中：「微调」命中「模型微调」，「监督微调」也命中「微调」
_KEYWORD_TITLE_SQL = (
    "SELECT path FROM notes WHERE length(title) >= 2"
    " AND (instr(lower(title), lower(?)) > 0 OR instr(lower(?), lower(title)) > 0)"
)
_KEYWORD_ALIAS_SQL = (
    "SELECT DISTINCT notes.path FROM notes, json_each(notes.aliases)"
    " WHERE length(json_each.value) >= 2"
    " AND (instr(lower(json_each.value), lower(?)) > 0"
    "      OR instr(lower(?), lower(json_each.value)) > 0)"
)

_QUESTION_FTS_CANDIDATES = 50
_QUESTION_MAX_TERMS = 60
# ASCII 词整段保留；其余文字（中文等）连续段切三字滑窗。标点和空白是分隔符
_QUESTION_RUNS = re.compile(r"[A-Za-z0-9]+|[^\W\dA-Za-z_]+")


def _question_terms(question: str) -> list[str]:
    """trigram 分词器至少要 3 个字符才能匹配，更短的片段直接丢弃。"""
    terms: list[str] = []
    for run in _QUESTION_RUNS.findall(question):
        if len(run) < 3:
            continue
        pieces = [run] if run.isascii() else [run[i : i + 3] for i in range(len(run) - 2)]
        for piece in pieces:
            if piece not in terms:
                terms.append(piece)
    return terms[:_QUESTION_MAX_TERMS]


def refs_by_ids(index: SearchIndex, ids: list[str]) -> list[NoteRef]:
    """按 id 取 NoteRef，顺序与 ids 一致；索引里已不存在的 id 直接略过。"""
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    with index.connect() as conn:
        cursor = conn.execute(
            f"SELECT {_ROW_COLUMNS} FROM notes WHERE id IN ({placeholders})", ids
        )
        by_id = {row["id"]: row for row in _dict_rows(cursor)}
    return [_to_ref(by_id[i]) for i in ids if i in by_id]


def find_by_id(index: SearchIndex, note_id: str) -> NoteRef | None:
    refs = refs_by_ids(index, [note_id])
    return refs[0] if refs else None


def all_refs(index: SearchIndex) -> list[NoteRef]:
    """全部未归档的笔记，按路径排序（顺序稳定，重新归类分批时用）。"""
    with index.connect() as conn:
        cursor = conn.execute(
            f"SELECT {_ROW_COLUMNS} FROM notes WHERE status != 'archived' ORDER BY path"
        )
        return [_to_ref(row) for row in _dict_rows(cursor)]


def refs_with_category(index: SearchIndex, category: str) -> list[NoteRef]:
    """分类字段正好是 category 的全部笔记，不论状态。分类改名、合并、判空时用。"""
    with index.connect() as conn:
        cursor = conn.execute(
            f"SELECT {_ROW_COLUMNS} FROM notes WHERE category = ? ORDER BY path", (category,)
        )
        return [_to_ref(row) for row in _dict_rows(cursor)]


def refs_with_duplicate_of(index: SearchIndex, note_id: str) -> list[NoteRef]:
    """标记为「疑似与 note_id 重复」的全部笔记。那条笔记被删或被合并时要把这些标记清掉。"""
    with index.connect() as conn:
        cursor = conn.execute(
            f"SELECT {_ROW_COLUMNS} FROM notes WHERE duplicate_of = ? ORDER BY path", (note_id,)
        )
        return [_to_ref(row) for row in _dict_rows(cursor)]


def category_counts(index: SearchIndex) -> dict[str, int]:
    """每个「大块/小块」下 status=active 的笔记数。待整理的不计入任何分类（SCHEMA /api/tree）。"""
    with index.connect() as conn:
        rows = conn.execute(
            "SELECT category, COUNT(*) FROM notes"
            " WHERE status = 'active' AND category IS NOT NULL GROUP BY category"
        ).fetchall()
    return dict(rows)


def categories_in_use(index: SearchIndex) -> set[str]:
    """还有笔记写着的全部分类，含待整理笔记上模型猜的那些。清理空分类时用。"""
    with index.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category FROM notes WHERE category IS NOT NULL"
        ).fetchall()
    return {category for (category,) in rows}


def status_counts(index: SearchIndex) -> tuple[int, int, int]:
    """(未归档的笔记总数, 待整理的笔记数, 疑似重复的笔记数)。"""
    with index.connect() as conn:
        total, inbox, duplicates = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(status = 'inbox'), 0),"
            " COALESCE(SUM(duplicate_of IS NOT NULL), 0)"
            " FROM notes WHERE status != 'archived'"
        ).fetchone()
    return total, inbox, duplicates


def _to_ref(row: dict) -> NoteRef:
    return NoteRef(
        id=row["id"],
        type=row["type"],
        path=row["path"],
        title=row["title"],
        category=row["category"],
        category_locked=bool(row["category_locked"]),
        tags=row["tags"],
        summary=row["summary"],
        status=row["status"],
        duplicate_of=row["duplicate_of"],
        updated=row["updated"],
        confidence=row["confidence"],
    )


def _dict_rows(cursor: sqlite3.Cursor) -> list[dict]:
    columns = [c.strip() for c in _ROW_COLUMNS.split(",")]
    rows = []
    for values in cursor:
        row = dict(zip(columns, values))
        row["tags"] = json.loads(row["tags"])
        row["aliases"] = json.loads(row["aliases"])
        rows.append(row)
    return rows


def _fetch_rows(conn: sqlite3.Connection, paths: list[str]) -> dict[str, dict]:
    if not paths:
        return {}
    placeholders = ", ".join("?" for _ in paths)
    cursor = conn.execute(
        f"SELECT {_ROW_COLUMNS} FROM notes WHERE path IN ({placeholders})", paths
    )
    return {row["path"]: row for row in _dict_rows(cursor)}


def _better(hits: dict[str, tuple[int, float]], path: str, rank: int, score: float) -> None:
    current = hits.get(path)
    if current is None or (rank, score) < current:
        hits[path] = (rank, score)


def _recall_exact(conn: sqlite3.Connection, q: str, hits: dict[str, tuple[int, float]]) -> None:
    # 精确档大小写不敏感（SCHEMA 不变量 12），COLLATE NOCASE 只折叠 ASCII，中文不受影响
    for (path,) in conn.execute(
        "SELECT path FROM notes WHERE title = ? COLLATE NOCASE", (q,)
    ):
        _better(hits, path, _RANK_TITLE_EXACT, 0.0)
    # json_each 展开后逐项等值比较。
    # 不允许对 aliases 列做 LIKE 子串匹配（"RAG" 会误中 "storage" 的子串 "rag"）
    for (path,) in conn.execute(
        "SELECT DISTINCT notes.path FROM notes, json_each(notes.aliases)"
        " WHERE json_each.value = ? COLLATE NOCASE",
        (q,),
    ):
        _better(hits, path, _RANK_ALIAS_EXACT, 0.0)


def _recall_fts(conn: sqlite3.Connection, q: str, hits: dict[str, tuple[int, float]]) -> None:
    # 短语查询并转义引号，避免用户输入被当成 FTS 查询语法
    match = '"' + q.replace('"', '""') + '"'
    try:
        rows = conn.execute(
            "SELECT path, bm25(notes_fts) FROM notes_fts WHERE notes_fts MATCH ?",
            (match,),
        ).fetchall()
    except sqlite3.OperationalError:
        return  # 非法查询串（如过短、纯符号）视为无 FTS 命中
    for path, score in rows:
        _better(hits, path, _RANK_FTS, score)
