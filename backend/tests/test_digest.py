"""app/extract/digest.py：长资料的摘要级整理。模型调用全部打桩。"""

from types import SimpleNamespace

import pytest

from app.extract import digest
from app.extract.digest import _WireDigest, digest_document, split_chunks
from app.extract.extractor import ExtractError
from app.extract.models import TOPIC_CONTENT_MAX

DIGEST = {
    "title": "注意力机制综述",
    "summary": "梳理注意力机制从提出到 Transformer 的演进。",
    "abstract": "本文回顾了注意力机制的发展。\n它最早用于机器翻译，后来成为 Transformer 的核心。",
    "outline": [
        {"heading": "## 1. 背景", "points": ["RNN 难以并行", "长距离依赖容易丢失"]},
        {"heading": "2. 自注意力", "points": "每个位置都能直接看到其他所有位置"},
        "3. 结论",
    ],
    "concepts": [
        {
            "title": "自注意力",
            "summary": "序列里每个位置对其他所有位置计算相关性。",
            "points": ["可以并行计算"],
            "aliases": ["Self-Attention"],
            "related": ["Transformer"],
            "supplement": None,
        }
    ],
}


class FakeModel:
    """假的文本模型：分段调用返回「第 n 段的要点」，最后的整理调用返回 DIGEST。"""

    model_name = "fake-text"

    def __init__(self, digest_json: dict | None = None) -> None:
        self.digest_json = digest_json if digest_json is not None else DIGEST
        self.chunk_prompts: list[str] = []
        self.final_messages: list = []
        self.fail_chunks: set[int] = set()

    def invoke(self, messages):
        prompt = messages[0].content
        self.chunk_prompts.append(prompt)
        index = int(prompt.split("第 ")[1].split(" ")[0])
        if index in self.fail_chunks:
            raise RuntimeError("429 限流")
        return SimpleNamespace(content=f"- 第 {index} 段的要点")

    def with_structured_output(self, schema, method=None, include_raw=False):
        assert (schema, method, include_raw) == (_WireDigest, "json_mode", True)
        outer = self

        class Runnable:
            def invoke(self, messages):
                outer.final_messages.append(messages)
                return {"parsed": schema(**outer.digest_json), "raw": None, "parsing_error": None}

        return Runnable()


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> FakeModel:
    fake = FakeModel()
    monkeypatch.setattr(digest, "get_digest_model", lambda override=None: fake)
    return fake


def test_short_document_is_read_in_one_call(model: FakeModel) -> None:
    progress: list[tuple[int, int]] = []
    extraction, info = digest_document(
        "正文" * 500, title_hint="attention-survey", on_progress=lambda d, t: progress.append((d, t))
    )

    assert info == {"chars": 1000, "truncated": False, "chunks": 1}
    assert model.chunk_prompts == [] and progress == []
    system, human = model.final_messages[0]
    assert "摘要级" in system.content
    assert human.content.startswith("文件名:attention-survey\n\n# 资料正文\n正文正文")

    topic = extraction.topic
    assert (topic.title, topic.summary) == ("注意力机制综述", "梳理注意力机制从提出到 Transformer 的演进。")
    # 正文的结构由程序拼：摘要收成一段，大纲编号、去掉模型自己加的井号，容忍各种写法
    assert topic.content == (
        "### 摘要\n本文回顾了注意力机制的发展。 它最早用于机器翻译，后来成为 Transformer 的核心。\n\n"
        "### 大纲\n"
        "1. **1. 背景**\n   - RNN 难以并行\n   - 长距离依赖容易丢失\n"
        "2. **2. 自注意力**\n   - 每个位置都能直接看到其他所有位置\n"
        "3. **3. 结论**"
    )
    assert topic.supplement == ""  # 长资料的主题笔记不要模型补充
    [concept] = extraction.concepts
    assert (concept.title, concept.aliases, concept.related, concept.supplement) == (
        "自注意力",
        ["Self-Attention"],
        ["Transformer"],
        [],
    )


def test_long_document_is_summarised_chunk_by_chunk_then_combined(
    model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(digest, "SINGLE_PASS_CHARS", 100)
    monkeypatch.setattr(digest, "CHUNK_CHARS", 60)
    text = "\n".join(f"第{i}段" + "字" * 40 for i in range(4))  # 4 段，每段 43 字 → 一段一块
    progress: list[tuple[int, int]] = []

    _, info = digest_document(text, on_progress=lambda d, t: progress.append((d, t)))

    assert info["chunks"] == 4
    assert sorted(p.split("# 第 ")[1][0] for p in model.chunk_prompts) == ["1", "2", "3", "4"]
    assert "(共 4 部分)" in model.chunk_prompts[0]
    assert progress[0] == (0, 4) and sorted(progress[1:]) == [(1, 4), (2, 4), (3, 4), (4, 4)]
    # 最后的整理拿到的是各段的要点，按原文顺序，而不是原文
    human = model.final_messages[0][1].content
    assert human.startswith("# 资料各部分的要点(共 4 部分,按原文顺序)")
    order = [human.index(f"## 第 {i} 部分的要点\n- 第 {i} 段的要点") for i in (1, 2, 3, 4)]
    assert order == sorted(order) and "字字字" not in human


def test_a_chunk_that_keeps_failing_fails_the_whole_document(
    model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(digest, "SINGLE_PASS_CHARS", 100)
    monkeypatch.setattr(digest, "CHUNK_CHARS", 60)
    model.fail_chunks = {2}
    with pytest.raises(ExtractError, match="第 2/3 部分"):
        digest_document("\n".join("字" * 45 for _ in range(3)))
    assert model.final_messages == []  # 少一段的摘要不可信，不做最后的整理


def test_oversized_document_is_truncated_and_says_so(
    model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(digest, "DOC_MAX_CHARS", 50)
    extraction, info = digest_document("字" * 80)
    assert info == {"chars": 80, "truncated": True, "chunks": 1}
    assert model.final_messages[0][1].content == "# 资料正文\n" + "字" * 50
    assert extraction.topic.content.endswith("> 这份资料约 80 字,只整理了前 50 字。")


def test_topic_is_kept_even_when_it_shares_its_name_with_the_only_concept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    same_name = {**DIGEST, "title": "自注意力"}
    monkeypatch.setattr(digest, "get_digest_model", lambda override=None: FakeModel(same_name))
    extraction, _ = digest_document("正文" * 100)
    assert extraction.topic is not None and extraction.topic.title == "自注意力"
    assert [c.title for c in extraction.concepts] == ["自注意力"]


def test_an_overfull_outline_loses_points_not_headings(monkeypatch: pytest.MonkeyPatch) -> None:
    bloated = {
        **DIGEST,
        "outline": [{"heading": f"第 {i} 节", "points": ["要点" * 200] * 5} for i in range(1, 13)],
    }
    monkeypatch.setattr(digest, "get_digest_model", lambda override=None: FakeModel(bloated))
    content = digest_document("正文" * 100)[0].topic.content
    assert len(content) <= TOPIC_CONTENT_MAX
    assert all(f"**第 {i} 节**" in content for i in range(1, 13))
    assert "已截断" not in content


def test_digest_without_abstract_or_outline_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    empty = {**DIGEST, "abstract": "", "outline": []}
    monkeypatch.setattr(digest, "get_digest_model", lambda override=None: FakeModel(empty))
    with pytest.raises(ExtractError, match="摘要"):
        digest_document("正文" * 100)


def test_split_chunks_respects_paragraphs_and_hard_splits_long_ones() -> None:
    text = "甲" * 30 + "\n" + "乙" * 30 + "\n" + "丙" * 130 + "\n\n" + "丁" * 10
    chunks = split_chunks(text, 50)
    assert chunks == ["甲" * 30, "乙" * 30, "丙" * 50, "丙" * 50, "丙" * 30 + "\n\n" + "丁" * 10]
    assert all(len(chunk) <= 50 for chunk in chunks)
    assert split_chunks("短文", 50) == ["短文"]
