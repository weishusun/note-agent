"""taxonomy.py：分类树的读写——登记、改名、合并、删除、清理、锁定。"""

import os
from pathlib import Path

import pytest
import yaml

from app.vault.taxonomy import CategoryExists, CategoryNotFound, TaxonomyManager


def _on_disk(vault_root: Path) -> dict:
    return yaml.safe_load((vault_root / "taxonomy.yaml").read_text(encoding="utf-8"))["categories"]


# ---------- 读 ----------


def test_leaf_categories(taxonomy: TaxonomyManager) -> None:
    assert taxonomy.leaf_categories() == [
        "AI工程/Agent",
        "AI工程/RAG",
        "AI工程/提示工程",
        "计算机基础/算法",
        "计算机基础/数据结构",
    ]
    assert taxonomy.is_valid_category("AI工程/RAG")
    for invalid in ["AI工程", "AI工程/不存在", None, ""]:
        assert not taxonomy.is_valid_category(invalid)


def test_missing_or_empty_file_is_an_empty_tree(tmp_path: Path) -> None:
    """新知识库的正常起点，不是错误。"""
    manager = TaxonomyManager(tmp_path / "taxonomy.yaml")
    assert manager.leaf_categories() == []
    (tmp_path / "taxonomy.yaml").write_text("", encoding="utf-8")
    assert manager.leaf_categories() == []
    assert manager.match_category("RAG") is None


def test_canonical_reuses_existing_spelling(taxonomy: TaxonomyManager) -> None:
    assert taxonomy.canonical("ai工程", "rag") == "AI工程/RAG"
    assert taxonomy.canonical(" AI工程 ", "向量检索") == "AI工程/向量检索"
    assert taxonomy.canonical("物理", "量子力学") == "物理/量子力学"
    with pytest.raises(ValueError):
        taxonomy.canonical("AI工程", "含/斜杠")


def test_match_category(taxonomy: TaxonomyManager) -> None:
    assert taxonomy.match_category("ai工程/rag") == "AI工程/RAG"
    assert taxonomy.match_category(" rag ") == "AI工程/RAG"
    assert taxonomy.match_category("计算机基础") == "计算机基础"
    # 精确匹配，不做子串
    assert taxonomy.match_category("检索") is None
    assert taxonomy.match_category("") is None


def test_filter_tags_drops_unknown(taxonomy: TaxonomyManager) -> None:
    kept, dropped = taxonomy.filter_tags(["向量检索", "自创标签", "Python", "另一个野标签"])
    assert kept == ["向量检索", "Python"]
    assert dropped == ["自创标签", "另一个野标签"]


def test_cache_invalidated_on_external_edit(vault_root: Path, taxonomy: TaxonomyManager) -> None:
    assert not taxonomy.is_valid_category("AI工程/新小块")

    path = vault_root / "taxonomy.yaml"
    text = path.read_text(encoding="utf-8").replace(
        "      RAG: { locked: false }",
        "      RAG: { locked: false }\n      新小块: { locked: false }",
    )
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))

    assert taxonomy.is_valid_category("AI工程/新小块")


# ---------- 登记 ----------


def test_ensure_creates_missing_levels_unlocked(vault_root: Path, taxonomy: TaxonomyManager) -> None:
    assert taxonomy.ensure("ai工程/向量检索") == "AI工程/向量检索"  # 沿用已有大块的写法
    assert taxonomy.ensure("物理/量子力学") == "物理/量子力学"

    disk = _on_disk(vault_root)
    assert disk["AI工程"]["children"]["向量检索"] == {"locked": False}
    assert disk["物理"] == {"locked": False, "children": {"量子力学": {"locked": False}}}
    # 写完立刻读得到，不依赖 mtime 变化
    assert taxonomy.is_valid_category("物理/量子力学")
    assert list(vault_root.glob("*.tmp")) == []


def test_ensure_locked_only_marks_what_it_creates(vault_root: Path, taxonomy: TaxonomyManager) -> None:
    taxonomy.ensure("AI工程/微调", locked=True)
    disk = _on_disk(vault_root)
    assert disk["AI工程"]["children"]["微调"] == {"locked": True}
    assert disk["AI工程"]["locked"] is False  # 已有的大块不因此被锁

    taxonomy.ensure("AI工程/RAG", locked=True)  # 已存在：不动它的 locked
    assert _on_disk(vault_root)["AI工程"]["children"]["RAG"] == {"locked": False}


def test_ensure_rejects_bad_paths(taxonomy: TaxonomyManager) -> None:
    for bad in ["只有大块", "a/b/c", "AI工程/", "AI工程/十三个字的分类名超出上限了啊"]:
        with pytest.raises(ValueError):
            taxonomy.ensure(bad)


def test_create_is_locked_and_refuses_duplicates(vault_root: Path, taxonomy: TaxonomyManager) -> None:
    assert taxonomy.create("英语") == "英语"
    assert taxonomy.create("英语/语法") == "英语/语法"
    assert taxonomy.create("数学/线性代数") == "数学/线性代数"  # 大块不存在时一并创建

    disk = _on_disk(vault_root)
    assert disk["英语"] == {"locked": True, "children": {"语法": {"locked": True}}}
    assert disk["数学"]["locked"] is True

    for duplicate in ["英语", "ai工程", "AI工程/rag"]:
        with pytest.raises(CategoryExists):
            taxonomy.create(duplicate)


# ---------- 改名与合并 ----------


def test_rename_child(vault_root: Path, taxonomy: TaxonomyManager) -> None:
    planned, moves = taxonomy.plan_rename("AI工程/RAG", "检索增强")
    assert moves == {"AI工程/RAG": "AI工程/检索增强"}
    assert "RAG" in _on_disk(vault_root)["AI工程"]["children"]  # plan 不落盘

    taxonomy.commit(planned)
    children = _on_disk(vault_root)["AI工程"]["children"]
    assert list(children) == ["Agent", "检索增强", "提示工程"]  # 留在原来的位置
    assert children["检索增强"] == {"locked": True}


def test_rename_child_to_existing_sibling_merges(vault_root: Path, taxonomy: TaxonomyManager) -> None:
    planned, moves = taxonomy.plan_rename("AI工程/提示工程", "agent")
    assert moves == {"AI工程/提示工程": "AI工程/Agent"}

    taxonomy.commit(planned)
    children = _on_disk(vault_root)["AI工程"]["children"]
    assert list(children) == ["Agent", "RAG"]
    assert children["Agent"] == {"locked": True}


def test_rename_parent_moves_every_child(vault_root: Path, taxonomy: TaxonomyManager) -> None:
    planned, moves = taxonomy.plan_rename("AI工程", "人工智能")
    assert moves == {
        "AI工程/Agent": "人工智能/Agent",
        "AI工程/RAG": "人工智能/RAG",
        "AI工程/提示工程": "人工智能/提示工程",
    }
    taxonomy.commit(planned)
    disk = _on_disk(vault_root)
    assert list(disk) == ["人工智能", "计算机基础"]
    assert disk["人工智能"]["locked"] is True


def test_rename_parent_to_existing_parent_merges_children(
    vault_root: Path, taxonomy: TaxonomyManager
) -> None:
    taxonomy.ensure("计算机基础/RAG")  # 两边都有 RAG：合并后只剩一个
    planned, moves = taxonomy.plan_rename("AI工程", "计算机基础")
    assert moves == {
        "AI工程/Agent": "计算机基础/Agent",
        "AI工程/RAG": "计算机基础/RAG",
        "AI工程/提示工程": "计算机基础/提示工程",
    }
    taxonomy.commit(planned)
    disk = _on_disk(vault_root)
    assert list(disk) == ["计算机基础"]
    assert sorted(disk["计算机基础"]["children"]) == sorted(
        ["算法", "数据结构", "RAG", "Agent", "提示工程"]
    )


def test_rename_only_changing_case_is_not_a_merge(taxonomy: TaxonomyManager) -> None:
    planned, moves = taxonomy.plan_rename("AI工程/RAG", "Rag")
    assert moves == {"AI工程/RAG": "AI工程/Rag"}
    assert "Rag" in planned.categories["AI工程"].children


def test_rename_errors(taxonomy: TaxonomyManager) -> None:
    with pytest.raises(CategoryNotFound):
        taxonomy.plan_rename("AI工程/不存在", "新名字")
    with pytest.raises(CategoryNotFound):
        taxonomy.plan_rename("不存在的大块", "新名字")
    with pytest.raises(ValueError):
        taxonomy.plan_rename("AI工程/RAG", "含/斜杠")


# ---------- 删除与清理 ----------


def test_delete(vault_root: Path, taxonomy: TaxonomyManager) -> None:
    assert taxonomy.leaves_under("AI工程") == ["AI工程/Agent", "AI工程/RAG", "AI工程/提示工程"]
    assert taxonomy.leaves_under("ai工程/rag") == ["AI工程/RAG"]

    assert taxonomy.delete("AI工程/RAG") == ["AI工程/RAG"]
    assert taxonomy.delete("计算机基础") == ["计算机基础/算法", "计算机基础/数据结构"]
    assert list(_on_disk(vault_root)) == ["AI工程"]

    with pytest.raises(CategoryNotFound):
        taxonomy.delete("AI工程/RAG")
    with pytest.raises(CategoryNotFound):
        taxonomy.leaves_under("计算机基础")


def test_prune_removes_only_unlocked_empty_categories(
    vault_root: Path, taxonomy: TaxonomyManager
) -> None:
    taxonomy.create("英语/语法")  # 使用者建的：锁定，空着也保留
    removed = taxonomy.prune({"AI工程/RAG"})

    assert set(removed) == {
        "AI工程/Agent",
        "AI工程/提示工程",
        "计算机基础/算法",
        "计算机基础/数据结构",
        "计算机基础",  # 小块删光了，未锁定的大块一并删除
    }
    assert _on_disk(vault_root) == {
        "AI工程": {"locked": False, "children": {"RAG": {"locked": False}}},
        "英语": {"locked": True, "children": {"语法": {"locked": True}}},
    }
    assert taxonomy.prune({"AI工程/RAG"}) == []
