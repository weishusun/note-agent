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
    "path, title, category, tags, aliases, summary, status, confidence, updated"
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


def _to_ref(row: dict) -> NoteRef:
    return NoteRef(
        path=row["path"],
        title=row["title"],
        category=row["category"],
        tags=row["tags"],
        summary=row["summary"],
        updated=row["updated"],
        confidence=row["confidence"],
    )


def _fetch_rows(conn: sqlite3.Connection, paths: list[str]) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    if not paths:
        return rows
    placeholders = ", ".join("?" for _ in paths)
    cursor = conn.execute(
        f"SELECT {_ROW_COLUMNS} FROM notes WHERE path IN ({placeholders})", paths
    )
    for path, title, cat, tags, aliases, summary, status, confidence, updated in cursor:
        rows[path] = {
            "path": path,
            "title": title,
            "category": cat,
            "tags": json.loads(tags),
            "aliases": json.loads(aliases),
            "summary": summary,
            "status": status,
            "confidence": confidence,
            "updated": updated,
        }
    return rows


def _better(hits: dict[str, tuple[int, float]], path: str, rank: int, score: float) -> None:
    current = hits.get(path)
    if current is None or (rank, score) < current:
        hits[path] = (rank, score)


def _recall_exact(conn: sqlite3.Connection, q: str, hits: dict[str, tuple[int, float]]) -> None:
    for (path,) in conn.execute("SELECT path FROM notes WHERE title = ?", (q,)):
        _better(hits, path, _RANK_TITLE_EXACT, 0.0)
    for path, aliases_json in conn.execute("SELECT path, aliases FROM notes"):
        if q in json.loads(aliases_json):
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
