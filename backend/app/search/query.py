"""检索。三路召回取并集：① title/aliases 精确匹配 ② category 过滤 ③ FTS5 全文。

排序：title 精确匹配 > 别名精确匹配 > FTS bm25。这一版不接 LLM 重排。
include_inbox=True 时 status=inbox 的笔记参与召回，且必须排在所有 active 笔记之后。
category / tag 参数作用于所有召回结果做过滤；q 为空时退化为按分类/标签浏览。
NoteRef 完全从索引构造，不回读笔记文件（SCHEMA 第 4 节）。
"""

import json
import sqlite3

from app.search.index import SearchIndex
from app.vault.models import NoteRef

_RANK_TITLE_EXACT = 0
_RANK_ALIAS_EXACT = 1
_RANK_FTS = 2
_RANK_BROWSE = 3

_ROW_COLUMNS = (
    "path, id, title, category, suggested_domain, tags, aliases, summary, status,"
    " verified, confidence, updated"
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


def distinct_suggested_domains(index: SearchIndex) -> list[tuple[str, int]]:
    """inbox 笔记里各 suggested_domain 的计数,按计数倒序(同数按名字稳定排序)。

    给整理循环/分类复用当聚类线索:分类时把它注入 prompt,让模型优先复用已有领域名,
    这是冷启动能收敛的关键。只统计 status=inbox 的笔记(已归档的分类不再是候选领域)。
    """
    with index.connect() as conn:
        rows = conn.execute(
            "SELECT suggested_domain, COUNT(*) AS n FROM notes"
            " WHERE status = 'inbox' AND suggested_domain IS NOT NULL"
            " GROUP BY suggested_domain ORDER BY n DESC, suggested_domain"
        ).fetchall()
    return [(domain, count) for domain, count in rows]


def _to_ref(row: dict) -> NoteRef:
    return NoteRef(
        id=row["id"],
        path=row["path"],
        title=row["title"],
        category=row["category"],
        suggested_domain=row["suggested_domain"],
        tags=row["tags"],
        summary=row["summary"],
        status=row["status"],
        verified=bool(row["verified"]),
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
