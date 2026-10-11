"""提取层 extractor / models。全程 mock LLM,不碰真实 API、不碰网络。

mock 方式:monkeypatch 掉 extractor.get_extract_model / get_classify_model,
让它们返回一个假模型。假模型的 with_structured_output 返回一个假 runnable,
runnable.invoke 按预设脚本逐次返回 include_raw=True 形态的字典。
"""

import logging

import pytest
from pydantic import ValidationError

from app.extract import extractor
from app.extract.models import NoteDraft


class _RawStub:
    """冒充带 usage_metadata 的 AIMessage,只需要 usage_metadata 属性。"""

    def __init__(self, input_tokens: int = 10, output_tokens: int = 5) -> None:
        self.usage_metadata = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }


def _ok(parsed: object) -> dict:
    return {"raw": _RawStub(), "parsed": parsed, "parsing_error": None}


def _fail() -> dict:
    return {"raw": _RawStub(3, 0), "parsed": None, "parsing_error": ValueError("坏 json")}


class _FakeRunnable:
    def __init__(self, outputs: list) -> None:
        self.outputs = list(outputs)
        self.calls = 0
        self.messages: list = []

    def invoke(self, messages: object) -> object:
        idx = min(self.calls, len(self.outputs) - 1)
        self.calls += 1
        self.messages.append(messages)
        out = self.outputs[idx]
        if isinstance(out, Exception):
            raise out
        return out


class _FakeModel:
    model_name = "fake-model"

    def __init__(self, runnable: _FakeRunnable) -> None:
        self._runnable = runnable

    def with_structured_output(
        self, schema: object, method: str | None = None, include_raw: bool = False
    ):
        self.schema = schema
        self.method = method
        return self._runnable


def _patch_classify(monkeypatch: pytest.MonkeyPatch, outputs: list) -> _FakeRunnable:
    runnable = _FakeRunnable(outputs)
    model = _FakeModel(runnable)
    runnable.model = model  # 让测试能看到绑定了哪个 schema
    monkeypatch.setattr(extractor, "get_classify_model", lambda override=None: model)
    return runnable


def _batch(*drafts: NoteDraft, **overrides) -> "extractor._DraftBatch":
    """把草稿包成模型原样返回的形态;overrides 覆盖每条草稿的字段(可超出 NoteDraft 上限)。"""
    return extractor._DraftBatch(
        topic=None, concepts=[{**d.model_dump(), **overrides} for d in drafts]
    )


def _concepts(*args, **kwargs) -> tuple[list[NoteDraft], dict]:
    """extract_drafts 的概念笔记那一半；这个文件里大多数测试只关心它。"""
    extraction, usage = extractor.extract_drafts(*args, **kwargs)
    return extraction.concepts, usage


def _patch_extract(monkeypatch: pytest.MonkeyPatch, outputs: list) -> _FakeRunnable:
    runnable = _FakeRunnable(outputs)
    monkeypatch.setattr(extractor, "get_extract_model", lambda override=None: _FakeModel(runnable))
    return runnable


def _draft(**kwargs) -> NoteDraft:
    base = dict(
        title="角动量算符的对易关系",
        summary="三个角动量分量互不对易。",
        points=["[Lx, Ly] = iħLz", "三分量无法同时确定", "L² 与任一分量对易"],
        aliases=["angular momentum commutator"],
        related=["不确定性原理"],
    )
    base.update(kwargs)
    return NoteDraft(**base)


# ---------- 6. NoteDraft 字段长度约束 ----------


def test_notedraft_length_constraints() -> None:
    # 合法基线可构造
    _draft()

    with pytest.raises(ValidationError):
        _draft(title="标" * 61)  # >60 字
    with pytest.raises(ValidationError):
        _draft(title="")  # 空标题
    with pytest.raises(ValidationError):
        _draft(summary="概" * 81)  # >80 字
    with pytest.raises(ValidationError):
        _draft(points=[])  # 0 条(下限是 1)
    with pytest.raises(ValidationError):
        _draft(points=["a", "b", "c", "d", "e", "f", "g"])  # >6 条
    with pytest.raises(ValidationError):
        _draft(points=["点" * 41, "b", "c"])  # 单条 >40 字
    with pytest.raises(ValidationError):
        _draft(aliases=["a", "b", "c", "d", "e", "f"])  # >5 个
    with pytest.raises(ValidationError):
        _draft(related=["a", "b", "c", "d", "e", "f"])  # >5 个


# ---------- 额外:extract_drafts 主路径与入参校验 ----------


def test_extract_drafts_returns_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    batch = _batch(_draft(), _draft(title="泡利矩阵"))
    _patch_extract(monkeypatch, [_ok(batch)])

    drafts, _usage = _concepts(image=None, text="一段关于角动量的短文本")

    assert [d.title for d in drafts] == ["角动量算符的对易关系", "泡利矩阵"]


def test_extract_drafts_usage_accumulates(monkeypatch: pytest.MonkeyPatch) -> None:
    batch = _batch(_draft())
    _patch_extract(monkeypatch, [_ok(batch)])

    _drafts, usage = _concepts(None, "文本")

    assert usage == {"input_tokens": 10, "output_tokens": 5, "model": "fake-model"}


# ---------- 真实接口上实测过的失败:超长字段不得让整次提取失败 ----------


def test_overlong_fields_are_compressed_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    long_summary = "概" * 100
    long_point = "点" * 55
    _patch_extract(
        monkeypatch, [_ok(_batch(_draft(), summary=long_summary, points=[long_point, "短要点"]))]
    )
    compressor = _patch_classify(
        monkeypatch, [_ok(extractor._Compressed(items=["压缩后的概要", "压缩后的要点"]))]
    )

    drafts, _usage = _concepts(None, "文本")

    assert drafts[0].summary == "压缩后的概要"
    assert drafts[0].points == ["压缩后的要点", "短要点"]
    # 一次调用压完所有超长文本,且把各自的上限告诉了模型
    assert compressor.calls == 1
    body = compressor.messages[0][0].content
    assert f"1. (上限 80 字) {long_summary}" in body
    assert f"2. (上限 40 字) {long_point}" in body


@pytest.mark.parametrize(
    "compress_output",
    [
        _fail(),  # 压缩调用本身失败
        _ok(extractor._Compressed(items=["只回了一条"])),  # 条数对不上
        _ok(extractor._Compressed(items=["仍" * 200, "仍" * 200])),  # 压完还是超长
    ],
)
def test_compress_failure_falls_back_to_truncation(
    monkeypatch: pytest.MonkeyPatch, compress_output: dict
) -> None:
    _patch_extract(
        monkeypatch, [_ok(_batch(_draft(), summary="概" * 100, points=["点" * 55]))]
    )
    _patch_classify(monkeypatch, [compress_output])

    drafts, _usage = _concepts(None, "文本")

    assert drafts[0].summary == "概" * 79 + "…"
    assert drafts[0].points == ["点" * 39 + "…"]


def test_no_compress_call_when_everything_fits(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_extract(monkeypatch, [_ok(_batch(_draft()))])
    compressor = _patch_classify(monkeypatch, [_fail()])

    extractor.extract_drafts(None, "文本")

    assert compressor.calls == 0


def test_excess_counts_are_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    wire = _batch(
        *[_draft(title=f"草稿{i}") for i in range(10)],
        points=[f"要点{i}" for i in range(9)] + ["  "],
        aliases=["RAG", " RAG ", "", "a", "b", "c", "d", "e"],
        related=[f"概念{i}" for i in range(8)],
    )
    _patch_extract(monkeypatch, [_ok(wire)])

    drafts, _usage = _concepts(None, "文本")

    assert [d.title for d in drafts] == [f"草稿{i}" for i in range(8)]
    assert drafts[0].points == [f"要点{i}" for i in range(6)]
    assert drafts[0].aliases == ["RAG", "a", "b", "c", "d"]  # 去空、去重、最多 5 个
    assert len(drafts[0].related) == 5


def test_empty_points_fall_back_to_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_extract(monkeypatch, [_ok(_batch(_draft(), points=[]))])

    drafts, _usage = _concepts(None, "文本")

    assert drafts[0].points == [drafts[0].summary]


def test_blank_title_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_extract(monkeypatch, [_ok(_batch(_draft(), title="  "))])

    with pytest.raises(extractor.ExtractError, match="空的标题或概要"):
        extractor.extract_drafts(None, "文本")


# ---------- 重试必须告诉模型错在哪 ----------


def test_parse_failure_retry_carries_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    runnable = _patch_extract(monkeypatch, [_fail(), _ok(_batch(_draft()))])

    drafts, _usage = _concepts(None, "文本")

    assert len(drafts) == 1
    first, second = runnable.messages
    assert len(second) == len(first) + 1
    assert "坏 json" in second[-1].content
    assert "没有通过校验" in second[-1].content


def test_extract_error_message_is_short(monkeypatch: pytest.MonkeyPatch) -> None:
    """ExtractError 的消息会直接显示在界面上,不能带模型返回的全文。"""
    _patch_extract(monkeypatch, [_fail()])

    with pytest.raises(extractor.ExtractError) as info:
        extractor.extract_drafts(None, "文本")

    assert str(info.value) == "模型输出不合法,已重试一次仍失败: 坏 json"


def test_extract_drafts_requires_some_input() -> None:
    with pytest.raises(ValueError):
        extractor.extract_drafts(image=None, text="   ")
    with pytest.raises(ValueError):
        extractor.extract_drafts(image=None, text=None)


# ---------- P1:extract 提炼质量(只测校验逻辑,不调 API) ----------


def test_title_source_context_is_prompt_only_no_code_cleaning() -> None:
    # 决定:标题去语境(去掉「阶段1:」「(提示工程)」等)只靠 extract_zh.md 的 prompt 约束,
    # 代码层不做清洗——冒号/括号在标题里是合法字符,NoteDraft 既不拒绝也不改写。
    # 这里断言的正是「代码层不插手」,真正的效果由 prompt 保证、在 evals 里观察。
    d = _draft(title="阶段1：Prompt Engineering（提示工程）")
    assert d.title == "阶段1：Prompt Engineering（提示工程）"


def test_long_alias_emits_warning(caplog: pytest.LogCaptureFixture) -> None:
    long_alias = "RAG（Retrieval-Augmented Generation 检索增强生成技术全称）"
    assert len(long_alias) > 20
    draft = _draft(aliases=[long_alias])

    with caplog.at_level(logging.WARNING, logger="app.extract.extractor"):
        extractor._warn_draft_quality(draft)

    assert any("alias 过长" in r.getMessage() for r in caplog.records)


def test_short_aliases_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    draft = _draft(aliases=["RAG", "检索增强生成", "角动量对易子"])  # 均 ≤20 字

    with caplog.at_level(logging.WARNING, logger="app.extract.extractor"):
        extractor._warn_draft_quality(draft)

    assert caplog.records == []


# ---------- points 条数下限放宽到 1 ----------


def test_notedraft_allows_single_point() -> None:
    d = _draft(points=["只说了一件事,就只写一条"])
    assert d.points == ["只说了一件事,就只写一条"]


def test_notedraft_rejects_zero_points() -> None:
    with pytest.raises(ValidationError):
        _draft(points=[])


# ---------- 主题笔记 + 概念笔记(SCHEMA 2.3) ----------

TOPIC = {
    "title": "大模型的训练流程",
    "summary": "从随机初始化到可用的大模型要经过的几个训练阶段。",
    "content": "1. **文字接龙训练** ← 非结构化文本数据\n2. **SFT** ← 高质量人工标注数据",
    "supplement": "这是典型的训练流程。",
}


def _wire(topic: dict | None, *concepts: dict) -> "extractor._DraftBatch":
    return extractor._DraftBatch.model_validate({"topic": topic, "concepts": list(concepts)})


def _concept(**overrides) -> dict:
    return {**_draft().model_dump(), **overrides}


def test_extraction_returns_topic_and_concepts(monkeypatch: pytest.MonkeyPatch) -> None:
    wire = _wire(TOPIC, _concept(title="SFT", supplement=["SFT 即监督微调"]), _concept(title="RLHF"))
    _patch_extract(monkeypatch, [_ok(wire)])

    extraction, _usage = extractor.extract_drafts(b"\x89PNG", None)

    assert extraction.topic is not None
    assert extraction.topic.model_dump() == TOPIC
    assert [c.title for c in extraction.concepts] == ["SFT", "RLHF"]
    assert extraction.concepts[0].supplement == ["SFT 即监督微调"]
    assert extraction.concepts[1].supplement == []


def test_single_concept_input_has_no_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_extract(monkeypatch, [_ok(_wire(None, _concept(title="LoRA")))])
    extraction, _usage = extractor.extract_drafts(None, "LoRA 是……")
    assert extraction.topic is None
    assert [c.title for c in extraction.concepts] == ["LoRA"]


def test_topic_that_duplicates_its_only_concept_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """整份输入只讲一个概念，模型却两种都出了：只留概念笔记。"""
    wire = _wire({**TOPIC, "title": "lora"}, _concept(title="LoRA"))
    _patch_extract(monkeypatch, [_ok(wire)])
    extraction, _usage = extractor.extract_drafts(None, "LoRA 是……")
    assert extraction.topic is None and len(extraction.concepts) == 1


def test_topic_only_is_fine_but_nothing_at_all_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_extract(monkeypatch, [_ok(_wire(TOPIC))])
    extraction, _usage = extractor.extract_drafts(None, "一段没有可拆概念的文字")
    assert extraction.topic is not None and extraction.concepts == []

    _patch_extract(monkeypatch, [_ok(_wire(None))])
    with pytest.raises(extractor.ExtractError, match="没有返回任何笔记"):
        extractor.extract_drafts(None, "文本")
    # 主题笔记没有内容等于没有
    _patch_extract(monkeypatch, [_ok(_wire({**TOPIC, "content": "  "}))])
    with pytest.raises(extractor.ExtractError, match="没有返回任何笔记"):
        extractor.extract_drafts(None, "文本")


def test_wire_format_tolerates_what_the_real_model_sends() -> None:
    """真实接口上都出现过：列表字段写成空字符串 / 单个字符串 / null，topic 写成空对象。"""
    wire = extractor._DraftBatch.model_validate(
        {
            "topic": {},
            "concepts": [
                _concept(supplement="", aliases=None, related="不确定性原理", points="只有一条要点"),
            ],
        }
    )
    assert wire.topic is None
    concept = wire.concepts[0]
    assert (concept.supplement, concept.aliases) == ([], [])
    assert (concept.related, concept.points) == (["不确定性原理"], ["只有一条要点"])

    topic = extractor._DraftBatch.model_validate(
        {"topic": {**TOPIC, "supplement": None, "content": ["第一行", "第二行"]}, "concepts": None}
    )
    assert (topic.topic.supplement, topic.topic.content, topic.concepts) == ("", "第一行\n第二行", [])


def test_both_wire_fields_are_required() -> None:
    """都设成可选时，真实模型会直接返回一个空对象。"""
    assert set(extractor._DraftBatch.model_json_schema()["required"]) == {"topic", "concepts"}
    with pytest.raises(ValidationError):
        extractor._DraftBatch.model_validate({})


def test_title_parentheses_move_into_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    wire = _wire(
        TOPIC,
        _concept(title="对齐 (RLHF/DPO)", aliases=["Alignment", "rlhf"]),
        _concept(title="SFT（监督微调）", aliases=["SFT", "Supervised Fine-Tuning"]),
        _concept(title="f(x) 的导数", aliases=[]),  # 括号不在结尾：不动
    )
    _patch_extract(monkeypatch, [_ok(wire)])

    extraction, _usage = extractor.extract_drafts(None, "文本")
    first, second, third = extraction.concepts

    assert (first.title, first.aliases) == ("对齐", ["RLHF", "DPO", "Alignment", "rlhf"])
    # 与标题相同的别名没有意义，去掉
    assert (second.title, second.aliases) == ("SFT", ["监督微调", "Supervised Fine-Tuning"])
    assert third.title == "f(x) 的导数"


def test_overlong_topic_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    wire = _wire({**TOPIC, "summary": "概" * 100, "content": "行\n" * 3000, "supplement": "补" * 400})
    _patch_extract(monkeypatch, [_ok(wire)])
    compressor = _patch_classify(
        monkeypatch, [_ok(extractor._Compressed(items=["压缩后的概要", "压缩后的补充"]))]
    )

    extraction, _usage = extractor.extract_drafts(None, "文本")
    topic = extraction.topic

    assert (topic.summary, topic.supplement) == ("压缩后的概要", "压缩后的补充")
    # 内容是结构化正文：不送去压缩，超长只截断并说明
    assert len(topic.content) <= 4000 and topic.content.endswith("完整内容见原始输入)")
    body = compressor.messages[0][0].content
    assert "(上限 80 字)" in body and "(上限 300 字)" in body and "行\n行" not in body


def test_supplement_is_trimmed_like_other_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    wire = _wire(None, _concept(supplement=["甲", " 甲 ", "", "乙", "丙", "丁", "戊"]))
    _patch_extract(monkeypatch, [_ok(wire)])
    extraction, _usage = extractor.extract_drafts(None, "文本")
    assert extraction.concepts[0].supplement == ["甲", "乙", "丙", "丁"]  # 去空、去重、最多 4 条


def test_extraction_always_uses_json_mode_and_spells_out_the_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """智谱的函数调用处理不了嵌套结构：提取固定走 json_mode，输出结构写在提示词里。"""
    runnable = _FakeRunnable([_ok(_wire(TOPIC))])
    model = _FakeModel(runnable)
    monkeypatch.setattr(extractor, "get_extract_model", lambda override=None: model)

    extractor.extract_drafts(None, "文本")

    assert model.method == "json_mode"
    system = runnable.messages[0][0].content
    assert '{"topic": {"title"' in system and '"concepts": [' in system
