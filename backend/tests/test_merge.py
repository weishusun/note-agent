"""app/organize/merge.py：把一条笔记的正文并进另一条。纯函数，不碰 vault。"""

import pytest

from app.ingest.assemble import SUPPLEMENT_HEADING, SUPPLEMENT_MARKER
from app.organize.merge import (
    dedupe_link_lines,
    drop_link_lines,
    merge_bodies,
    parse_sections,
    render_sections,
)

KEPT = f"""## 要点
- 用标注数据继续训练
- 需要高质量样本

{SUPPLEMENT_HEADING}
{SUPPLEMENT_MARKER}

- SFT 即 Supervised Fine-Tuning

## 关联
- [[预训练]]
- [[SFT (2)]]

## 原始输入
![[assets/a.png]]
"""

MERGED = f"""## 要点
- 需要高质量样本
- 通常接在预训练之后

{SUPPLEMENT_HEADING}
{SUPPLEMENT_MARKER}

- 常与 RLHF 配合使用

## 关联
- [[大模型训练流程]]
- [[预训练]]
- [[SFT]]
"""


def _merge(kept: str = KEPT, merged: str = MERGED, **kw) -> tuple[str, str]:
    kw.setdefault("kept_summary", "概要甲")
    kw.setdefault("merged_summary", "概要乙")
    kw.setdefault("drop_links_to", ("SFT", "SFT (2)"))
    return merge_bodies(kept, merged, **kw)


def _section(body: str, heading: str) -> list[str]:
    return dict(parse_sections(body))[heading]


def test_parse_and_render_round_trip() -> None:
    sections = parse_sections("开头一段\n\n## 要点\n- 甲\n\n\n## 关联\n（暂无）\n")
    assert sections == [(None, ["开头一段"]), ("## 要点", ["- 甲"]), ("## 关联", ["（暂无）"])]
    assert render_sections(sections) == "开头一段\n\n## 要点\n- 甲\n\n## 关联\n（暂无）\n"


def test_sections_are_merged_one_by_one_without_a_model() -> None:
    body, summary = _merge()

    assert summary == "概要甲"
    # 区块顺序跟留下的那条走，原始输入始终在最后
    assert [h for h, _ in parse_sections(body)] == ["## 要点", SUPPLEMENT_HEADING, "## 关联", "## 原始输入"]
    assert _section(body, "## 要点") == ["- 用标注数据继续训练", "- 需要高质量样本", "- 通常接在预训练之后"]
    # 说明行只留一份，仍在最前面（不变量 14）
    assert _section(body, SUPPLEMENT_HEADING) == [
        SUPPLEMENT_MARKER,
        "",
        "- SFT 即 Supervised Fine-Tuning",
        "- 常与 RLHF 配合使用",
    ]
    # 关联取并集，不留指向自己和指向被并掉那条的链接
    assert _section(body, "## 关联") == ["- [[预训练]]", "- [[大模型训练流程]]"]
    assert _section(body, "## 原始输入") == ["![[assets/a.png]]"]


def test_sections_only_the_merged_note_has_are_appended_before_the_source() -> None:
    kept = "## 要点\n- 甲\n\n## 原始输入\n> 原文\n"
    merged = f"## 要点\n- 乙\n\n{SUPPLEMENT_HEADING}\n{SUPPLEMENT_MARKER}\n\n- 补充\n\n## 关联\n- [[丙]]\n"
    body, _ = _merge(kept, merged)
    assert [h for h, _ in parse_sections(body)] == ["## 要点", SUPPLEMENT_HEADING, "## 关联", "## 原始输入"]
    assert _section(body, SUPPLEMENT_HEADING) == [SUPPLEMENT_MARKER, "", "- 补充"]


def test_link_section_left_empty_gets_the_placeholder() -> None:
    body, _ = _merge("## 要点\n- 甲\n\n## 关联\n- [[SFT (2)]]\n", "## 要点\n- 乙\n\n## 关联\n（暂无）\n")
    assert _section(body, "## 关联") == ["（暂无）"]


def test_tidy_rewrites_points_supplement_and_summary() -> None:
    seen: list[tuple] = []

    def tidy(summary_a, summary_b, points, supplement):
        seen.append((summary_a, summary_b, points, supplement))
        return "合并后的概要", ["整理后的要点"], ["整理后的补充"]

    body, summary = _merge(tidy=tidy)

    # 交给模型的是逐字去重后的并集；模型补充里不含那行固定说明
    assert seen == [
        (
            "概要甲",
            "概要乙",
            ["用标注数据继续训练", "需要高质量样本", "通常接在预训练之后"],
            ["SFT 即 Supervised Fine-Tuning", "常与 RLHF 配合使用"],
        )
    ]
    assert summary == "合并后的概要"
    assert _section(body, "## 要点") == ["- 整理后的要点"]
    assert _section(body, SUPPLEMENT_HEADING) == [SUPPLEMENT_MARKER, "", "- 整理后的补充"]
    # 模型碰不到链接和原始输入
    assert _section(body, "## 关联") == ["- [[预训练]]", "- [[大模型训练流程]]"]
    assert _section(body, "## 原始输入") == ["![[assets/a.png]]"]


def test_tidy_failure_keeps_the_plain_union() -> None:
    def boom(*args):
        raise RuntimeError("模型挂了")

    assert _merge(tidy=boom) == _merge()


@pytest.mark.parametrize(
    ("kept", "merged"),
    [
        # 只有一边有要点：没什么可去重的
        ("## 要点\n- 甲\n", "## 关联\n- [[乙]]\n"),
        # 使用者把要点改成了自由文本：不让模型重写
        ("## 要点\n一段自己写的话\n- 甲\n", "## 要点\n- 乙\n"),
        # 手写的笔记没有「要点」区块
        ("自己写的正文，提到了甲\n", "## 要点\n- 乙\n"),
    ],
)
def test_tidy_is_skipped_when_the_points_are_not_two_plain_lists(kept: str, merged: str) -> None:
    def never(*args):
        raise AssertionError("不该调用模型")

    body, summary = _merge(kept, merged, tidy=never)
    assert summary == "概要甲"
    assert "甲" in body


def test_supplement_with_free_text_is_left_alone_by_tidy() -> None:
    kept = f"## 要点\n- 甲\n\n{SUPPLEMENT_HEADING}\n{SUPPLEMENT_MARKER}\n\n一段自己改过的补充\n"
    body, _ = _merge(kept, "## 要点\n- 乙\n", tidy=lambda a, b, p, s: ("概要", ["甲乙"], ["模型想塞进来的"]))
    assert _section(body, "## 要点") == ["- 甲乙"]
    assert _section(body, SUPPLEMENT_HEADING) == [SUPPLEMENT_MARKER, "", "一段自己改过的补充"]


def test_dedupe_link_lines() -> None:
    body = "## 概念\n- [[SFT]]\n- [[预训练]]\n- [[SFT]]\n\n## 内容\n- 重复的普通行\n- 重复的普通行\n"
    assert dedupe_link_lines(body) == (
        "## 概念\n- [[SFT]]\n- [[预训练]]\n\n## 内容\n- 重复的普通行\n- 重复的普通行\n"
    )
    untouched = "## 概念\n- [[SFT]]\n\n\n## 关联\n- [[SFT]]\n"
    assert dedupe_link_lines(untouched) is untouched


def test_drop_link_lines() -> None:
    body = "## 要点\n- 见 [[SFT]] 一节\n\n## 关联\n- [[SFT]]\n- [[预训练]]\n"
    assert drop_link_lines(body, ("SFT",)) == "## 要点\n- 见 [[SFT]] 一节\n\n## 关联\n- [[预训练]]\n"
    assert drop_link_lines("## 关联\n- [[SFT]]\n", ("SFT",)) == "## 关联\n（暂无）\n"
    untouched = "## 关联\n- [[预训练]]\n"
    assert drop_link_lines(untouched, ("SFT",)) is untouched
