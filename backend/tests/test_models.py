"""models.py：SCHEMA v2 的交叉校验规则。"""

import pytest
from pydantic import ValidationError

from tests.conftest import make_note


def test_null_category_requires_suggested_domain() -> None:
    with pytest.raises(ValidationError, match="suggested_domain 必填"):
        make_note(category=None, status="inbox", suggested_domain=None)


def test_category_forbids_suggested_domain() -> None:
    with pytest.raises(ValidationError, match="必须为 null"):
        make_note(category="ai-engineering/rag", suggested_domain="检索技术")


def test_suggested_domain_length_2_to_8() -> None:
    with pytest.raises(ValidationError, match="2–8 字"):
        make_note(category=None, status="inbox", suggested_domain="短")
    with pytest.raises(ValidationError, match="2–8 字"):
        make_note(category=None, status="inbox", suggested_domain="九个字符长的领域猜测")
    ok = make_note(category=None, status="inbox", suggested_domain="量子力学")
    assert ok.frontmatter.suggested_domain == "量子力学"


def test_verified_independent_of_source_kind() -> None:
    """verified 与 source.kind 互相独立（SCHEMA 2.2）。模型层任意组合都合法：
    「创建时一律 false」是 store.create_note 写入层的职责，不在模型层。"""
    for kind in ("screenshot", "text", "url", "file", "generated"):
        for verified in (True, False):
            note = make_note(kind=kind, verified=verified)
            assert note.frontmatter.verified is verified
