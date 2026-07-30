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


def test_generated_must_not_be_verified() -> None:
    with pytest.raises(ValidationError, match="verified 必须为 false"):
        make_note(kind="generated", verified=True)
    ok = make_note(kind="generated", verified=False)
    assert ok.frontmatter.verified is False
    # 其余来源允许 verified=True
    assert make_note(kind="screenshot", verified=True).frontmatter.verified is True
