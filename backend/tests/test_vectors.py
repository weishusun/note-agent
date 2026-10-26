"""向量索引（app/search/vectors.py）和混合检索（app/search/hybrid.py）。向量接口全部打桩。"""

import math
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import settings
from app.ask import answer, expand
from app.ask.answer import answer_question
from app.ask.expand import Turn
from app.extract import client
from app.main import create_app
from app.search import hybrid
from app.search import vectors as vectors_module
from app.search.hybrid import retrieve, vocabulary
from app.search.index import SCHEMA_VERSION, SearchIndex
from app.search.vectors import VectorIndex, embedding_text, normalize
from app.vault.store import VaultStore
from tests.conftest import make_note
from tests.test_ask import FakeExpander, FakeModel

# 假的向量空间：每一维是一个「意思」。文字里出现哪些词，就在对应的维度上有分量——
# 所以「监督微调」和「教模型听指令」方向相同，尽管没有一个字相同
MEANINGS = [
    ("微调", "SFT", "听指令", "指令"),
    ("排序", "快排", "从小到大"),
    ("检索", "RAG", "外部资料"),
    ("虚拟语气", "假设"),
]


def fake_vector(text: str) -> list[float]:
    vector = [float(sum(text.count(word) for word in words)) for words in MEANINGS]
    return vector + [0.05]  # 垫一维，免得全零


class Embedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.error: Exception | None = None
        # 第几次调用开始失败（从 1 数）；None 表示不限
        self.fail_from: int | None = None

    def __call__(self, texts: list[str], override: str | None = None) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.error is not None and (self.fail_from is None or len(self.calls) >= self.fail_from):
            raise self.error
        return [fake_vector(text) for text in texts]

    @property
    def embedded(self) -> int:
        return sum(len(batch) for batch in self.calls)


@pytest.fixture
def embedder(monkeypatch: pytest.MonkeyPatch) -> Embedder:
    fake = Embedder()
    monkeypatch.setattr(client, "embed_texts", fake)
    # 后台补算不等那一秒
    monkeypatch.setattr(vectors_module, "SYNC_DELAY_SECONDS", 0.0)
    return fake


@pytest.fixture
def embed_model(isolated_settings: Path) -> None:
    settings.save({"embed": {"model": "fake-embed"}})


@pytest.fixture
def vectors(index: SearchIndex) -> VectorIndex:
    return VectorIndex(index)


def _add(store: VaultStore, index: SearchIndex, title: str, summary: str, body: str = "", **kw):
    note = make_note(title=title, summary=summary, aliases=kw.pop("aliases", []), body=body or "正文\n", **kw)
    store.create_note(note)
    index.upsert_note(note)
    return note


@pytest.fixture
def notes(store: VaultStore, index: SearchIndex, vectors: VectorIndex, embedder: Embedder, embed_model: None):
    sft = _add(store, index, "SFT", "监督微调：用标注数据继续训练模型。")
    sort = _add(store, index, "快速排序", "一种排序算法。")
    rag = _add(store, index, "RAG", "生成前先检索外部资料。")
    assert vectors.wait_idle()
    embedder.calls.clear()
    return sft, sort, rag


def _stored(index: SearchIndex) -> dict[str, tuple[str, int]]:
    with index.connect() as conn:
        return {
            path: (model, len(blob) // 4)
            for path, model, blob in conn.execute("SELECT path, model, vector FROM embeddings")
        }


# ---------- 算向量用的文字 ----------


def test_embedding_text_keeps_the_notes_own_content_only() -> None:
    body = (
        "## 要点\n- 第一条\n\n## 模型补充\n> 说明\n\n- 补充\n\n"
        "## 关联\n- [[别的笔记]]\n\n## 原始输入\n> 一大段原文\n"
    )
    text = embedding_text("SFT", ["监督微调", "Supervised Fine-Tuning"], "一句话概要。", body)
    assert text == (
        "SFT\n别名：监督微调、Supervised Fine-Tuning\n一句话概要。\n"
        "## 要点\n- 第一条\n\n## 模型补充\n> 说明\n\n- 补充"
    )
    assert embedding_text("标题", [], "", "") == "标题"
    assert len(embedding_text("标题", [], "概要", "长" * 5000)) == vectors_module.TEXT_MAX_CHARS


def test_normalize_makes_the_dot_product_a_cosine() -> None:
    a, b = normalize([3.0, 4.0]), normalize([6.0, 8.0])
    assert math.isclose(math.sumprod(a, b), 1.0, rel_tol=1e-6)
    assert list(normalize([0.0, 0.0])) == [0.0, 0.0]


# ---------- 向量跟着笔记走 ----------


def test_nothing_happens_without_an_embedding_model(
    store: VaultStore, index: SearchIndex, vectors: VectorIndex, embedder: Embedder
) -> None:
    _add(store, index, "SFT", "监督微调。")
    assert vectors.wait_idle()
    assert (vectors.enabled(), vectors.sync(), embedder.calls) == (False, 0, [])
    assert vectors.embed_query("怎么微调") is None
    assert vectors.status() == {"enabled": False, "model": None, "total": 1, "embedded": 0, "calibrated": False}


def test_new_notes_are_embedded_in_the_background(
    index: SearchIndex, vectors: VectorIndex, embedder: Embedder, notes
) -> None:
    assert _stored(index) == {
        "notes/SFT.md": ("fake-embed", 5),
        "notes/快速排序.md": ("fake-embed", 5),
        "notes/RAG.md": ("fake-embed", 5),
    }
    assert vectors.status() == {"enabled": True, "model": "fake-embed", "total": 3, "embedded": 3, "calibrated": False}
    # 没有变动：再同步一次不调用接口
    assert vectors.sync() == 0 and embedder.calls == []


def test_only_changed_notes_are_re_embedded(
    store: VaultStore, index: SearchIndex, vectors: VectorIndex, embedder: Embedder, notes
) -> None:
    sft, sort, _rag = notes
    # 改了分类：文字没变，不用重算
    index.upsert_note(store.update_frontmatter(sft.path, category="AI工程/Agent"))
    # 改了概要：要重算
    index.upsert_note(store.rewrite_note(sort.path, summary="把一组数从小到大排好的算法。"))
    assert vectors.wait_idle()

    assert [len(batch) for batch in embedder.calls] == [1]
    assert embedder.calls[0][0].startswith("快速排序\n把一组数从小到大排好的算法。")


def test_deleted_and_archived_notes_lose_their_vectors(
    store: VaultStore, index: SearchIndex, vectors: VectorIndex, notes
) -> None:
    sft, sort, _rag = notes
    index.remove_note("notes/SFT.md")
    index.upsert_note(store.update_frontmatter(sort.path, status="archived"))
    assert vectors.wait_idle()
    assert sorted(_stored(index)) == ["notes/RAG.md"]
    assert [path for path, _ in vectors.search(normalize(fake_vector("微调 排序 检索")))] == ["notes/RAG.md"]


def test_changing_the_model_re_embeds_everything(
    index: SearchIndex, vectors: VectorIndex, embedder: Embedder, notes
) -> None:
    settings.save({"embed": {"model": "another-embed"}})
    # 换了模型：旧模型算的向量不能拿来比，检索时当它们不存在
    assert vectors.search(normalize(fake_vector("微调"))) == []
    assert vectors.sync() == 3
    assert {model for model, _dim in _stored(index).values()} == {"another-embed"}
    assert len(vectors.search(normalize(fake_vector("微调")))) == 3


def test_a_failed_batch_keeps_the_batches_before_it(
    store: VaultStore,
    index: SearchIndex,
    vectors: VectorIndex,
    embedder: Embedder,
    embed_model: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vectors_module, "BATCH_SIZE", 2)
    embedder.error, embedder.fail_from = RuntimeError("429 限流"), 2
    for i in range(5):
        store.create_note(note := make_note(title=f"笔记{i}", aliases=[]))
        with index.connect() as conn:  # 不触发后台补算，下面手动同步
            index._upsert(conn, note)

    with pytest.raises(RuntimeError, match="限流"):
        vectors.sync()
    assert len(_stored(index)) == 2  # 第一批已经存下

    embedder.error = None
    assert vectors.sync() == 3  # 下次只补剩下的
    assert len(_stored(index)) == 5


def test_background_sync_swallows_api_failures(
    store: VaultStore, index: SearchIndex, vectors: VectorIndex, embedder: Embedder, embed_model: None
) -> None:
    embedder.error = RuntimeError("没网")
    _add(store, index, "SFT", "监督微调。")  # 不抛异常：没有向量只是退回字面检索
    assert vectors.wait_idle()
    assert _stored(index) == {}
    assert vectors.embed_query("怎么微调") is None


def test_embeddings_survive_a_schema_upgrade(store: VaultStore, index: SearchIndex) -> None:
    index.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(index.db_path)
    conn.executescript(
        """
        CREATE TABLE notes (path TEXT PRIMARY KEY, title TEXT);
        CREATE TABLE ingest_log (input_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL, result TEXT NOT NULL);
        CREATE VIRTUAL TABLE notes_fts USING fts5(path UNINDEXED, title, tokenize = 'trigram');
        CREATE TABLE links (source TEXT NOT NULL, target TEXT NOT NULL, PRIMARY KEY (source, target));
        CREATE TABLE embeddings (path TEXT PRIMARY KEY, text_hash TEXT NOT NULL, model TEXT NOT NULL, vector BLOB NOT NULL);
        INSERT INTO embeddings VALUES ('notes/旧.md', 'abc', 'fake-embed', x'0000803f');
        PRAGMA user_version = 7;
        """
    )
    conn.close()

    with index.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        # 重算向量要调用接口：升级表结构时不丢
        assert conn.execute("SELECT path, model FROM embeddings").fetchall() == [("notes/旧.md", "fake-embed")]


# ---------- 检索 ----------


def test_search_ranks_by_meaning_not_wording(vectors: VectorIndex, notes) -> None:
    query = vectors.embed_query("怎么教模型听指令")
    hits = vectors.search(query)
    # 和「SFT」没有一个词相同，但意思最近；另外两条都不相干
    assert hits[0][0] == "notes/SFT.md" and hits[0][1] > 0.9
    assert {path for path, _ in hits[1:]} == {"notes/RAG.md", "notes/快速排序.md"}
    assert all(score < 0.2 for _, score in hits[1:])
    assert vectors.search(query, limit=1) == hits[:1]
    excluded = vectors.search(query, exclude=frozenset({"notes/SFT.md"}))
    assert {path for path, _ in excluded} == {"notes/RAG.md", "notes/快速排序.md"}


def test_hybrid_finds_a_note_the_wording_misses(index: SearchIndex, vectors: VectorIndex, notes) -> None:
    question = "怎么教模型听指令"
    # 问题和「SFT」这条笔记没有一个词相同：字面检索找不到
    assert retrieve(index, None, question) == []
    assert [r.title for r in retrieve(index, vectors, question)] == ["SFT"]


def test_hybrid_drops_vector_hits_that_are_not_close_enough(
    index: SearchIndex, vectors: VectorIndex, notes
) -> None:
    # 库里没有相关的笔记：向量检索照样能排出「最接近的」，但都不够近，不能拿来当依据
    assert retrieve(index, vectors, "今天天气怎么样") == []


def test_hybrid_drops_fragment_matches_that_are_unrelated_in_meaning(
    store: VaultStore, index: SearchIndex, vectors: VectorIndex, notes
) -> None:
    # 正文里碰巧有和问题一样的三字碎片（「怎么教」「教模型」），但讲的完全是另一回事
    _add(store, index, "红烧肉", "一道家常菜。", body="怎么教模型做菜\n")
    assert vectors.wait_idle()
    question = "怎么教模型听指令"
    assert [r.title for r in retrieve(index, None, question)] == ["红烧肉"]  # 纯字面检索会被它骗到
    assert [r.title for r in retrieve(index, vectors, question)] == ["SFT"]


def test_hybrid_keeps_fragment_matches_that_have_no_vector_yet(
    store: VaultStore, index: SearchIndex, vectors: VectorIndex, embedder: Embedder, notes
) -> None:
    embedder.error = RuntimeError("限流")  # 新笔记的向量这次没算出来
    _add(store, index, "红烧肉", "一道家常菜。", body="怎么教模型做菜\n")
    assert vectors.wait_idle()
    embedder.error = None  # 之后问题的向量算得出
    # 没法判断它相不相干：留着，不能因为向量还没算出来就把刚录入的笔记排除在外
    assert {r.title for r in retrieve(index, vectors, "怎么教模型听指令")} == {"SFT", "红烧肉"}


def test_hybrid_puts_notes_found_both_ways_first(
    store: VaultStore, index: SearchIndex, vectors: VectorIndex, notes
) -> None:
    _add(store, index, "LoRA", "一种参数高效的微调方法。")
    assert vectors.wait_idle()
    # 「RAG」只被字面检索命中（问题里有它的名字）；「SFT」两边都命中；「LoRA」只被向量命中
    question = "SFT 和 RAG 都是微调吗"
    assert [r.title for r in retrieve(index, vectors, question, limit=3)] == ["SFT", "RAG", "LoRA"]
    assert [r.title for r in retrieve(index, vectors, question, limit=1)] == ["SFT"]


def test_hybrid_uses_the_thresholds_of_the_embedding_model(
    index: SearchIndex, vectors: VectorIndex, notes, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert vectors.thresholds() == vectors_module.DEFAULT_THRESHOLDS  # fake-embed 没实测过
    question = "怎么教模型听指令"
    assert [r.title for r in retrieve(index, vectors, question)] == ["SFT"]
    # 换成一个尺度完全不同的模型：同样的相似度在它那里算不上相关
    strict = settings.EmbedThresholds(floor=1.5, margin=0.12, duplicate=0.55)
    monkeypatch.setattr(VectorIndex, "thresholds", staticmethod(lambda: strict))
    assert retrieve(index, vectors, question) == []


def test_hybrid_falls_back_to_lexical_when_the_query_cannot_be_embedded(
    index: SearchIndex, vectors: VectorIndex, embedder: Embedder, notes
) -> None:
    embedder.error = RuntimeError("超时")
    assert [r.title for r in retrieve(index, vectors, "RAG 是什么")] == ["RAG"]


# ---------- 接口 ----------


def test_api_vectors_status_and_settings(
    vault_root: Path, store: VaultStore, index: SearchIndex, embedder: Embedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client, "probe_text", lambda: {"ok": True, "detail": "正常", "seconds": 0.1})
    monkeypatch.setattr(client, "probe_vision", lambda: {"ok": True, "detail": "红色", "seconds": 0.1})
    api = TestClient(create_app(vault_root))
    note = make_note(title="SFT", aliases=[])
    store.create_note(note)

    assert api.get("/api/vectors").json()["enabled"] is False
    assert "embedding" not in api.post("/api/settings/test").json()  # 没填就不测

    saved = api.put("/api/settings", json={"embed": {"model": "  fake-embed "}}).json()
    assert saved["embed"]["model"] == "fake-embed"
    probe = api.post("/api/settings/test").json()["embedding"]
    assert (probe["ok"], probe["detail"]) == (True, "5 维")

    assert api.post("/api/reindex").json() == {"indexed": 1}  # 同步：顺带把向量补上
    assert api.app.state.vectors.wait_idle()
    assert api.get("/api/vectors").json() == {
        "enabled": True,
        "model": "fake-embed",
        "total": 1,
        "embedded": 1,
        "calibrated": False,  # fake-embed 没实测过
    }


# ---------- 检索词扩展的词表 ----------


@pytest.fixture
def asking(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeExpander, FakeModel]:
    expander, model = FakeExpander(), FakeModel()
    monkeypatch.setattr(expand, "get_expand_model", lambda override=None: expander)
    monkeypatch.setattr(answer, "get_answer_model", lambda override=None: model)
    return expander, model


def test_vocabulary_is_picked_by_meaning(
    store: VaultStore, index: SearchIndex, vectors: VectorIndex, embedder: Embedder,
    notes, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hybrid, "VOCABULARY_NEIGHBORS", 1)
    query = vectors.embed_query("怎么教模型听指令")
    assert vocabulary(index, vectors, query) == ["SFT"]

    # 向量还没算出来的新笔记也列上：不能因为向量没算好就看不见刚录入的笔记
    embedder.error = RuntimeError("限流")
    _add(store, index, "红烧肉", "一道家常菜。")
    assert vectors.wait_idle()
    assert vocabulary(index, vectors, query) == ["SFT", "红烧肉"]

    # 没有问题的向量：退回最近更新的
    assert set(vocabulary(index, vectors, None)) == {"SFT", "快速排序", "RAG", "红烧肉"}
    assert set(vocabulary(index, None, None)) == {"SFT", "快速排序", "RAG", "红烧肉"}


def test_ask_embeds_the_question_once_and_shows_the_nearest_titles(
    store: VaultStore, index: SearchIndex, vectors: VectorIndex, embedder: Embedder,
    notes, asking, monkeypatch: pytest.MonkeyPatch,
) -> None:
    expander, _model = asking
    monkeypatch.setattr(hybrid, "VOCABULARY_NEIGHBORS", 1)
    result = answer_question(store, index, "怎么教模型听指令", vectors=vectors)

    assert embedder.calls == [["怎么教模型听指令"]]  # 挑词表和向量检索用的是同一个向量
    assert "- SFT" in expander.prompts[0] and "快速排序" not in expander.prompts[0]
    assert [r.title for r in result.sources] == ["SFT"]


def test_follow_up_vocabulary_uses_the_previous_question_too(
    store: VaultStore, index: SearchIndex, vectors: VectorIndex, embedder: Embedder,
    notes, asking,
) -> None:
    expander, _model = asking
    history = [Turn(question="什么是 SFT", answer="监督微调 [1]。")]
    expander.rewritten = "SFT 和外部资料检索有什么关系"
    answer_question(store, index, "它和检索有什么关系", vectors=vectors, history=history)
    # 先按「上一问 + 这一问」挑词表，改写之后按改写出来的那句话检索
    assert embedder.calls == [["什么是 SFT 它和检索有什么关系"], ["SFT 和外部资料检索有什么关系"]]

    # 改写失败：退回的就是「上一问 + 这一问」，不用再算一次
    embedder.calls.clear()
    expander.error = RuntimeError("超时")
    answer_question(store, index, "它和检索有什么关系", vectors=vectors, history=history)
    assert embedder.calls == [["什么是 SFT 它和检索有什么关系"]]


def test_ask_does_not_retry_an_embedding_that_failed(
    store: VaultStore, index: SearchIndex, vectors: VectorIndex, embedder: Embedder,
    notes, asking,
) -> None:
    embedder.error = RuntimeError("超时")
    result = answer_question(store, index, "RAG 是什么", vectors=vectors)
    assert len(embedder.calls) == 1  # 再算一次多半还是超时，白等
    assert [r.title for r in result.sources] == ["RAG"]  # 字面检索照常
