"""SQLite 索引，位于 vault/.noteagent/index.db。纯派生物，可随时删除重建。

建表 SQL 与 docs/SCHEMA.md 第 4 节逐字一致。
tokenize='trigram' 不可改：FTS5 默认分词器按空格切词，中文检索会整段当一个 token。
"""

import hashlib
import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from typing import Iterator

import yaml

from app.vault.models import Note
from app.vault.store import VaultStore

logger = logging.getLogger(__name__)

# 四段拼起来与 SCHEMA.md 第 4 节一字不差。分开存是因为表结构升级时只重建派生的三张表，ingest_log 留着
_NOTES_SQL = """
CREATE TABLE notes (
  path              TEXT PRIMARY KEY,   -- 相对 vault 根
  id                TEXT UNIQUE,        -- 26 字符 ULID，可空：UNIQUE 允许多个 NULL
  type              TEXT NOT NULL,      -- topic | concept
  title             TEXT NOT NULL,
  category          TEXT,               -- 「大块/小块」，可空
  category_locked   INTEGER NOT NULL,   -- 0/1
  tags              TEXT NOT NULL,      -- JSON array
  aliases           TEXT NOT NULL,      -- JSON array
  summary           TEXT NOT NULL,
  status            TEXT NOT NULL,
  confidence        REAL NOT NULL,
  duplicate_of      TEXT,               -- 疑似与之重复的笔记 id，可空
  content_hash      TEXT NOT NULL,
  updated           TEXT NOT NULL
);
"""
_INGEST_LOG_SQL = """
CREATE TABLE ingest_log (
  input_hash    TEXT PRIMARY KEY,   -- 输入内容 SHA256，用于幂等
  created_at    TEXT NOT NULL,
  result        TEXT NOT NULL       -- JSON: {created: [id], skipped: bool}
);
"""
_FTS_SQL = """
CREATE VIRTUAL TABLE notes_fts USING fts5(
  path UNINDEXED, title, aliases, summary, body,
  tokenize = 'trigram'
);
"""
_LINKS_SQL = """
CREATE TABLE links (
  source  TEXT NOT NULL,   -- 发出链接的笔记的 path
  target  TEXT NOT NULL,   -- [[双链]] 里写的文件名（不含 .md）；不保证那条笔记存在
  PRIMARY KEY (source, target)
);
"""
_SCHEMA_SQL = _NOTES_SQL + _INGEST_LOG_SQL + _FTS_SQL + _LINKS_SQL

# 记在 PRAGMA user_version 里，与 SCHEMA.md 的版本号一致
SCHEMA_VERSION = 7

# fts 表里 aliases 的存储分隔符，查询层按它还原成列表做精确匹配
ALIAS_SEP = "\n"


# [[文件名]] 和 [[文件名|显示名]]；前面带 ! 的是嵌入素材，不是指向笔记的链接
_LINK = re.compile(r"(?<!!)\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


def link_targets(body: str) -> list[str]:
    """正文里 [[双链]] 指向的文件名，去重，保持出现顺序。"""
    seen: dict[str, None] = {}
    for match in _LINK.finditer(body):
        target = match.group(1).strip()
        if target and not target.startswith("assets/"):
            seen.setdefault(target)
    return list(seen)


def content_hash(note: Note) -> str:
    payload = note.frontmatter.model_dump_json() + "\n" + note.body
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SearchIndex:
    def __init__(self, store: VaultStore) -> None:
        self.store = store
        self.db_path = store.state_dir / "index.db"

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """每次操作独立连接，用完即关，保证 index.db 随时可删。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            # WAL：读不阻塞写、写不阻塞读，多线程各开连接时不会互相卡死
            conn.execute("PRAGMA journal_mode=WAL")
            # 抢不到写锁时等 5 秒再报错，而不是立刻 SQLITE_BUSY
            conn.execute("PRAGMA busy_timeout=5000")
            self._ensure_schema(conn)
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        """表结构版本不符时丢弃并重建 notes / notes_fts / links（数据由启动时的全量重建补回）。

        ingest_log 不丢：它不是从笔记文件派生的，丢了就失去重复录入的检测。
        """
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if version == SCHEMA_VERSION and {"notes", "notes_fts", "ingest_log", "links"} <= tables:
            return
        conn.executescript(
            "DROP TABLE IF EXISTS notes; DROP TABLE IF EXISTS notes_fts; DROP TABLE IF EXISTS links;"
        )
        conn.executescript(_NOTES_SQL + _FTS_SQL + _LINKS_SQL)
        if "ingest_log" not in tables:
            conn.executescript(_INGEST_LOG_SQL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # ---------- 写入 ----------

    def upsert_note(self, note: Note) -> None:
        with self.connect() as conn:
            self._upsert(conn, note)

    @staticmethod
    def _upsert(conn: sqlite3.Connection, note: Note) -> None:
        if note.path is None:
            raise ValueError("note.path 未设置，无法索引")
        path = note.path.as_posix()
        fm = note.frontmatter
        conn.execute("DELETE FROM notes WHERE path = ?", (path,))
        conn.execute(
            "INSERT INTO notes"
            " (path, id, type, title, category, category_locked, tags, aliases, summary, status,"
            "  confidence, duplicate_of, content_hash, updated)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                path,
                fm.id,
                fm.type,
                fm.title,
                fm.category,
                int(fm.category_locked),
                json.dumps(fm.tags, ensure_ascii=False),
                json.dumps(fm.aliases, ensure_ascii=False),
                fm.summary,
                fm.status,
                fm.confidence,
                fm.duplicate_of,
                content_hash(note),
                fm.updated.isoformat(),
            ),
        )
        conn.execute("DELETE FROM notes_fts WHERE path = ?", (path,))
        conn.execute(
            "INSERT INTO notes_fts (path, title, aliases, summary, body) VALUES (?, ?, ?, ?, ?)",
            (path, fm.title, ALIAS_SEP.join(fm.aliases), fm.summary, note.body),
        )
        conn.execute("DELETE FROM links WHERE source = ?", (path,))
        conn.executemany(
            "INSERT INTO links (source, target) VALUES (?, ?)",
            [(path, target) for target in link_targets(note.body)],
        )

    def remove_note(self, rel_path: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM notes WHERE path = ?", (rel_path,))
            conn.execute("DELETE FROM notes_fts WHERE path = ?", (rel_path,))
            conn.execute("DELETE FROM links WHERE source = ?", (rel_path,))

    # ---------- 幂等日志（SCHEMA 不变量 8） ----------

    def get_ingest_log(self, input_hash: str) -> list[str] | None:
        """返回该输入上次创建的笔记 id；没处理过返回 None。"""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT result FROM ingest_log WHERE input_hash = ?", (input_hash,)
            ).fetchone()
        return None if row is None else json.loads(row[0])["created"]

    def put_ingest_log(self, input_hash: str, created_ids: list[str], created_at: str) -> None:
        result = json.dumps({"created": created_ids, "skipped": False})
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ingest_log (input_hash, created_at, result)"
                " VALUES (?, ?, ?)",
                (input_hash, created_at, result),
            )

    def delete_ingest_log(self, input_hash: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM ingest_log WHERE input_hash = ?", (input_hash,))

    def rebuild_all(self) -> int:
        """清空后全量重建，扫描 notes/ 和 inbox/ 两个目录。返回索引的笔记数。

        读不了的文件（用户在 Obsidian 里手写、frontmatter 不合契约）跳过并告警，
        不让一条坏笔记拖垮整个索引（SCHEMA 不变量 13）。
        """
        with self.connect() as conn:
            conn.execute("DELETE FROM notes")
            conn.execute("DELETE FROM notes_fts")
            conn.execute("DELETE FROM links")
            count = 0
            for rel in self.store.list_notes(include_inbox=True):
                try:
                    note = self.store.read_note(rel)
                    self._upsert(conn, note)
                except (ValueError, OSError, yaml.YAMLError, sqlite3.IntegrityError) as exc:
                    logger.warning("重建索引时跳过 %s: %s", rel.as_posix(), exc)
                    continue
                count += 1
            return count
