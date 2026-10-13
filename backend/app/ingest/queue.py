"""录入队列（SCHEMA 2.7）：提交的输入先落到 SQLite，再由一个后台线程按提交顺序一份一份处理。

队列在 vault/.noteagent/queue.db 里。它和 index.db 不同，**不是派生物**：里面是还没处理的输入，
删了就丢了。进程重启后，没处理完的任务从头重做（见 resume）。

这里只管排队、持久化和调度；一份输入具体怎么变成笔记由构造时传入的 handler 决定。
"""

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.vault.ids import new_ulid

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id           TEXT PRIMARY KEY,   -- ULID
  kind         TEXT NOT NULL,      -- screenshot | text | file | url
  label        TEXT NOT NULL,      -- 列表里显示的名字
  filename     TEXT,
  payload      BLOB,               -- 输入本身；处理成功后清空
  input_hash   TEXT NOT NULL,
  status       TEXT NOT NULL,      -- pending | running | done | failed
  stage        TEXT NOT NULL,
  note_ids     TEXT NOT NULL,      -- JSON array
  duplicate    INTEGER NOT NULL,   -- 1 = 这份输入之前录入过，note_ids 是当时的结果
  force        INTEGER NOT NULL,   -- 1 = 使用者要求重新处理，跳过幂等检查
  error        TEXT,
  progress     TEXT,               -- JSON [done, total]；只有长资料分段整理时有
  created_at   TEXT NOT NULL,
  queued_at    TEXT NOT NULL,      -- 排队顺序；重试时更新
  finished_at  TEXT
)
"""
_COLUMNS = (
    "id, kind, label, filename, input_hash, status, stage, note_ids, duplicate, force,"
    " error, progress, created_at, finished_at"
)
FIRST_STAGE = "解析"
# 处理完的任务只留最近这么多条，更早的记录自动清掉
KEEP_FINISHED = 200


@dataclass(frozen=True)
class QueuedJob:
    id: str
    kind: str
    label: str
    filename: str | None
    input_hash: str
    status: str
    stage: str
    note_ids: list[str]
    duplicate: bool
    force: bool
    error: str | None
    created_at: str
    finished_at: str | None
    # (已完成, 总数)：长资料分段整理到第几段了
    progress: tuple[int, int] | None = None
    # 只有交给 handler 处理时才带上；列表和查询不读它
    payload: bytes | None = None


class JobRunning(Exception):
    """任务正在处理，不能取消或重试。"""


class JobNotRetryable(Exception):
    """任务不在可以重试的状态，或者它的输入已经不在了。"""


# (任务, 报告阶段的回调, 报告进度的回调) -> 新建笔记的 id
Handler = Callable[[QueuedJob, Callable[[str], None], Callable[[int, int], None]], list[str]]


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _job(row: sqlite3.Row, payload: bytes | None = None) -> QueuedJob:
    return QueuedJob(
        id=row["id"],
        kind=row["kind"],
        label=row["label"],
        filename=row["filename"],
        input_hash=row["input_hash"],
        status=row["status"],
        stage=row["stage"],
        note_ids=json.loads(row["note_ids"]),
        duplicate=bool(row["duplicate"]),
        force=bool(row["force"]),
        error=row["error"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
        progress=tuple(json.loads(row["progress"])) if row["progress"] else None,
        payload=payload,
    )


class IngestQueue:
    def __init__(self, db_path: Path, handler: Handler) -> None:
        self.db_path = db_path
        self._handler = handler
        self._lock = threading.Lock()
        self._worker_alive = False
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        if not self._ready:
            conn.execute(_SCHEMA)
            # 早先建的表没有 progress 这一列
            if "progress" not in {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}:
                conn.execute("ALTER TABLE jobs ADD COLUMN progress TEXT")
            conn.commit()
            self._ready = True
        return conn

    # ---------- 提交 ----------

    def submit(
        self,
        *,
        kind: str,
        label: str,
        payload: bytes,
        input_hash: str,
        filename: str | None = None,
        force: bool = False,
    ) -> str:
        """排进队尾，返回任务 id。"""
        job_id = self._insert(kind, label, filename, payload, input_hash, "pending", [], False, force)
        self._ensure_worker()
        return job_id

    def add_duplicate(
        self,
        *,
        kind: str,
        label: str,
        payload: bytes,
        input_hash: str,
        note_ids: list[str],
        filename: str | None = None,
    ) -> str:
        """这份输入之前录入过：直接记成一条已完成的任务。输入留着，使用者可以选择重新处理。"""
        return self._insert(kind, label, filename, payload, input_hash, "done", note_ids, True, False)

    def _insert(
        self,
        kind: str,
        label: str,
        filename: str | None,
        payload: bytes,
        input_hash: str,
        status: str,
        note_ids: list[str],
        duplicate: bool,
        force: bool,
    ) -> str:
        job_id, now = new_ulid(), _now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO jobs (id, kind, label, filename, payload, input_hash, status, stage,"
                " note_ids, duplicate, force, error, created_at, queued_at, finished_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                (
                    job_id,
                    kind,
                    label,
                    filename,
                    payload,
                    input_hash,
                    status,
                    "写入" if status == "done" else FIRST_STAGE,
                    json.dumps(note_ids),
                    int(duplicate),
                    int(force),
                    now,
                    now,
                    now if status == "done" else None,
                ),
            )
        return job_id

    # ---------- 查询 ----------

    def get(self, job_id: str) -> QueuedJob | None:
        with closing(self._connect()) as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row) if row is not None else None

    def list(self, limit: int = 50) -> list[QueuedJob]:
        """最近提交的在前。"""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM jobs ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_job(row) for row in rows]

    def find_active(self, input_hash: str) -> str | None:
        """同一份输入已经在排队或正在处理时，返回那个任务的 id。"""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id FROM jobs WHERE input_hash = ? AND status IN ('pending', 'running')"
                " ORDER BY queued_at LIMIT 1",
                (input_hash,),
            ).fetchone()
        return row["id"] if row is not None else None

    # ---------- 使用者的操作 ----------

    def retry(self, job_id: str) -> None:
        """失败的任务重试；「之前录入过」的任务重新处理（跳过幂等检查）。排到队尾。"""
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT status, duplicate, payload IS NOT NULL AS has_payload FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] in ("pending", "running"):
                raise JobRunning(job_id)
            retryable = row["status"] == "failed" or (row["status"] == "done" and row["duplicate"])
            if not retryable or not row["has_payload"]:
                raise JobNotRetryable(job_id)
            conn.execute(
                "UPDATE jobs SET status = 'pending', stage = ?, note_ids = '[]', duplicate = 0,"
                " force = 1, error = NULL, progress = NULL, queued_at = ?, finished_at = NULL"
                " WHERE id = ?",
                (FIRST_STAGE, _now(), job_id),
            )
        self._ensure_worker()

    def remove(self, job_id: str) -> None:
        """取消一个还在排队的任务，或者清掉一条处理完的记录。已经写进知识库的笔记不受影响。"""
        with closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] == "running":
                raise JobRunning(job_id)
            conn.execute("DELETE FROM jobs WHERE id = ? AND status != 'running'", (job_id,))

    def clear_finished(self) -> int:
        """清掉所有成功处理完的记录；失败的留着，等使用者重试或自己清。"""
        with closing(self._connect()) as conn, conn:
            return conn.execute("DELETE FROM jobs WHERE status = 'done'").rowcount

    # ---------- 调度 ----------

    def resume(self) -> int:
        """进程启动时调用：上次没处理完的任务接着处理。返回等着处理的任务数。

        重启前正处理到一半的那一份从头重做——它可能已经写下了一部分笔记，重做会再写一遍，
        重名的那些会被标成疑似重复，交给使用者处理。
        """
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE jobs SET status = 'pending', stage = ?, progress = NULL"
                " WHERE status = 'running'",
                (FIRST_STAGE,),
            )
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE status = 'pending'"
            ).fetchone()["n"]
        if pending:
            self._ensure_worker()
        return pending

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """等到队列里没有要处理的任务（测试用）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._worker_alive:
                    return True
            time.sleep(0.005)
        return False

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker_alive:
                return
            self._worker_alive = True
        threading.Thread(target=self._drain, name="ingest-queue", daemon=True).start()

    def _drain(self) -> None:
        """处理到队列空了为止，然后线程退出；下次有任务进来时再起一个。"""
        while True:
            with self._lock:
                # 「队列空了」和「标记线程已退出」必须在同一把锁里，否则这中间提交的任务没人处理
                try:
                    job = self._claim_next()
                except Exception:
                    logger.exception("读取录入队列失败")
                    job = None
                if job is None:
                    self._worker_alive = False
                    return
            self._process(job)

    def _claim_next(self) -> QueuedJob | None:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                f"SELECT {_COLUMNS}, payload FROM jobs WHERE status = 'pending'"
                " ORDER BY queued_at, id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (row["id"],))
        return _job(row, payload=row["payload"])

    def _process(self, job: QueuedJob) -> None:
        try:
            ids = self._handler(
                job,
                # 换阶段时进度清零：进度只属于「拆分」里的分段整理
                lambda stage: self._update(job.id, stage=stage, progress=None),
                lambda done, total: self._update(job.id, progress=json.dumps([done, total])),
            )
        except Exception as exc:
            logger.exception("录入失败 job=%s", job.id)
            self._update(
                job.id,
                status="failed",
                error=str(exc) or type(exc).__name__,
                progress=None,
                finished_at=_now(),
            )
            return
        # 成功后输入就不用留了
        self._update(
            job.id,
            status="done",
            stage="写入",
            note_ids=json.dumps(ids),
            payload=None,
            progress=None,
            finished_at=_now(),
        )
        self._prune()

    def _update(self, job_id: str, **fields: object) -> None:
        assignments = ", ".join(f"{name} = ?" for name in fields)
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", (*fields.values(), job_id))
        except sqlite3.Error:
            logger.exception("更新录入任务 %s 失败", job_id)

    def _prune(self) -> None:
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute(
                    "DELETE FROM jobs WHERE status IN ('done', 'failed') AND id NOT IN ("
                    " SELECT id FROM jobs WHERE status IN ('done', 'failed')"
                    " ORDER BY created_at DESC, id DESC LIMIT ?)",
                    (KEEP_FINISHED,),
                )
        except sqlite3.Error:
            logger.exception("清理录入记录失败")
