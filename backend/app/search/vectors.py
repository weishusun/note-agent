"""笔记的向量索引（SCHEMA 第 4 节 embeddings 表）：换个说法也能搜到。

每条笔记一个向量，由设置里的向量模型算出来，存在 index.db 的 embeddings 表里。
它和 notes 表一样是派生物，但重算要调用接口、要花钱，所以表结构升级时不跟着丢。

向量模型没配置、或者接口调用失败，都不是错误：没有向量就只做字面检索。
笔记有变动时由 SearchIndex 通知这里，稍后在后台线程里把缺的、过期的向量补上。
"""

import hashlib
import logging
import math
import threading
import time
from array import array

from app import settings
from app.extract import client
from app.search.index import ALIAS_SEP, SearchIndex

logger = logging.getLogger(__name__)

# 一次请求里最多放几条、每条最多多少字：都远在服务商的限制之内
BATCH_SIZE = 16
TEXT_MAX_CHARS = 1800
# 笔记变动后等这么久再去算向量：一次录入会连着写好几条笔记，攒到一起只调一次接口
SYNC_DELAY_SECONDS = 1.0
# 这些区块里是链接列表和原始输入的引用，不是这条笔记自己的内容
_SKIPPED_SECTIONS = ("## 原始输入", "## 关联", "## 概念")


def embedding_text(title: str, aliases: list[str], summary: str, body: str) -> str:
    """一条笔记拿去算向量的文字：标题、别名、概要，加上正文里属于它自己的内容。"""
    kept: list[str] = []
    skipping = False
    for line in body.splitlines():
        if line.startswith("## "):
            skipping = line.strip() in _SKIPPED_SECTIONS
        if not skipping:
            kept.append(line)
    parts = [title]
    if aliases:
        parts.append("别名：" + "、".join(aliases))
    parts += [summary, "\n".join(kept).strip()]
    return "\n".join(part for part in parts if part)[:TEXT_MAX_CHARS]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize(vector: list[float]) -> array:
    """归一化成单位向量：之后两个向量的点积就是余弦相似度。"""
    norm = math.sqrt(math.sumprod(vector, vector))
    return array("f", (v / norm for v in vector) if norm else vector)


class VectorIndex:
    def __init__(self, index: SearchIndex) -> None:
        self.index = index
        self._lock = threading.Lock()
        # (算这些向量用的模型, {path: 向量})；索引一有变动就作废
        self._cache: tuple[str, dict[str, array]] | None = None
        self._pending = False
        self._worker_alive = False
        index.subscribe(self._on_index_changed)

    # ---------- 状态 ----------

    @staticmethod
    def model() -> str | None:
        return settings.load().embed_model or None

    def enabled(self) -> bool:
        return self.model() is not None

    def status(self) -> dict[str, object]:
        """给设置页看：配了哪个模型、多少条笔记已经有（当前模型算的）向量。"""
        model = self.model()
        with self.index.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM notes WHERE status != 'archived'").fetchone()[0]
            embedded = conn.execute(
                "SELECT COUNT(*) FROM embeddings e JOIN notes n ON n.path = e.path"
                " WHERE n.status != 'archived' AND e.model = ?",
                (model or "",),
            ).fetchone()[0]
        return {"enabled": model is not None, "model": model, "total": total, "embedded": embedded}

    # ---------- 写入 ----------

    def sync(self) -> int:
        """把缺的、过期的向量补上，删掉已经不存在的笔记的向量。返回这次新算了多少条。

        没配向量模型时什么都不做。接口调用失败会抛异常——已经算好的那几批不受影响，下次接着补。
        """
        model = self.model()
        if model is None:
            return 0
        with self.index.connect() as conn:
            rows = conn.execute(
                "SELECT n.path, n.title, f.aliases, n.summary, f.body, e.text_hash, e.model"
                " FROM notes n JOIN notes_fts f ON f.path = n.path"
                " LEFT JOIN embeddings e ON e.path = n.path"
                " WHERE n.status != 'archived'"
            ).fetchall()
            gone = conn.execute(
                "DELETE FROM embeddings WHERE path NOT IN"
                " (SELECT path FROM notes WHERE status != 'archived')"
            ).rowcount
        stale: list[tuple[str, str, str]] = []
        for path, title, aliases, summary, body, text_hash, old_model in rows:
            text = embedding_text(title, [a for a in aliases.split(ALIAS_SEP) if a], summary, body)
            digest = _hash(text)
            if digest != text_hash or old_model != model:
                stale.append((path, text, digest))

        for start in range(0, len(stale), BATCH_SIZE):
            batch = stale[start : start + BATCH_SIZE]
            vectors = client.embed_texts([text for _path, text, _digest in batch])
            with self.index.connect() as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO embeddings (path, text_hash, model, vector)"
                    " VALUES (?, ?, ?, ?)",
                    [
                        (path, digest, model, normalize(vector).tobytes())
                        for (path, _text, digest), vector in zip(batch, vectors)
                    ],
                )
            self._invalidate()
        if gone:
            self._invalidate()
        return len(stale)

    def _on_index_changed(self) -> None:
        self._invalidate()
        self.request_sync()

    def request_sync(self) -> None:
        """笔记有变动：稍后在后台补向量。短时间内的多次请求合成一次。没配向量模型时什么都不做。"""
        if not self.enabled():
            return
        with self._lock:
            self._pending = True
            if self._worker_alive:
                return
            self._worker_alive = True
        threading.Thread(target=self._sync_loop, name="vector-sync", daemon=True).start()

    def _sync_loop(self) -> None:
        while True:
            time.sleep(SYNC_DELAY_SECONDS)
            with self._lock:
                self._pending = False
            try:
                count = self.sync()
                if count:
                    logger.info("补算了 %d 条笔记的向量", count)
            except Exception as exc:
                # 没网、限流、模型名填错：下次笔记变动或重启时再试，不在这里死循环重试
                logger.warning("计算笔记向量失败，这次先跳过: %s", exc)
            with self._lock:
                if not self._pending:
                    self._worker_alive = False
                    return

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """等后台的补算结束（测试用）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._worker_alive:
                    return True
            time.sleep(0.01)
        return False

    # ---------- 检索 ----------

    def embed_query(self, text: str) -> array | None:
        """把一句话变成向量。没配模型或调用失败返回 None，调用方退回字面检索。"""
        if not self.enabled() or not text.strip():
            return None
        try:
            [vector] = client.embed_texts([text.strip()[:TEXT_MAX_CHARS]])
        except Exception as exc:
            logger.warning("问题的向量没算出来，这次只做字面检索: %s", exc)
            return None
        return normalize(vector)

    def search(
        self, query: array, limit: int = 10, exclude: frozenset[str] = frozenset()
    ) -> list[tuple[str, float]]:
        """与 query 最接近的笔记：[(path, 余弦相似度)]，相似度从高到低。"""
        scored = [
            (math.sumprod(query, vector), path)
            for path, vector in self._vectors().items()
            if path not in exclude and len(vector) == len(query)
        ]
        scored.sort(reverse=True)
        return [(path, score) for score, path in scored[:limit]]

    def vector_of(self, path: str) -> array | None:
        return self._vectors().get(path)

    def _vectors(self) -> dict[str, array]:
        model = self.model() or ""
        with self._lock:
            cached = self._cache
        if cached is not None and cached[0] == model:
            return cached[1]
        with self.index.connect() as conn:
            rows = conn.execute(
                "SELECT e.path, e.vector FROM embeddings e JOIN notes n ON n.path = e.path"
                " WHERE n.status != 'archived' AND e.model = ?",
                (model,),
            ).fetchall()
        loaded = {path: array("f", blob) for path, blob in rows}
        with self._lock:
            self._cache = (model, loaded)
        return loaded

    def _invalidate(self) -> None:
        with self._lock:
            self._cache = None
