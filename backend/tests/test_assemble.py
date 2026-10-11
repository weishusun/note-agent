"""ingest/assemble.py：正文四段结构、进不进待整理的判定、双链只指向真实文件。"""

from datetime import datetime

import pytest

from app import config
from app.extract.models import NoteDraft
from app.ingest.assemble import assemble_note
from app.vault.store import VaultStore
from tests.conftest import TZ

NOW = datetime(2026, 8, 6, 9, 30, 0, tzinfo=TZ)
RAG = "AI工程/RAG"


def _draft(**overrides) -> NoteDraft:
    fields = dict(
        title="RAG",
        summary="生成前先检索相关片段，拼进上下文再作答。",
        points=["检索质量决定上限", "片段过长会稀释注意力"],
        aliases=["检索增强生成", "Retrieval-Augmented Generation"],
        related=["Prompt Engineering", "Fine-tuning"],
    )
    fields.update(overrides)
    return NoteDraft(**fields)


def _assemble(draft: NoteDraft | None = None, **kwargs):
    kwargs.setdefault("category", RAG)
    kwargs.setdefault("confidence", 0.9)
    kwargs.setdefault("now", NOW)
    if "kind" not in kwargs:
        kwargs.update(kind="text", text="原文")
    return assemble_note(draft or _draft(), **kwargs)


def test_screenshot_body_is_exact_four_sections() -> None:
    note = _assemble(
        kind="screenshot",
        asset_path="assets/2026-08-06-093000.png",
        link_targets=["Prompt Engineering"],
    )
    assert note.body == (
        "## 一句话\n"
        "生成前先检索相关片段，拼进上下文再作答。\n"
        "\n"
        "## 要点\n"
        "- 检索质量决定上限\n"
        "- 片段过长会稀释注意力\n"
        "\n"
        "## 关联\n"
        "- [[Prompt Engineering]]\n"
        "\n"
        "## 原始输入\n"
        "![[assets/2026-08-06-093000.png]]\n"
    )
    fm = note.frontmatter
    assert (fm.source.kind, fm.source.ref) == ("screenshot", "assets/2026-08-06-093000.png")
    assert fm.summary == "生成前先检索相关片段，拼进上下文再作答。"
    assert fm.aliases == ["检索增强生成", "Retrieval-Augmented Generation"]
    assert fm.tags == []
    assert fm.created == NOW and fm.updated == NOW
    assert (fm.id, fm.category_locked) == (None, False)


def test_text_source_is_quoted_and_ref_truncated_to_100() -> None:
    text = "第一行" + "长" * 150 + "\n\n第三行"
    note = _assemble(kind="text", text=f"  {text}\n")

    assert note.frontmatter.source.kind == "text"
    assert note.frontmatter.source.ref == text[:100]
    original = note.body.split("## 原始输入\n", 1)[1]
    assert original == "> 第一行" + "长" * 150 + "\n>\n> 第三行\n"


def test_related_links_only_real_files() -> None:
    draft = _draft(related=["prompt engineering", "不存在的概念", "RAG", "问: A/B?", "Prompt Engineering"])
    note = _assemble(draft, link_targets=["Prompt Engineering", "RAG", "问 AB"])
    related = note.body.split("## 关联\n", 1)[1].split("\n\n", 1)[0]
    # 大小写不敏感对齐到真实文件名、去重、不链接自己、文件名按安全化规则匹配
    assert related == "- [[Prompt Engineering]]\n- [[问 AB]]"


def test_no_linkable_related_writes_placeholder() -> None:
    note = _assemble()
    assert "## 关联\n（暂无）\n\n## 原始输入" in note.body
    assert "[[" not in note.body


def test_confident_placement_is_active() -> None:
    fm = _assemble(confidence=0.9).frontmatter
    assert (fm.status, fm.category, fm.confidence) == ("active", RAG, 0.9)


def test_low_confidence_goes_to_inbox_but_keeps_the_guess() -> None:
    """不变量 10：模型拿不准时进待整理，category 保留它的猜测。"""
    fm = _assemble(confidence=0.59).frontmatter
    assert (fm.status, fm.category, fm.confidence) == ("inbox", RAG, 0.59)


def test_confidence_at_threshold_is_active() -> None:
    assert config.INBOX_CONFIDENCE_THRESHOLD == 0.6
    assert _assemble(confidence=0.6).frontmatter.status == "active"


def test_failed_classification_goes_to_inbox_without_category() -> None:
    fm = _assemble(category=None, confidence=0.8).frontmatter
    assert (fm.status, fm.category, fm.confidence) == ("inbox", None, 0.0)


def test_default_timestamp_has_timezone() -> None:
    fm = _assemble(now=None).frontmatter
    assert fm.created.tzinfo is not None


@pytest.mark.parametrize(
    "kwargs, message",
    [
        (dict(kind="screenshot"), "asset_path"),
        (dict(kind="text", text="   "), "非空 text"),
        (dict(kind="file", asset_path="assets/a.pdf"), "暂不支持"),
        (dict(kind="generated", text="记录量子力学"), "暂不支持"),
    ],
)
def test_invalid_source_raises(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _assemble(**kwargs)


def test_assembled_note_round_trips_through_store(store: VaultStore) -> None:
    note = _assemble(confidence=0.3)
    rel = store.create_note(note)

    assert rel.as_posix() == "notes/RAG.md"  # 待整理的也落在 notes/
    read = store.read_note(rel)
    assert read.frontmatter == note.frontmatter
    assert read.frontmatter.id is not None
    assert read.body.strip() == note.body.strip()
