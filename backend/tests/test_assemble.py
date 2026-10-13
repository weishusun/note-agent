"""ingest/assemble.py：两种笔记的正文结构、模型补充区块、进不进待整理、双链只指向真实文件。"""

from datetime import datetime

import pytest

from app import config
from app.extract.models import NoteDraft, TopicDraft
from app.ingest.assemble import SUPPLEMENT_MARKER, assemble_concept, assemble_topic
from app.vault.store import VaultStore
from tests.conftest import TZ

NOW = datetime(2026, 8, 6, 9, 30, 0, tzinfo=TZ)
CATEGORY = "人工智能/模型训练"
IMAGE = "assets/2026-08-06-093000.png"


def _draft(**overrides) -> NoteDraft:
    fields = dict(
        title="RLHF",
        summary="训练流程中 SFT 之后的阶段，与自生成数据交互。",
        points=["训练流程中 SFT 之后的阶段", "与自生成数据双向交互"],
        aliases=["基于人类反馈的强化学习"],
        related=["SFT", "LLM"],
    )
    fields.update(overrides)
    return NoteDraft(**fields)


def _topic(**overrides) -> TopicDraft:
    fields = dict(
        title="大模型的训练流程",
        summary="从随机初始化的 Transformer 到可用的大模型要经过的几个训练阶段。",
        content="1. **文字接龙训练** ← 非结构化文本数据\n2. **SFT** ← 高质量人工标注数据\n3. **RLHF** ⇄ 自生成数据",
    )
    fields.update(overrides)
    return TopicDraft(**fields)


def _concept(draft: NoteDraft | None = None, **kwargs):
    kwargs.setdefault("category", CATEGORY)
    kwargs.setdefault("confidence", 0.9)
    kwargs.setdefault("now", NOW)
    if "kind" not in kwargs:
        kwargs.update(kind="screenshot", asset_path=IMAGE)
    return assemble_concept(draft or _draft(), **kwargs)


def _topic_note(topic: TopicDraft | None = None, **kwargs):
    kwargs.setdefault("category", CATEGORY)
    kwargs.setdefault("confidence", 0.9)
    kwargs.setdefault("now", NOW)
    if "kind" not in kwargs:
        kwargs.update(kind="screenshot", asset_path=IMAGE)
    return assemble_topic(topic or _topic(), **kwargs)


# ---------- 主题笔记 ----------


def test_topic_note_keeps_structure_and_lists_its_concepts() -> None:
    note = _topic_note(concept_stems=["文字接龙训练", "SFT", "RLHF"])
    assert note.body == (
        "## 内容\n"
        "1. **文字接龙训练** ← 非结构化文本数据\n"
        "2. **SFT** ← 高质量人工标注数据\n"
        "3. **RLHF** ⇄ 自生成数据\n"
        "\n"
        "## 概念\n"
        "- [[文字接龙训练]]\n"
        "- [[SFT]]\n"
        "- [[RLHF]]\n"
        "\n"
        "## 原始输入\n"
        f"![[{IMAGE}]]\n"
    )
    fm = note.frontmatter
    assert (fm.type, fm.title, fm.aliases, fm.tags) == ("topic", "大模型的训练流程", [], [])
    assert fm.summary == "从随机初始化的 Transformer 到可用的大模型要经过的几个训练阶段。"
    assert (fm.source.kind, fm.source.ref) == ("screenshot", IMAGE)
    assert (fm.id, fm.category_locked, fm.duplicate_of) == (None, False, None)


def test_topic_note_without_concepts_or_supplement_has_no_empty_sections() -> None:
    body = _topic_note().body
    assert "## 概念" not in body and "## 模型补充" not in body
    assert body.startswith("## 内容\n") and body.endswith(f"## 原始输入\n![[{IMAGE}]]\n")


def test_topic_supplement_goes_in_its_own_marked_section() -> None:
    body = _topic_note(_topic(supplement="这是典型的三阶段训练流程。"), concept_stems=["SFT"]).body
    assert (
        "## 概念\n- [[SFT]]\n\n"
        "## 模型补充\n"
        "> 以下内容由模型根据自身知识补充，不是来自你的资料。\n\n"
        "这是典型的三阶段训练流程。\n\n"
        "## 原始输入\n"
    ) in body


# ---------- 概念笔记 ----------


def test_concept_note_with_a_topic_links_back_and_omits_the_source() -> None:
    note = _concept(topic_stem="大模型的训练流程", link_targets=["SFT", "预训练"])
    assert note.body == (
        "## 要点\n"
        "- 训练流程中 SFT 之后的阶段\n"
        "- 与自生成数据双向交互\n"
        "\n"
        "## 关联\n"
        "- [[大模型的训练流程]]\n"  # 第一条是它出自的主题笔记
        "- [[SFT]]\n"  # LLM 不是真实存在的文件名：不写
    )
    fm = note.frontmatter
    assert (fm.type, fm.aliases) == ("concept", ["基于人类反馈的强化学习"])
    # 原始输入只放在主题笔记里，但来源信息仍然记在 frontmatter
    assert (fm.source.kind, fm.source.ref) == ("screenshot", IMAGE)


def test_concept_note_without_a_topic_carries_the_source() -> None:
    """输入只讲一个概念：没有主题笔记，原始输入放在概念笔记末尾。"""
    text = "第一行" + "长" * 150 + "\n\n第三行"
    note = _concept(_draft(related=[]), kind="text", text=f"  {text}\n")

    assert note.frontmatter.source.ref == text[:100]
    assert note.body == (
        "## 要点\n- 训练流程中 SFT 之后的阶段\n- 与自生成数据双向交互\n\n"
        "## 关联\n（暂无）\n\n"
        "## 原始输入\n> 第一行" + "长" * 150 + "\n>\n> 第三行\n"
    )


def test_concept_supplement_is_never_mixed_into_points() -> None:
    """不变量 14：模型自己的知识只能出现在「模型补充」区块，且带固定的第一行。"""
    note = _concept(
        _draft(supplement=["RLHF 即基于人类反馈的强化学习", "先训练奖励模型，再用它优化策略"]),
        topic_stem="大模型的训练流程",
    )
    points, rest = note.body.split("## 模型补充\n", 1)
    assert "RLHF 即" not in points
    assert rest.startswith(
        f"{SUPPLEMENT_MARKER}\n\n- RLHF 即基于人类反馈的强化学习\n- 先训练奖励模型，再用它优化策略\n\n## 关联\n"
    )
    assert SUPPLEMENT_MARKER == "> 以下内容由模型根据自身知识补充，不是来自你的资料。"


def test_related_links_only_real_files() -> None:
    draft = _draft(related=["sft", "不存在的概念", "RLHF", "问: A/B?", "SFT"])
    note = _concept(draft, topic_stem="主题", link_targets=["SFT", "RLHF", "问 AB", "主题"])
    related = note.body.split("## 关联\n", 1)[1]
    # 大小写不敏感对齐到真实文件名、去重、不链接自己、文件名按安全化规则匹配
    assert related == "- [[主题]]\n- [[SFT]]\n- [[问 AB]]\n"


# ---------- 进不进待整理（两种笔记共用） ----------


@pytest.mark.parametrize("build", [_concept, _topic_note])
def test_placement(build) -> None:
    assert config.INBOX_CONFIDENCE_THRESHOLD == 0.6
    fm = build(confidence=0.9).frontmatter
    assert (fm.status, fm.category, fm.confidence) == ("active", CATEGORY, 0.9)
    assert build(confidence=0.6).frontmatter.status == "active"
    # 模型拿不准：进待整理，保留它的猜测
    fm = build(confidence=0.59).frontmatter
    assert (fm.status, fm.category, fm.confidence) == ("inbox", CATEGORY, 0.59)
    # 没分上类
    fm = build(category=None, confidence=0.8).frontmatter
    assert (fm.status, fm.category, fm.confidence) == ("inbox", None, 0.0)


def test_default_timestamp_has_timezone() -> None:
    fm = _concept(now=None).frontmatter
    assert fm.created.tzinfo is not None and fm.created == fm.updated


@pytest.mark.parametrize("build", [_concept, _topic_note])
@pytest.mark.parametrize(
    "kwargs, message",
    [
        (dict(kind="screenshot"), "asset_path"),
        (dict(kind="text", text="   "), "非空 text"),
        (dict(kind="file"), "asset_path"),
        (dict(kind="generated", text="记录量子力学"), "暂不支持"),
    ],
)
def test_invalid_source_raises(build, kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build(**kwargs)


def test_assembled_notes_round_trip_through_store(store: VaultStore) -> None:
    concept = _concept(topic_stem="大模型的训练流程", confidence=0.3)
    topic = _topic_note(concept_stems=["RLHF"])
    concept_rel, topic_rel = store.create_note(concept), store.create_note(topic)

    assert (concept_rel.as_posix(), topic_rel.as_posix()) == ("notes/RLHF.md", "notes/大模型的训练流程.md")
    for note, rel in ((concept, concept_rel), (topic, topic_rel)):
        read = store.read_note(rel)
        assert read.frontmatter == note.frontmatter and read.frontmatter.id is not None
        assert read.body.strip() == note.body.strip()
    assert "type: topic" in (store.root / topic_rel).read_text(encoding="utf-8")


# ---------- 长资料的来源写法 ----------


def test_file_source_links_the_saved_original() -> None:
    note = _topic_note(kind="file", asset_path="assets/讲义.pdf")
    assert (note.frontmatter.source.kind, note.frontmatter.source.ref) == ("file", "assets/讲义.pdf")
    assert note.body.endswith("## 原始输入\n![[assets/讲义.pdf]]\n")


def test_long_text_source_quotes_only_the_opening_and_links_the_full_text() -> None:
    text = "第一行\n" + "长" * 5000
    note = _topic_note(kind="text", text=text, asset_path="assets/2026-10-13-190000.txt")
    assert note.frontmatter.source.ref == text[:100]
    original = note.body.split("## 原始输入\n")[1]
    assert original == "> 第一行\n> " + "长" * 196 + "……\n\n全文：![[assets/2026-10-13-190000.txt]]\n"
