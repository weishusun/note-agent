"""提取层 extractor / models。全程 mock LLM,不碰真实 API、不碰网络。

mock 方式:monkeypatch 掉 extractor.get_extract_model / get_classify_model,
让它们返回一个假模型。假模型的 with_structured_output 返回一个假 runnable,
runnable.invoke 按预设脚本逐次返回 include_raw=True 形态的字典。
"""

import pytest
from pydantic import ValidationError

from app.extract import extractor
from app.extract.models import ClassifyResult, NoteDraft


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

    def invoke(self, messages: object) -> object:
        idx = min(self.calls, len(self.outputs) - 1)
        self.calls += 1
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
    monkeypatch.setattr(extractor, "get_classify_model", lambda override=None: _FakeModel(runnable))
    return runnable


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


# ---------- 1. leaves 为空 ----------


def test_empty_leaves_goes_suggested_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = ClassifyResult(
        category=None, suggested_domain="量子力学", tags=[], confidence=0.7, reason="x"
    )
    runnable = _patch_classify(monkeypatch, [_ok(raw)])

    result, usage = extractor.classify(_draft(), leaves=[], tags=[], known_domains=[])

    assert result.category is None
    assert result.suggested_domain == "量子力学"
    assert result.confidence == 0.0  # suggested_domain 路径强制 0
    assert usage["model"] == "fake-model"
    assert runnable.calls == 1  # 无需语义重试


# ---------- 2. category 非法 → 重试 → 仍非法 → None ----------


def test_illegal_category_retries_then_nulls(monkeypatch: pytest.MonkeyPatch) -> None:
    leaves = ["physics/quantum-mechanics"]
    bad1 = ClassifyResult(category="physics/nope", suggested_domain="量子力学", confidence=0.9)
    bad2 = ClassifyResult(category="physics/still-wrong", suggested_domain="量子力学", confidence=0.9)
    runnable = _patch_classify(monkeypatch, [_ok(bad1), _ok(bad2)])

    result, usage = extractor.classify(_draft(), leaves=leaves, tags=[], known_domains=[])

    assert result.category is None
    assert result.confidence == 0.0
    # 两次调用的 token 累加
    assert usage["input_tokens"] == 20 and usage["output_tokens"] == 10
    assert runnable.calls == 2  # 一次语义重试


def test_legal_category_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    leaves = ["physics/quantum-mechanics"]
    raw = ClassifyResult(
        category="physics/quantum-mechanics", suggested_domain=None, confidence=0.83
    )
    runnable = _patch_classify(monkeypatch, [_ok(raw)])

    result, _usage = extractor.classify(_draft(), leaves=leaves, tags=[], known_domains=[])

    assert result.category == "physics/quantum-mechanics"
    assert result.suggested_domain is None
    assert result.confidence == 0.83
    assert runnable.calls == 1


# ---------- 3. 词表外标签被丢弃 ----------


def test_tags_outside_vocab_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    leaves = ["physics/quantum-mechanics"]
    tags = ["算符代数", "待深入"]
    raw = ClassifyResult(
        category="physics/quantum-mechanics",
        tags=["算符代数", "不存在的标签", "待深入"],
        confidence=0.8,
    )
    _patch_classify(monkeypatch, [_ok(raw)])

    result, _usage = extractor.classify(_draft(), leaves=leaves, tags=tags, known_domains=[])

    assert result.tags == ["算符代数", "待深入"]


# ---------- 4. suggested_domain 长度非法 → 重试 ----------


@pytest.mark.parametrize("bad_domain", ["量", "量子力学导论理论体"])  # 1 字 / 9 字
def test_bad_suggested_domain_length_retries(
    monkeypatch: pytest.MonkeyPatch, bad_domain: str
) -> None:
    assert len(bad_domain) in (1, 9)
    bad = ClassifyResult(category=None, suggested_domain=bad_domain, confidence=0.0)
    good = ClassifyResult(category=None, suggested_domain="量子力学", confidence=0.0)
    runnable = _patch_classify(monkeypatch, [_ok(bad), _ok(good)])

    result, _usage = extractor.classify(_draft(), leaves=[], tags=[], known_domains=[])

    assert result.suggested_domain == "量子力学"
    assert runnable.calls == 2


# ---------- 5. 结构化输出解析失败 → 重试一次 → 仍失败抛异常 ----------


def test_parse_failure_retries_once_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    runnable = _patch_classify(monkeypatch, [_fail()])  # 每次都失败

    with pytest.raises(extractor.ExtractError):
        extractor.classify(_draft(), leaves=[], tags=[], known_domains=[])

    assert runnable.calls == 2  # 首次 + 一次重试


# ---------- 6. NoteDraft 字段长度约束 ----------


def test_notedraft_length_constraints() -> None:
    # 合法基线可构造
    _draft()

    with pytest.raises(ValidationError):
        _draft(title="标" * 61)  # >60 字
    with pytest.raises(ValidationError):
        _draft(title="")  # 空标题
    with pytest.raises(ValidationError):
        _draft(summary="概" * 51)  # >50 字
    with pytest.raises(ValidationError):
        _draft(points=["a", "b"])  # <3 条
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
    batch = extractor._DraftBatch(drafts=[_draft(), _draft(title="泡利矩阵")])
    _patch_extract(monkeypatch, [_ok(batch)])

    drafts, _usage = extractor.extract_drafts(image=None, text="一段关于角动量的短文本")

    assert [d.title for d in drafts] == ["角动量算符的对易关系", "泡利矩阵"]


def test_extract_drafts_usage_accumulates(monkeypatch: pytest.MonkeyPatch) -> None:
    batch = extractor._DraftBatch(drafts=[_draft()])
    _patch_extract(monkeypatch, [_ok(batch)])

    _drafts, usage = extractor.extract_drafts(None, "文本")

    assert usage == {"input_tokens": 10, "output_tokens": 5, "model": "fake-model"}


def test_extract_drafts_requires_some_input() -> None:
    with pytest.raises(ValueError):
        extractor.extract_drafts(image=None, text="   ")
    with pytest.raises(ValueError):
        extractor.extract_drafts(image=None, text=None)
