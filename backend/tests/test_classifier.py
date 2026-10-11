"""extract/classifier.py：一次调用给一批笔记归类。模型调用打桩。"""

import pytest

from app.extract import classifier
from app.extract.classifier import NoteBrief, Placement, _WireBatch, classify_notes
from app.extract.extractor import ExtractError
from app.vault.models import Tree, TreeChild, TreeParent

BRIEFS = [
    NoteBrief(title="LoRA", summary="参数高效微调方法。", points=["冻结预训练权重", "只训练低秩矩阵"]),
    NoteBrief(title="RAG", summary="先检索再生成。"),
    NoteBrief(title="快速排序", summary="分治的原地排序。"),
]
TREE = Tree(
    total=12,
    inbox=1,
    categories=[
        TreeParent(
            name="人工智能",
            locked=False,
            count=11,
            children=[
                TreeChild(name="模型训练", path="人工智能/模型训练", locked=True, count=9),
                TreeChild(name="检索增强", path="人工智能/检索增强", locked=False, count=2),
            ],
        ),
        TreeParent(name="英语", locked=True, count=0, children=[]),
    ],
)


class FakeModel:
    """按脚本逐次返回；最后一个输出会被重复使用。"""

    model_name = "fake-text"

    def __init__(self, outputs: list) -> None:
        self.outputs = outputs
        self.calls: list = []
        self.bound: dict = {}

    def with_structured_output(self, schema, **kwargs):
        self.bound = {"schema": schema, **kwargs}
        return self

    def invoke(self, messages):
        out = self.outputs[min(len(self.calls), len(self.outputs) - 1)]
        self.calls.append(messages)
        if isinstance(out, Exception):
            raise out
        return out


def _items(*rows: tuple) -> dict:
    items = [dict(zip(("index", "parent", "child", "confidence"), row)) for row in rows]
    return {"parsed": _WireBatch.model_validate({"items": items}), "raw": None, "parsing_error": None}


@pytest.fixture
def use(monkeypatch: pytest.MonkeyPatch):
    def _use(*outputs) -> FakeModel:
        model = FakeModel(list(outputs))
        monkeypatch.setattr(classifier, "get_classify_model", lambda override=None: model)
        return model

    return _use


def test_placements_follow_brief_order_not_answer_order(use) -> None:
    model = use(
        _items(
            (3, "算法", "排序", 0.95),
            (1, "人工智能", "模型训练", 0.9),
            (2, " 人工智能 ", " 检索增强 ", 0.85),
        )
    )

    result = classify_notes(BRIEFS, TREE)

    assert result == [
        Placement(parent="人工智能", child="模型训练", confidence=0.9),
        Placement(parent="人工智能", child="检索增强", confidence=0.85),
        Placement(parent="算法", child="排序", confidence=0.95),
    ]
    assert [p.category for p in result] == ["人工智能/模型训练", "人工智能/检索增强", "算法/排序"]
    assert len(model.calls) == 1
    # 关闭思考后 function calling 不可用：必须走 json_mode
    assert model.bound["method"] == "json_mode"


def test_prompt_shows_the_tree_and_every_note(use) -> None:
    model = use(_items((1, "a", "b", 1), (2, "a", "b", 1), (3, "a", "b", 1)))
    classify_notes(BRIEFS, TREE)
    prompt = model.calls[0][0].content

    assert "- 人工智能: 模型训练(9 条)、检索增强(2 条)" in prompt
    assert "- 英语: (还没有小块)" in prompt
    assert "1. 标题:LoRA\n   概要:参数高效微调方法。\n   要点:冻结预训练权重;只训练低秩矩阵" in prompt
    assert "2. 标题:RAG\n   概要:先检索再生成。\n3. 标题:快速排序" in prompt
    assert "一共 3 条" in prompt


def test_empty_tree_tells_the_model_to_start_one(use) -> None:
    model = use(_items((1, "人工智能", "模型微调", 0.9)))
    classify_notes(BRIEFS[:1], Tree(total=0, inbox=0, categories=[]))
    assert "(还没有任何分类,由你来建第一批)" in model.calls[0][0].content


def test_missing_notes_are_asked_for_once_more(use) -> None:
    model = use(
        _items((1, "人工智能", "模型训练", 0.9)),
        _items((2, "人工智能", "检索增强", 0.8), (3, "算法", "排序", 0.9)),
    )

    result = classify_notes(BRIEFS, TREE)

    assert [p.category for p in result] == ["人工智能/模型训练", "人工智能/检索增强", "算法/排序"]
    assert len(model.calls) == 2
    assert "漏掉了第 2、3 条" in model.calls[1][-1].content


def test_still_missing_after_retry_becomes_unplaced_not_an_error(use) -> None:
    model = use(_items((1, "人工智能", "模型训练", 0.9)))  # 每次都只答第 1 条

    result = classify_notes(BRIEFS, TREE)

    assert result == [
        Placement(parent="人工智能", child="模型训练", confidence=0.9),
        Placement(),
        Placement(),
    ]
    assert result[1].category is None
    assert len(model.calls) == 2  # 只重问一次


def test_bad_names_and_odd_answers_are_tolerated(use) -> None:
    use(
        _items(
            (1, "人工智能/大模型", "模型训练", 0.9),  # 名字里有斜杠
            (2, "人工智能", "", 0.9),  # 缺小块
            (3, "算法", "一个超过十二个字的非常长的小块名字", 0.9),  # 太长
            (1, "算法", "排序", 0.9),  # 同一个 index 第二次出现：忽略
            (9, "算法", "排序", 0.9),  # 范围外：忽略
        )
    )
    assert classify_notes(BRIEFS, TREE) == [Placement(), Placement(), Placement()]


@pytest.mark.parametrize(
    "raw, expected", [(1.7, 1.0), (-0.2, 0.0), ("0.75", 0.75), ("很有把握", 0.0), (None, 0.0)]
)
def test_confidence_is_clamped(use, raw: object, expected: float) -> None:
    use(_items((1, "人工智能", "模型训练", raw)))
    assert classify_notes(BRIEFS[:1], TREE)[0].confidence == expected


def test_no_briefs_means_no_call(use) -> None:
    model = use(_items())
    assert classify_notes([], TREE) == []
    assert model.calls == []


def test_unparseable_output_raises_for_the_caller_to_handle(use) -> None:
    use({"parsed": None, "raw": None, "parsing_error": ValueError("不是 JSON")})
    with pytest.raises(ExtractError, match="不是 JSON"):
        classify_notes(BRIEFS, TREE)
