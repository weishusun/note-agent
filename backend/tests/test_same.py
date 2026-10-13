"""app/extract/same.py：让文本模型判断两条笔记是不是同一个概念。模型调用打桩。"""

import pytest

from app.extract import same
from app.extract.extractor import ExtractError
from app.extract.same import Card, judge_same_concept

NEW = Card("有监督微调", (), "用标注数据继续训练。", "- 需要高质量样本\n" + "长" * 600)
OLD = Card("SFT", ("Supervised Fine-Tuning", "监督微调"), "用指令和回答成对的数据微调。")


class FakeModel:
    model_name = "fake-text"

    def __init__(self, reply: dict) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def with_structured_output(self, schema, method=None, include_raw=False):
        assert (method, include_raw) == ("json_mode", True)
        outer = self

        class Runnable:
            def invoke(self, messages):
                outer.prompts.append(messages[0].content)
                try:
                    return {"parsed": schema(**outer.reply), "raw": None, "parsing_error": None}
                except Exception as exc:
                    return {"parsed": None, "raw": None, "parsing_error": exc}

        return Runnable()


def _patch(monkeypatch: pytest.MonkeyPatch, reply: dict) -> FakeModel:
    fake = FakeModel(reply)
    monkeypatch.setattr(same, "get_classify_model", lambda override=None: fake)
    return fake


def test_pairs_are_judged_in_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _patch(monkeypatch, {"same": [True, "false"]})  # 布尔写成字符串也认
    assert judge_same_concept([(NEW, OLD), (OLD, NEW)]) == [True, False]

    [prompt] = model.prompts
    assert "下面有 2 对笔记" in prompt and "一共 2 个" in prompt
    assert "## 第 1 对\n【新】\n标题:有监督微调\n概要:用标注数据继续训练。\n内容:- 需要高质量样本 长长" in prompt
    assert "【旧】\n标题:SFT\n别名:Supervised Fine-Tuning、监督微调\n概要:用指令和回答成对的数据微调。\n\n## 第 2 对" in prompt
    assert "长" * 401 not in prompt  # 正文只给一小段


def test_no_pairs_means_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _patch(monkeypatch, {"same": []})
    assert judge_same_concept([]) == [] and model.prompts == []


@pytest.mark.parametrize("reply", [{"same": [True]}, {"same": [True, False, True]}, {}])
def test_wrong_number_of_verdicts_is_an_error(monkeypatch: pytest.MonkeyPatch, reply: dict) -> None:
    _patch(monkeypatch, reply)
    with pytest.raises(ExtractError, match="应该是 2 个"):
        judge_same_concept([(NEW, OLD), (OLD, NEW)])
