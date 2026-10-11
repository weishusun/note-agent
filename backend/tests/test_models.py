"""models.py：frontmatter 的跨字段规则、分类名的规整。"""

import pytest
from pydantic import ValidationError

from app.vault.models import Taxonomy, clean_category_name, split_category
from tests.conftest import make_note


def test_null_category_requires_inbox() -> None:
    with pytest.raises(ValidationError, match="status 必须是 inbox"):
        make_note(category=None, status="active")
    assert make_note(category=None, status="inbox", confidence=0.0).frontmatter.category is None


def test_inbox_may_keep_a_guessed_category() -> None:
    """模型拿不准时进待整理，但保留它猜的分类（SCHEMA 不变量 10）。"""
    note = make_note(category="AI工程/RAG", status="inbox", confidence=0.4)
    assert (note.frontmatter.status, note.frontmatter.category) == ("inbox", "AI工程/RAG")


@pytest.mark.parametrize("category", ["只有大块", "大块/小块/第三层", "/小块", "大块/", " / "])
def test_category_must_be_two_levels(category: str) -> None:
    with pytest.raises(ValidationError, match="两层"):
        make_note(category=category)


def test_frontmatter_tolerates_long_names_on_read() -> None:
    """读取从宽：手工改出的长名字不能让整条笔记读不了；长度只在写入路径上卡。"""
    assert make_note(category="一个超过十二个字的很长的大块名字/小块").frontmatter.category


def test_clean_category_name() -> None:
    assert clean_category_name("  模型训练 ") == "模型训练"
    assert clean_category_name("十二个字的分类名正好十二") == "十二个字的分类名正好十二"
    for bad in ["", "   ", "含/斜杠", "十三个字的分类名超出上限了啊"]:
        with pytest.raises(ValueError):
            clean_category_name(bad)


def test_split_category() -> None:
    assert split_category(" 人工智能 / 模型训练 ") == ("人工智能", "模型训练")
    for bad in ["人工智能", "a/b/c", "人工智能/", ""]:
        with pytest.raises(ValueError):
            split_category(bad)


def test_taxonomy_accepts_v1_files_and_bare_nodes() -> None:
    """v1 的 label 键被忽略；「小块名:」后面什么都不写（yaml 解析成 None）也能读。"""
    taxonomy = Taxonomy.model_validate(
        {
            "version": 1,
            "updated": "2026-07-30",
            "categories": {
                "AI工程": {"label": "AI 工程", "children": {"RAG": {"label": "检索"}, "Agent": None}},
                "空大块": None,
            },
            "tags": ["向量检索"],
        }
    )
    assert taxonomy.leaves() == ["AI工程/RAG", "AI工程/Agent"]
    assert taxonomy.categories["空大块"].children == {}
    assert Taxonomy.model_validate({}).categories == {}
