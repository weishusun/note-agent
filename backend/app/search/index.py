"""SQLite 索引，位于 vault/.noteagent/index.db。纯派生物，可随时删除重建。

建表 SQL 与 docs/SCHEMA.md 第 4 节逐字一致。
tokenize='trigram' 不可改：FTS5 默认分词器按空格切词，中文检索会整段当一个 token。
"""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app.vault.models import Note
from app.vault.store import VaultStore

# 与 SCHEMA.md 第 4 节一字不差
_SCHEMA_SQL = """
CREATE TABLE notes (
  path              TEXT PRIMARY KEY,   -- 相对 vault 根
  id                TEXT UNIQUE,        -- 26 字符 ULID，可空：UNIQUE 允许多个 NULL
  title             TEXT NOT NULL,
  category          TEXT,
  suggested_domain  TEXT,
  tags              TEXT NOT NULL,      -- JSON array
  aliases           TEXT NOT NULL,      -- JSON array
  summary           TEXT NOT NULL,
  status            TEXT NOT NULL,
  verified          INTEGER NOT NULL,   -- 0/1
  confidence        REAL NOT NULL,
  content_hash      TEXT NOT NULL,
  updated           TEXT NOT NULL
);

CREATE TABLE ingest_log (
  input_hash    TEXT PRIMARY KEY,   -- 输入内容 SHA256，用于幂等
  created_at    TEXT NOT NULL,
  result        TEXT NOT NULL       -- JSON: {created: [id], skipped: bool}
);

CREATE VIRTUAL TABLE notes_fts USING fts5(
  path UNINDEXED, title, aliases, summary, body,
  tokenize = 'trigram'
);
"""

# fts 表里 aliases 的存储分隔符，查询层按它还原成列表做精确匹配
ALIAS_SEP = "\n"


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
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = 'notes'"
        ).fetchone()
        if not exists:
            conn.executescript(_SCHEMA_SQL)

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
            " (path, id, title, category, suggested_domain, tags, aliases, summary, status,"
            "  verified, confidence, content_hash, updated)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                path,
                fm.id,
                fm.title,
                fm.category,
                fm.suggested_domain,
                json.dumps(fm.tags, ensure_ascii=False),
                json.dumps(fm.aliases, ensure_ascii=False),
                fm.summary,
                fm.status,
                int(fm.verified),
                fm.confidence,
                content_hash(note),
                fm.updated.isoformat(),
            ),
        )
        conn.execute("DELETE FROM notes_fts WHERE path = ?", (path,))
        conn.execute(
            "INSERT INTO notes_fts (path, title, aliases, summary, body) VALUES (?, ?, ?, ?, ?)",
            (path, fm.title, ALIAS_SEP.join(fm.aliases), fm.summary, note.body),
        )

    def remove_note(self, rel_path: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM notes WHERE path = ?", (rel_path,))
            conn.execute("DELETE FROM notes_fts WHERE path = ?", (rel_path,))

    def rebuild_all(self) -> int:
        """清空后全量重建，扫描 notes/ 和 inbox/ 两个目录。返回索引的笔记数。"""
        with self.connect() as conn:
            conn.execute("DELETE FROM notes")
            conn.execute("DELETE FROM notes_fts")
            count = 0
            for rel in self.store.list_notes(include_inbox=True):
                note = self.store.read_note(rel)
                self._upsert(conn, note)
                count += 1
            return count
