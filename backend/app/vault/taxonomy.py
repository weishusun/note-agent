"""taxonomy.yaml 的读写（SCHEMA 第 3 节）。整个代码库里唯一允许碰这个文件的模块。

分类固定两层「大块/小块」，名字就是标识。本模块只管分类树本身：登记、改名、合并、删除、清理。
笔记文件里的 category 怎么跟着改，由 app/organize 负责——那需要同时动笔记和索引。

读取带 mtime 失效的进程内缓存；写入用原子替换并立即刷新缓存。
写操作用一把锁串行化：录入任务跑在线程池里，会并发地登记新分类。
"""

import os
import threading
from datetime import date
from pathlib import Path
from uuid import uuid4

import yaml

from app.vault.models import (
    Taxonomy,
    TaxonomyChild,
    TaxonomyParent,
    clean_category_name,
    join_category,
    split_category,
)

_HEADER = (
    "# 分类树：固定两层，大块 → 小块。笔记的 category 写成「大块/小块」。\n"
    "# 本文件由 note-agent 自动维护；请在应用里修改分类，不要手改这个文件。\n"
    "# locked: true 表示使用者手动建过或改过，模型不会再给它改名、合并或删除。\n"
)


class CategoryNotFound(LookupError):
    pass


class CategoryExists(ValueError):
    pass


def _find(names: object, wanted: str) -> str | None:
    """在一层名字里做大小写不敏感的查找，返回库里已有的写法。"""
    key = wanted.casefold()
    for name in names:  # type: ignore[attr-defined]
        if name.casefold() == key:
            return name
    return None


class TaxonomyManager:
    def __init__(self, taxonomy_path: Path) -> None:
        self._path = Path(taxonomy_path)
        self._cached: Taxonomy | None = None
        self._cached_mtime_ns: int | None = None
        self._lock = threading.RLock()

    # ---------- 读 ----------

    def load(self) -> Taxonomy:
        """文件不存在或为空时返回空分类树：那是新知识库的正常起点，不是错误。"""
        try:
            mtime_ns = self._path.stat().st_mtime_ns
        except FileNotFoundError:
            return Taxonomy()
        with self._lock:
            if self._cached is None or mtime_ns != self._cached_mtime_ns:
                with open(self._path, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                self._cached = Taxonomy.model_validate(data or {})
                self._cached_mtime_ns = mtime_ns
            return self._cached

    def leaf_categories(self) -> list[str]:
        """全部「大块/小块」路径。"""
        return self.load().leaves()

    def is_valid_category(self, category: str | None) -> bool:
        """只认登记过的「大块/小块」；只有大块、None、未知路径都返回 False。"""
        if not category:
            return False
        return category in self.leaf_categories()

    def canonical(self, parent: str, child: str) -> str:
        """把一对名字规整成路径：与已有分类大小写不敏感相同的，沿用已有的写法。

        模型写「Rag」而库里是「RAG」时用「RAG」，否则同一个分类会裂成两个。名字不合规抛 ValueError。
        """
        parent, child = clean_category_name(parent), clean_category_name(child)
        categories = self.load().categories
        existing_parent = _find(categories, parent)
        if existing_parent is None:
            return join_category(parent, child)
        existing_child = _find(categories[existing_parent].children, child)
        return join_category(existing_parent, existing_child or child)

    def match_category(self, q: str) -> str | None:
        """查询词是否就是某个分类（SCHEMA 第 5 节 matched_category）。大小写不敏感精确匹配，
        小块优先于大块。"""
        key = q.strip().casefold()
        if not key:
            return None
        categories = self.load().categories
        for parent, node in categories.items():
            for child in node.children:
                path = join_category(parent, child)
                if key in (path.casefold(), child.casefold()):
                    return path
        return _find(categories, key)

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

    # ---------- 写 ----------

    def _mutable(self) -> Taxonomy:
        return self.load().model_copy(deep=True)

    def _save(self, taxonomy: Taxonomy) -> None:
        taxonomy.version = 2
        taxonomy.updated = date.today().isoformat()
        body = yaml.safe_dump(
            taxonomy.model_dump(mode="python"),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(_HEADER + body)
            os.replace(tmp, self._path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        # 不等 mtime 自己变：同一毫秒内写完立刻读，mtime 可能分辨不出来
        self._cached = taxonomy
        self._cached_mtime_ns = self._path.stat().st_mtime_ns

    def ensure(self, path: str, locked: bool = False) -> str:
        """保证「大块/小块」已登记，返回库里的规范写法。

        缺的那一层会被新建，新建的节点带上传入的 locked；已经存在的节点不动它的 locked。
        """
        with self._lock:
            parent, child = split_category(self.canonical(*split_category(path)))
            taxonomy = self._mutable()
            changed = False
            if parent not in taxonomy.categories:
                taxonomy.categories[parent] = TaxonomyParent(locked=locked)
                changed = True
            if child not in taxonomy.categories[parent].children:
                taxonomy.categories[parent].children[child] = TaxonomyChild(locked=locked)
                changed = True
            if changed:
                self._save(taxonomy)
            return join_category(parent, child)

    def create(self, path: str) -> str:
        """使用者新建分类。path 是「大块」或「大块/小块」。已存在抛 CategoryExists。新建的一律锁定。"""
        with self._lock:
            taxonomy = self._mutable()
            if "/" not in path:
                name = clean_category_name(path)
                if _find(taxonomy.categories, name) is not None:
                    raise CategoryExists(f"大块已存在: {name}")
                taxonomy.categories[name] = TaxonomyParent(locked=True)
                self._save(taxonomy)
                return name
            parent, child = split_category(path)
            existing_parent = _find(taxonomy.categories, parent)
            if existing_parent is None:
                taxonomy.categories[parent] = TaxonomyParent(locked=True)
                existing_parent = parent
            node = taxonomy.categories[existing_parent]
            if _find(node.children, child) is not None:
                raise CategoryExists(f"分类已存在: {join_category(existing_parent, child)}")
            node.children[child] = TaxonomyChild(locked=True)
            self._save(taxonomy)
            return join_category(existing_parent, child)

    def plan_rename(self, path: str, name: str) -> tuple[Taxonomy, dict[str, str]]:
        """算出改名（或合并）后的分类树，以及每个受影响的「旧路径 → 新路径」。**不落盘。**

        调用方要先按映射改写笔记，再用 commit() 落盘——顺序反过来的话，中途失败会留下
        指向已不存在的分类的笔记，而且重做时原分类已经找不到了。
        改成同层已有的名字即合并；结果一律锁定（SCHEMA 3.3）。
        """
        name = clean_category_name(name)
        taxonomy = self._mutable()
        moves: dict[str, str] = {}

        if "/" not in path:
            old = _find(taxonomy.categories, path.strip())
            if old is None:
                raise CategoryNotFound(path)
            target = _find(taxonomy.categories, name)
            node = taxonomy.categories[old]
            if target is None or target == old:
                # 纯改名：保持在原来的位置上，不要挪到字典末尾
                taxonomy.categories = {
                    (name if key == old else key): value for key, value in taxonomy.categories.items()
                }
                node.locked = True
                for child in node.children:
                    moves[join_category(old, child)] = join_category(name, child)
            else:
                dest = taxonomy.categories[target]
                dest.locked = True
                for child, child_node in node.children.items():
                    dest_child = _find(dest.children, child)
                    if dest_child is None:
                        dest.children[child] = child_node
                        dest_child = child
                    moves[join_category(old, child)] = join_category(target, dest_child)
                del taxonomy.categories[old]
        else:
            parent, child = split_category(path)
            old_parent = _find(taxonomy.categories, parent)
            old_child = (
                _find(taxonomy.categories[old_parent].children, child) if old_parent else None
            )
            if old_parent is None or old_child is None:
                raise CategoryNotFound(path)
            children = taxonomy.categories[old_parent].children
            target = _find(children, name)
            if target is None or target == old_child:
                taxonomy.categories[old_parent].children = {
                    (name if key == old_child else key): value for key, value in children.items()
                }
                taxonomy.categories[old_parent].children[name].locked = True
                target = name
            else:
                children[target].locked = True
                del children[old_child]
            moves[join_category(old_parent, old_child)] = join_category(old_parent, target)

        moves = {old: new for old, new in moves.items() if old != new}
        return taxonomy, moves

    def commit(self, taxonomy: Taxonomy) -> None:
        """把 plan_rename 算出的分类树落盘。"""
        with self._lock:
            self._save(taxonomy)

    def delete(self, path: str) -> list[str]:
        """删除一个分类，返回它包含的全部「大块/小块」路径。是否为空由调用方先查。"""
        with self._lock:
            taxonomy = self._mutable()
            if "/" not in path:
                old = _find(taxonomy.categories, path.strip())
                if old is None:
                    raise CategoryNotFound(path)
                removed = [join_category(old, child) for child in taxonomy.categories[old].children]
                del taxonomy.categories[old]
            else:
                parent, child = split_category(path)
                old_parent = _find(taxonomy.categories, parent)
                old_child = (
                    _find(taxonomy.categories[old_parent].children, child) if old_parent else None
                )
                if old_parent is None or old_child is None:
                    raise CategoryNotFound(path)
                del taxonomy.categories[old_parent].children[old_child]
                removed = [join_category(old_parent, old_child)]
            self._save(taxonomy)
            return removed

    def leaves_under(self, path: str) -> list[str]:
        """一个分类包含的全部「大块/小块」路径。不存在抛 CategoryNotFound。"""
        categories = self.load().categories
        if "/" not in path:
            parent = _find(categories, path.strip())
            if parent is None:
                raise CategoryNotFound(path)
            return [join_category(parent, child) for child in categories[parent].children]
        parent, child = split_category(path)
        old_parent = _find(categories, parent)
        old_child = _find(categories[old_parent].children, child) if old_parent else None
        if old_parent is None or old_child is None:
            raise CategoryNotFound(path)
        return [join_category(old_parent, old_child)]

    def prune(self, in_use: set[str]) -> list[str]:
        """删掉没有笔记的**未锁定**分类（SCHEMA 3.3）。in_use 是还有笔记在用的「大块/小块」。

        锁定的分类即使空了也保留；未锁定的大块在小块删光后一并删除。返回被删掉的路径。
        """
        with self._lock:
            taxonomy = self._mutable()
            removed: list[str] = []
            for parent in list(taxonomy.categories):
                node = taxonomy.categories[parent]
                for child in list(node.children):
                    path = join_category(parent, child)
                    if not node.children[child].locked and path not in in_use:
                        del node.children[child]
                        removed.append(path)
                if not node.children and not node.locked:
                    del taxonomy.categories[parent]
                    removed.append(parent)
            if removed:
                self._save(taxonomy)
            return removed
