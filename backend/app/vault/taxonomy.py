"""taxonomy.yaml 的加载与校验。带 mtime 失效的进程内缓存。

分类树最多两层，category 必须指向叶子（parent/child）；
parent 单独出现视为非法。校验不抛异常，返回布尔值，由调用方决定落 inbox。
"""

from pathlib import Path

import yaml

from app.vault.models import Taxonomy


class TaxonomyManager:
    def __init__(self, taxonomy_path: Path) -> None:
        self._path = Path(taxonomy_path)
        self._cached: Taxonomy | None = None
        self._cached_mtime_ns: int | None = None

    def load(self) -> Taxonomy:
        mtime_ns = self._path.stat().st_mtime_ns
        if self._cached is None or mtime_ns != self._cached_mtime_ns:
            with open(self._path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            self._cached = Taxonomy.model_validate(data)
            self._cached_mtime_ns = mtime_ns
        return self._cached

    def leaf_categories(self) -> list[str]:
        """全部叶子路径，形如 "ai-engineering/rag"。给 LLM 当枚举约束用。"""
        taxonomy = self.load()
        return [
            f"{parent}/{child}"
            for parent, node in taxonomy.categories.items()
            for child in node.children
        ]

    def is_valid_category(self, category: str | None) -> bool:
        """只认叶子路径；parent 单独出现、None、未知路径都返回 False。"""
        if not category:
            return False
        return category in self.leaf_categories()

    def filter_tags(self, tags: list[str]) -> tuple[list[str], list[str]]:
        """按受控词表过滤：返回 (保留的, 丢弃的)，保留原顺序并去重。"""
        allowed = set(self.load().tags)
        kept: list[str] = []
        dropped: list[str] = []
        for tag in tags:
            if tag in allowed:
                if tag not in kept:
                    kept.append(tag)
            else:
                dropped.append(tag)
        return kept, dropped
