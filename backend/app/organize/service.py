"""把分类树、笔记文件、索引三处一起改对。

taxonomy.py 只管 taxonomy.yaml，store.py 只管笔记文件，index.py 只管索引；
凡是「改一个分类要同时动这三样」的操作都收在这里，并在结尾做一次 vault 的 git 提交。
"""

import logging
import subprocess
import threading
from collections.abc import Callable, Sequence

from app import config
from app.extract import classifier
from app.extract.classifier import NoteBrief
from app.search.index import SearchIndex
from app.search.query import (
    all_refs,
    categories_in_use,
    category_counts,
    find_by_id,
    refs_with_category,
    status_counts,
)
from app.vault import git
from app.vault.models import (
    CATEGORY_SEP,
    NoteRef,
    Tree,
    TreeChild,
    TreeParent,
    join_category,
    split_category,
)
from app.vault.store import VaultStore
from app.vault.taxonomy import TaxonomyManager

logger = logging.getLogger(__name__)

# 重新归类时每次模型调用处理多少条（SCHEMA 3.4）
RECLASSIFY_BATCH = 20


class NoteNotFound(LookupError):
    pass


class CategoryNotEmpty(ValueError):
    pass


class ReclassifyRunning(RuntimeError):
    pass


def placement_status(category: str | None, confidence: float) -> tuple[str, float]:
    """模型给出的归类最终落成什么状态（SCHEMA 3.2、不变量 10）。返回 (status, confidence)。"""
    if category is None:
        return "inbox", 0.0
    if confidence < config.INBOX_CONFIDENCE_THRESHOLD:
        return "inbox", confidence
    return "active", confidence


class Organizer:
    def __init__(self, store: VaultStore, taxonomy: TaxonomyManager, index: SearchIndex) -> None:
        self.store = store
        self.taxonomy = taxonomy
        self.index = index
        self._reclassify_lock = threading.Lock()

    # ---------- 目录树 ----------

    def tree(self) -> Tree:
        total, inbox, duplicates = status_counts(self.index)
        return self._tree(category_counts(self.index), total, inbox, duplicates)

    def _tree(
        self, counts: dict[str, int], total: int, inbox: int, duplicates: int = 0
    ) -> Tree:
        """taxonomy.yaml 里登记的分类，加上笔记上写着但没登记的——两边不一致时
        不能让笔记在界面上消失（SCHEMA /api/tree）。"""
        nodes: dict[str, tuple[bool, dict[str, bool]]] = {
            parent: (node.locked, {child: leaf.locked for child, leaf in node.children.items()})
            for parent, node in self.taxonomy.load().categories.items()
        }
        for path in counts:
            parent, _, child = path.partition(CATEGORY_SEP)
            locked, children = nodes.setdefault(parent, (False, {}))
            children.setdefault(child, False)

        parents = []
        for parent, (locked, children) in nodes.items():
            kids = [
                TreeChild(
                    name=child,
                    path=join_category(parent, child),
                    locked=child_locked,
                    count=counts.get(join_category(parent, child), 0),
                )
                for child, child_locked in children.items()
            ]
            kids.sort(key=lambda c: (-c.count, c.name))
            parents.append(
                TreeParent(
                    name=parent, locked=locked, count=sum(c.count for c in kids), children=kids
                )
            )
        parents.sort(key=lambda p: (-p.count, p.name))
        return Tree(total=total, inbox=inbox, duplicates=duplicates, categories=parents)

    # ---------- 自动归类 ----------

    def classify(
        self, briefs: Sequence[NoteBrief], tree: Tree | None = None
    ) -> list[tuple[str | None, float]]:
        """给一批笔记定分类，返回与 briefs 等长的 (「大块/小块」或 None, 置信度)。

        新出现的分类立即登记进 taxonomy.yaml（不锁定）。**从不抛异常**：分类失败不得让录入失败，
        分不上的那几条返回 (None, 0.0)，由调用方放进待整理。
        """
        try:
            placements = classifier.classify_notes(list(briefs), tree or self.tree())
        except Exception as exc:
            logger.warning("自动归类失败，这一批进待整理: %s", exc)
            return [(None, 0.0)] * len(briefs)

        results: list[tuple[str | None, float]] = []
        for placement in placements:
            if placement.category is None:
                results.append((None, 0.0))
                continue
            try:
                results.append((self.taxonomy.ensure(placement.category), placement.confidence))
            except (ValueError, OSError) as exc:
                logger.warning("登记分类 %r 失败，这条进待整理: %s", placement.category, exc)
                results.append((None, 0.0))
        return results

    # ---------- 手动调整（都会锁定，SCHEMA 3.3） ----------

    def move_note(self, note_id: str, category: str) -> NoteRef:
        """把一条笔记移到指定分类，或确认一条待整理的笔记。分类不存在时创建并锁定。"""
        ref = find_by_id(self.index, note_id)
        if ref is None:
            raise NoteNotFound(note_id)
        split_category(category)  # 名字不合规在动任何东西之前就报错
        path = self.taxonomy.ensure(category, locked=True)
        try:
            note = self.store.update_frontmatter(
                ref.path, category=path, category_locked=True, status="active", confidence=1.0
            )
        except FileNotFoundError:
            raise NoteNotFound(note_id) from None
        self.index.upsert_note(note)
        self._prune()
        self._commit(f"taxonomy: 「{note.frontmatter.title}」归入「{path}」")
        return find_by_id(self.index, note_id) or ref

    def create_category(self, path: str) -> str:
        created = self.taxonomy.create(path)
        self._commit(f"taxonomy: 新建「{created}」")
        return created

    def rename_category(self, path: str, name: str) -> int:
        """改名，或并入同层已有的分类。返回改写了多少条笔记。

        顺序是先改笔记、再落盘分类树：中途失败时旧分类还登记着，重做同一个操作能把剩下的补齐。
        """
        planned, moves = self.taxonomy.plan_rename(path, name)
        rewritten = 0
        for old, new in moves.items():
            for ref in refs_with_category(self.index, old):
                try:
                    # 索引可能落后于磁盘：以文件里实际写的分类为准
                    if self.store.read_note(ref.path).frontmatter.category != old:
                        continue
                    note = self.store.update_frontmatter(ref.path, category=new)
                except (FileNotFoundError, ValueError) as exc:
                    logger.warning("改名时跳过 %s: %s", ref.path, exc)
                    continue
                self.index.upsert_note(note)
                rewritten += 1
        self.taxonomy.commit(planned)
        self._commit(f"taxonomy: 「{path}」改为「{name}」(改写 {rewritten} 条)")
        return rewritten

    def delete_category(self, path: str) -> None:
        """只允许删空分类；删大块要求它下面所有小块都没有笔记。"""
        for leaf in self.taxonomy.leaves_under(path):
            if refs_with_category(self.index, leaf):
                raise CategoryNotEmpty(leaf)
        self.taxonomy.delete(path)
        self._commit(f"taxonomy: 删除「{path}」")

    # ---------- 重新归类（SCHEMA 3.4） ----------

    def start_reclassify(self) -> None:
        """占住「同一时间只允许一个重新归类任务」这个名额，已有一个在跑时抛 ReclassifyRunning。"""
        if not self._reclassify_lock.acquire(blocking=False):
            raise ReclassifyRunning()

    def reclassify(
        self, scope: str, on_progress: Callable[[int, int], None] = lambda done, total: None
    ) -> int:
        """必须先调 start_reclassify()。返回改写了多少条笔记。"""
        try:
            return self._reclassify(scope, on_progress)
        finally:
            self._reclassify_lock.release()

    def _reclassify(self, scope: str, on_progress: Callable[[int, int], None]) -> int:
        refs = all_refs(self.index)
        targets = [
            r
            for r in refs
            if not r.category_locked
            and (scope == "all" or r.category is None or r.status == "inbox")
        ]
        target_paths = {r.path for r in targets}
        untouched = [r for r in refs if r.path not in target_paths]

        if scope == "all":
            # 只留锁定的分类和含有不参与重排的笔记的分类，其余让模型重新长出来
            self.taxonomy.prune({r.category for r in untouched if r.category})

        # 分类树上的计数边归类边累积，后面的批次能看到前面新建的分类和它们的笔记数
        counts: dict[str, int] = {}
        for r in untouched:
            if r.category and r.status == "active":
                counts[r.category] = counts.get(r.category, 0) + 1
        total, changed = len(targets), 0
        on_progress(0, total)

        for start in range(0, total, RECLASSIFY_BATCH):
            batch = targets[start : start + RECLASSIFY_BATCH]
            briefs = [NoteBrief(title=r.title, summary=r.summary) for r in batch]
            placements = self.classify(briefs, tree=self._tree(counts, len(refs), 0))
            for ref, (category, confidence) in zip(batch, placements):
                if category is None:
                    continue  # 没分上：保持原样，不把已有的分类抹掉
                status, confidence = placement_status(category, confidence)
                try:
                    note = self.store.update_frontmatter(
                        ref.path, category=category, status=status, confidence=confidence
                    )
                except (FileNotFoundError, ValueError) as exc:
                    logger.warning("重新归类时跳过 %s: %s", ref.path, exc)
                    continue
                self.index.upsert_note(note)
                changed += 1
                if status == "active":
                    counts[category] = counts.get(category, 0) + 1
            on_progress(min(start + RECLASSIFY_BATCH, total), total)

        self.reconcile()
        self._commit(f"taxonomy: 重新归类 {changed} 条")
        return changed

    # ---------- 内部 ----------

    def reconcile(self) -> None:
        """让 taxonomy.yaml 与笔记上实际写的分类对齐：补登记缺的，清掉未锁定的空分类。"""
        in_use = categories_in_use(self.index)
        for category in sorted(in_use):
            try:
                self.taxonomy.ensure(category)
            except ValueError as exc:
                logger.warning("笔记上的分类 %r 不合规，未登记: %s", category, exc)
        self.taxonomy.prune(in_use)

    def _prune(self) -> None:
        self.taxonomy.prune(categories_in_use(self.index))

    def _commit(self, message: str) -> None:
        # 提交失败（如 vault 不是 git 仓库）只记日志：该改的已经改完了
        try:
            git.commit(self.store.root, message)
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("vault git 提交失败: %s", exc)
