"""ingest/assemble.py：正文四段结构、落 inbox 的判定、双链只指向真实文件。"""

from datetime import datetime

import pytest

from app import config
from app.extract.models import ClassifyResult, NoteDraft
from app.ingest.assemble import assemble_note
from app.vault.store import VaultStore
from tests.conftest import TZ

NOW = datetime(2026, 8, 6, 9, 30, 0, tzinfo=TZ)


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


def _classified(confidence: float = 0.9) -> ClassifyResult:
    return ClassifyResult(category="ai-engineering/rag", tags=["向量检索"], confidence=confidence)


def _unclassified() -> ClassifyResult:
    return ClassifyResult(category=None, suggested_domain="AI工程", confidence=0.0)


def test_screenshot_body_is_exact_four_sections() -> None:
    note = assemble_note(
        _draft(),
        _classified(),
        kind="screenshot",
        asset_path="assets/2026-08-06-093000.png",
        link_targets=["Prompt Engineering"],
        now=NOW,
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
    assert fm.source.kind == "screenshot"
    assert fm.source.ref == "assets/2026-08-06-093000.png"
    assert fm.summary == "生成前先检索相关片段，拼进上下文再作答。"
    assert fm.aliases == ["检索增强生成", "Retrieval-Augmented Generation"]
    assert fm.tags == ["向量检索"]
    assert fm.created == NOW and fm.updated == NOW
    assert fm.verified is False
    assert fm.id is None


def test_text_source_is_quoted_and_ref_truncated_to_100() -> None:
    text = "第一行" + "长" * 150 + "\n\n第三行"
    note = assemble_note(_draft(), _classified(), kind="text", text=f"  {text}\n", now=NOW)

    assert note.frontmatter.source.kind == "text"
    assert note.frontmatter.source.ref == text[:100]
    original = note.body.split("## 原始输入\n", 1)[1]
    assert original == "> 第一行" + "长" * 150 + "\n>\n> 第三行\n"


def test_related_links_only_real_files() -> None:
    draft = _draft(related=["prompt engineering", "不存在的概念", "RAG", "问: A/B?", "Prompt Engineering"])
    note = assemble_note(
        draft,
        _classified(),
        kind="text",
        text="原文",
        link_targets=["Prompt Engineering", "RAG", "问 AB"],
        now=NOW,
    )
    related = note.body.split("## 关联\n", 1)[1].split("\n\n", 1)[0]
    # 大小写不敏感对齐到真实文件名、去重、不链接自己、文件名按安全化规则匹配
    assert related == "- [[Prompt Engineering]]\n- [[问 AB]]"


def test_no_linkable_related_writes_placeholder() -> None:
    note = assemble_note(_draft(), _classified(), kind="text", text="原文", now=NOW)
    assert "## 关联\n（暂无）\n\n## 原始输入" in note.body
    assert "[[" not in note.body


def test_unclassified_goes_to_inbox_with_domain() -> None:
    fm = assemble_note(_draft(), _unclassified(), kind="text", text="原文", now=NOW).frontmatter
    assert (fm.status, fm.category, fm.suggested_domain, fm.confidence) == (
        "inbox",
        None,
        "AI工程",
        0.0,
    )


def test_low_confidence_goes_to_inbox_but_keeps_category() -> None:
    """不变量 10：低置信落 inbox 时 category 保留、suggested_domain 为空。"""
    fm = assemble_note(
        _draft(), _classified(confidence=0.59), kind="text", text="原文", now=NOW
    ).frontmatter
    assert (fm.status, fm.category, fm.suggested_domain, fm.confidence) == (
        "inbox",
        "ai-engineering/rag",
        None,
        0.59,
    )


def test_confidence_at_threshold_is_active() -> None:
    assert config.INBOX_CONFIDENCE_THRESHOLD == 0.6
    fm = assemble_note(
        _draft(), _classified(confidence=0.6), kind="text", text="原文", now=NOW
    ).frontmatter
    assert fm.status == "active"


def test_default_timestamp_has_timezone() -> None:
    fm = assemble_note(_draft(), _classified(), kind="text", text="原文").frontmatter
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
        assemble_note(_draft(), _classified(), now=NOW, **kwargs)


def test_assembled_note_round_trips_through_store(store: VaultStore) -> None:
    note = assemble_note(
        _draft(), _classified(confidence=0.3), kind="text", text="原文", now=NOW
    )
    rel = store.create_note(note)

    assert rel.as_posix() == "inbox/RAG.md"
    read = store.read_note(rel)
    assert read.frontmatter == note.frontmatter
    assert read.frontmatter.id is not None
    assert read.body.strip() == note.body.strip()
