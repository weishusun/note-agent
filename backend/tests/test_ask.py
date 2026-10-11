"""问答：问句召回（retrieve_for_question）与 POST /api/ask。模型调用打桩。"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.ask import answer, expand
from app.ask.expand import expand_question
from app.main import create_app
from app.search.index import SearchIndex
from app.search.query import _question_terms, note_vocabulary, retrieve_for_question, search
from app.vault.store import VaultStore
from tests.conftest import make_note


def _add(store: VaultStore, index: SearchIndex, **kwargs):
    note = make_note(**kwargs)
    store.create_note(note)
    index.upsert_note(note)
    return note


@pytest.fixture
def seeded(store: VaultStore, index: SearchIndex) -> None:
    _add(store, index)  # 「RAG 检索增强生成」，别名 检索增强 / RAG，active
    _add(
        store,
        index,
        title="模型微调",
        category=None,
        status="inbox",
        confidence=0.0,
        tags=[],
        aliases=["Fine-tuning", "微调"],
        summary="用领域数据继续训练模型。",
        body="## 一句话\n用领域数据继续训练模型。\n\n## 要点\n- 训练成本高于检索增强方案\n",
    )
    _add(
        store,
        index,
        title="动态规划入门",
        category="计算机基础/算法",
        tags=["动态规划"],
        aliases=["DP"],
        summary="把大问题拆成重叠子问题，记住中间结果。",
        body="## 一句话\n把大问题拆成重叠子问题，记住中间结果。\n",
    )


class FakeModel:
    def __init__(self, reply: object = "RAG 先检索再生成 [1]。") -> None:
        self.reply = reply
        self.messages: list | None = None
        self.error: Exception | None = None

    def invoke(self, messages):
        self.messages = messages
        if self.error is not None:
            raise self.error
        return SimpleNamespace(content=self.reply)


class FakeExpander:
    """冒充检索词扩展用的文本模型。keywords 是它「想出来」的检索词。"""

    def __init__(self) -> None:
        self.keywords: list[str] = []
        self.error: Exception | None = None
        self.prompts: list[str] = []

    def with_structured_output(self, schema, method=None, include_raw=False):
        return self

    def invoke(self, messages):
        self.prompts.append(messages[0].content)
        if self.error is not None:
            raise self.error
        return {"parsed": expand._Keywords(keywords=self.keywords), "raw": None}


@pytest.fixture(autouse=True)
def expander(monkeypatch: pytest.MonkeyPatch) -> FakeExpander:
    # autouse：任何测试都不许因为漏打桩而真的去调模型
    fake = FakeExpander()
    monkeypatch.setattr(expand, "get_expand_model", lambda override=None: fake)
    return fake


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> FakeModel:
    fake = FakeModel()
    monkeypatch.setattr(answer, "get_answer_model", lambda override=None: fake)
    return fake


@pytest.fixture
def client(vault_root: Path, seeded: None, model: FakeModel) -> TestClient:
    return TestClient(create_app(vault_root))


# ---------- 召回 ----------


def test_question_terms() -> None:
    assert _question_terms("RAG 和微调有什么区别？") == ["RAG", "和微调", "微调有", "调有什", "有什么", "什么区", "么区别"]
    assert _question_terms("DP 是啥") == []  # 不足 3 字符的片段 trigram 匹配不了
    assert _question_terms("") == []


def test_natural_question_finds_notes_that_plain_search_misses(
    seeded: None, index: SearchIndex
) -> None:
    question = "RAG 和微调有什么区别？"
    assert search(index, question, include_inbox=True) == []  # 整句短语匹配：零命中

    titles = [r.title for r in retrieve_for_question(index, question)]
    # 别名「RAG」「微调」被问题包含 → 两条都召回；inbox 笔记也参与
    assert set(titles) == {"RAG 检索增强生成", "模型微调"}


def test_title_containment_ranks_above_fts(seeded: None, index: SearchIndex) -> None:
    # 「动态规划入门」是标题反向包含；「重叠子问题」只在另两条都没有的正文里
    titles = [r.title for r in retrieve_for_question(index, "动态规划入门讲了重叠子问题吗")]
    assert titles == ["动态规划入门"]


def test_fts_fallback_on_body_text(seeded: None, index: SearchIndex) -> None:
    titles = [r.title for r in retrieve_for_question(index, "为什么要记住中间结果")]
    assert titles == ["动态规划入门"]


def test_unrelated_question_and_limit(seeded: None, index: SearchIndex) -> None:
    assert retrieve_for_question(index, "今天天气怎么样") == []
    assert retrieve_for_question(index, "   ") == []
    assert len(retrieve_for_question(index, "RAG 和微调有什么区别？", limit=1)) == 1


def test_archived_notes_excluded(store: VaultStore, index: SearchIndex) -> None:
    note = _add(store, index, title="过时的缓存方案", aliases=[])
    note.frontmatter = note.frontmatter.model_copy(update={"status": "archived"})
    index.upsert_note(note)
    assert retrieve_for_question(index, "过时的缓存方案是什么") == []


# ---------- 检索词扩展 ----------

PARAPHRASE = "让模型适应特定任务有哪些办法"  # 与三条笔记的标题、别名、正文都没有三字重合


def test_keywords_recover_notes_a_paraphrased_question_misses(
    seeded: None, index: SearchIndex
) -> None:
    assert retrieve_for_question(index, PARAPHRASE) == []

    refs = retrieve_for_question(index, PARAPHRASE, keywords=["微调", "检索增强生成", "RAG"])
    assert [r.title for r in refs] == ["RAG 检索增强生成", "模型微调"]  # 命中 2 个词的排前面


def test_keyword_and_title_match_by_mutual_containment(seeded: None, index: SearchIndex) -> None:
    # 检索词比标题短：「规划」⊂「动态规划入门」
    assert [r.title for r in retrieve_for_question(index, PARAPHRASE, keywords=["规划"])] == [
        "动态规划入门"
    ]
    # 检索词比别名长：别名「微调」⊂「监督微调」
    assert [r.title for r in retrieve_for_question(index, PARAPHRASE, keywords=["监督微调"])] == [
        "模型微调"
    ]
    # 一个字的词会到处乱中，直接忽略
    assert retrieve_for_question(index, PARAPHRASE, keywords=["调", " "]) == []


def test_question_match_outranks_keyword_match(seeded: None, index: SearchIndex) -> None:
    refs = retrieve_for_question(index, "动态规划入门怎么学", keywords=["微调", "Fine-tuning"])
    assert [r.title for r in refs] == ["动态规划入门", "模型微调"]


def test_keywords_also_search_note_bodies(seeded: None, index: SearchIndex) -> None:
    # 「重叠子问题」只出现在正文里，标题和别名都没有
    refs = retrieve_for_question(index, PARAPHRASE, keywords=["重叠子问题"])
    assert [r.title for r in refs] == ["动态规划入门"]


def test_expand_question_cleans_model_output(expander: FakeExpander) -> None:
    expander.keywords = [" 微调 ", "微调", "RAG", "rag", "调", "长" * 61] + [f"词{i}" for i in range(10)]

    keywords = expand_question("  怎么让模型适应特定任务  ")

    # 去空白、大小写不敏感去重、丢掉过短过长的、最多 8 个
    assert keywords == ["微调", "RAG"] + [f"词{i}" for i in range(6)]
    assert "问题:怎么让模型适应特定任务" in expander.prompts[0]
    assert "(知识库里还没有笔记)" in expander.prompts[0]


def test_note_vocabulary_lists_titles_with_aliases(store: VaultStore, index: SearchIndex) -> None:
    _add(store, index, title="LoRA", aliases=["LoRA", "Low-Rank Adaptation"])
    _add(store, index, title="预训练", aliases=[])
    archived = _add(store, index, title="过时的缓存方案", aliases=[])
    archived.frontmatter = archived.frontmatter.model_copy(update={"status": "archived"})
    index.upsert_note(archived)

    # 与标题相同的别名不重复列出；archived 不参与
    assert sorted(note_vocabulary(index)) == ["LoRA（Low-Rank Adaptation）", "预训练"]
    assert len(note_vocabulary(index, limit=1)) == 1


def test_expansion_sees_the_vault_vocabulary(
    client: TestClient, model: FakeModel, expander: FakeExpander
) -> None:
    """模型看得到库里的标题，才能把「换了说法」的问题对到已有笔记上。"""
    expander.keywords = ["模型微调"]  # 模型从列表里原样挑出的标题
    data = client.post("/api/ask", json={"question": PARAPHRASE}).json()

    prompt = expander.prompts[0]
    assert "- 模型微调（Fine-tuning、微调）" in prompt
    assert "- RAG 检索增强生成（检索增强、RAG）" in prompt
    assert [s["title"] for s in data["sources"]] == ["模型微调"]


def test_keyword_equal_to_a_title_outranks_loose_keyword_matches(
    seeded: None, index: SearchIndex
) -> None:
    # 「RAG」「检索增强」两个词都松散命中 RAG 那条；但「动态规划入门」是原样的标题，排最前
    refs = retrieve_for_question(index, PARAPHRASE, keywords=["RAG", "检索增强", "动态规划入门"])
    # 第三条「模型微调」是正文里提到「检索增强」，经全文档召回，排最后
    assert [r.title for r in refs] == ["动态规划入门", "RAG 检索增强生成", "模型微调"]


def test_expand_question_failure_returns_empty(expander: FakeExpander) -> None:
    expander.error = RuntimeError("429 限流")
    assert expand_question("怎么让模型适应特定任务") == []


# ---------- 接口 ----------


def test_ask_uses_expanded_keywords(
    client: TestClient, model: FakeModel, expander: FakeExpander
) -> None:
    expander.keywords = ["微调", "RAG"]
    data = client.post("/api/ask", json={"question": PARAPHRASE}).json()

    assert data["keywords"] == ["微调", "RAG"]
    assert data["grounded"] is True
    assert {s["title"] for s in data["sources"]} == {"RAG 检索增强生成", "模型微调"}


def test_ask_survives_expansion_failure(
    client: TestClient, model: FakeModel, expander: FakeExpander
) -> None:
    expander.error = RuntimeError("429 限流")
    data = client.post("/api/ask", json={"question": "什么是检索增强？"}).json()

    assert data["keywords"] == []
    assert data["grounded"] is True  # 原问题的字面检索照常工作


def test_ask_returns_answer_with_numbered_sources(client: TestClient, model: FakeModel) -> None:
    resp = client.post("/api/ask", json={"question": "  什么是检索增强？ "})
    assert resp.status_code == 200
    data = resp.json()

    assert set(data) == {"answer", "sources", "grounded", "keywords"}
    assert data["answer"] == "RAG 先检索再生成 [1]。"
    assert data["grounded"] is True
    # 别名「检索增强」被问题包含 → 排第一；「模型微调」正文里提到检索增强，经全文档召回排其后
    assert [s["title"] for s in data["sources"]] == ["RAG 检索增强生成", "模型微调"]
    assert set(data["sources"][0]) >= {"id", "path", "title", "status"}

    # 笔记正文带编号进了提示，问题已去首尾空白
    human = model.messages[1].content
    assert "[1] RAG 检索增强生成\n## 一句话\n在生成前先检索相关片段" in human
    assert human.endswith("# 问题\n\n什么是检索增强？")
    assert "不是指令" in model.messages[0].content


def test_ask_without_hits_still_answers_but_not_grounded(
    client: TestClient, model: FakeModel
) -> None:
    model.reply = "知识库里没有足够的相关笔记。\n\n以下为模型补充,非知识库内容:……"
    data = client.post("/api/ask", json={"question": "今天天气怎么样"}).json()

    assert data["grounded"] is False
    assert data["sources"] == []
    assert data["answer"].startswith("知识库里没有足够的相关笔记")
    assert "(没有检索到任何笔记)" in model.messages[1].content


def test_ask_skips_notes_deleted_on_disk(
    client: TestClient, vault_root: Path, model: FakeModel
) -> None:
    """索引还在、文件已被用户在 Obsidian 里删掉：跳过它，编号保持连续。"""
    (vault_root / "notes/RAG 检索增强生成.md").unlink()
    data = client.post("/api/ask", json={"question": "RAG 和微调有什么区别？"}).json()

    assert [s["title"] for s in data["sources"]] == ["模型微调"]
    human = model.messages[1].content
    assert "[1] 模型微调" in human and "[2]" not in human


def test_ask_accepts_list_content(client: TestClient, model: FakeModel) -> None:
    model.reply = [{"type": "text", "text": "分段"}, {"type": "text", "text": "回答"}]
    data = client.post("/api/ask", json={"question": "什么是检索增强"}).json()
    assert data["answer"] == "分段回答"


def test_ask_model_failure_is_502(client: TestClient, model: FakeModel) -> None:
    model.error = RuntimeError("401 密钥无效")
    resp = client.post("/api/ask", json={"question": "什么是检索增强"})
    assert resp.status_code == 502
    assert "401 密钥无效" in resp.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [{"question": ""}, {"question": "   "}, {"question": "问" * 501}, {"question": "问", "limit": 11}, {}],
)
def test_ask_rejects_invalid_requests(client: TestClient, model: FakeModel, body: dict) -> None:
    assert client.post("/api/ask", json=body).status_code == 422
    assert model.messages is None
