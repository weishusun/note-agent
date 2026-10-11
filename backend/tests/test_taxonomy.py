"""taxonomy.py：叶子校验、标签过滤、mtime 缓存失效。"""

from pathlib import Path

from app.vault.taxonomy import TaxonomyManager


def test_leaf_categories(taxonomy: TaxonomyManager) -> None:
    leaves = taxonomy.leaf_categories()
    assert "ai-engineering/rag" in leaves
    assert "cs-fundamentals/algorithm" in leaves
    assert "ai-engineering" not in leaves


def test_is_valid_category_only_accepts_leaves(taxonomy: TaxonomyManager) -> None:
    assert taxonomy.is_valid_category("ai-engineering/rag")
    assert not taxonomy.is_valid_category("ai-engineering")
    assert not taxonomy.is_valid_category("ai-engineering/nope")
    assert not taxonomy.is_valid_category(None)
    assert not taxonomy.is_valid_category("")


def test_filter_tags_drops_unknown(taxonomy: TaxonomyManager) -> None:
    kept, dropped = taxonomy.filter_tags(["向量检索", "自创标签", "Python", "另一个野标签"])
    assert kept == ["向量检索", "Python"]
    assert dropped == ["自创标签", "另一个野标签"]


def test_match_category(taxonomy: TaxonomyManager) -> None:
    # 叶子路径 / 叶子名 / 叶子 label，大小写不敏感
    assert taxonomy.match_category("AI-Engineering/RAG") == "ai-engineering/rag"
    assert taxonomy.match_category(" rag ") == "ai-engineering/rag"
    assert taxonomy.match_category("检索与上下文") == "ai-engineering/rag"
    # 一级分类名 / label
    assert taxonomy.match_category("cs-fundamentals") == "cs-fundamentals"
    assert taxonomy.match_category("AI 工程") == "ai-engineering"
    # 精确匹配，不做子串
    assert taxonomy.match_category("检索") is None
    assert taxonomy.match_category("") is None


def test_cache_invalidated_on_mtime_change(vault_root: Path, taxonomy: TaxonomyManager) -> None:
    assert not taxonomy.is_valid_category("ai-engineering/new-leaf")

    path = vault_root / "taxonomy.yaml"
    text = path.read_text(encoding="utf-8").replace(
        "rag:         { label: 检索与上下文 }",
        "rag:         { label: 检索与上下文 }\n      new-leaf:    { label: 新叶子 }",
    )
    path.write_text(text, encoding="utf-8")
    import os

    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))

    assert taxonomy.is_valid_category("ai-engineering/new-leaf")
