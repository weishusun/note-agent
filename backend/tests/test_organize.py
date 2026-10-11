"""app/organize：目录树、自动归类的落定、手动调整与锁定、重新归类，以及对应的接口。"""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.extract import classifier
from app.extract.classifier import NoteBrief, Placement
from app.main import create_app
from app.organize import service
from app.organize.service import CategoryNotEmpty, NoteNotFound, Organizer, ReclassifyRunning
from app.search.index import SearchIndex
from app.search.query import find_by_id
from app.vault.store import VaultStore
from app.vault.taxonomy import CategoryNotFound, TaxonomyManager
from tests.conftest import make_note


def _add(store: VaultStore, index: SearchIndex, title: str, category: str | None = "AI工程/RAG", **kw):
    if category is None:
        kw.setdefault("status", "inbox")
        kw.setdefault("confidence", 0.0)
    locked = kw.pop("category_locked", False)
    note = make_note(title=title, category=category, aliases=[], **kw)
    rel = store.create_note(note)
    if locked:  # 创建时一律不锁，锁定只能事后产生
        note = store.update_frontmatter(rel, category_locked=True)
    index.upsert_note(note)
    return note


def _taxonomy_on_disk(vault_root: Path) -> dict:
    return yaml.safe_load((vault_root / "taxonomy.yaml").read_text(encoding="utf-8"))["categories"]


def _fm(store: VaultStore, note):
    return store.read_note(note.path).frontmatter


class FakeClassifier:
    """按标题查表给出归类；没列出的标题不作答（返回空 Placement）。"""

    def __init__(self, table: dict[str, tuple[str, str, float]]) -> None:
        self.table = table
        self.calls: list[tuple[list[str], dict[str, int]]] = []
        self.error: Exception | None = None

    def __call__(self, briefs: list[NoteBrief], tree) -> list[Placement]:
        counts = {c.path: c.count for p in tree.categories for c in p.children}
        self.calls.append(([b.title for b in briefs], counts))
        if self.error is not None:
            raise self.error
        placements = []
        for brief in briefs:
            row = self.table.get(brief.title)
            if row is None:
                placements.append(Placement())
            else:
                placements.append(Placement(parent=row[0], child=row[1], confidence=row[2]))
        return placements


@pytest.fixture
def classify(monkeypatch: pytest.MonkeyPatch):
    def _install(table: dict[str, tuple[str, str, float]]) -> FakeClassifier:
        fake = FakeClassifier(table)
        monkeypatch.setattr(classifier, "classify_notes", fake)
        return fake

    return _install


# ---------- 目录树 ----------


def test_tree_counts_and_order(store: VaultStore, index: SearchIndex, organizer: Organizer) -> None:
    _add(store, index, "动态规划", "计算机基础/算法")
    _add(store, index, "快速排序", "计算机基础/算法")
    _add(store, index, "二叉堆", "计算机基础/数据结构")
    _add(store, index, "RAG 入门", "AI工程/RAG")
    _add(store, index, "拿不准", "AI工程/Agent", status="inbox", confidence=0.4)
    _add(store, index, "没分上", None)

    tree = organizer.tree()

    assert (tree.total, tree.inbox) == (6, 2)
    # 大块、小块都按笔记数从多到少；待整理的笔记不计入任何分类
    assert [(p.name, p.count) for p in tree.categories] == [("计算机基础", 3), ("AI工程", 1)]
    assert [(c.name, c.path, c.count) for c in tree.categories[0].children] == [
        ("算法", "计算机基础/算法", 2),
        ("数据结构", "计算机基础/数据结构", 1),
    ]
    assert [(c.name, c.count) for c in tree.categories[1].children] == [
        ("RAG", 1),
        ("Agent", 0),
        ("提示工程", 0),
    ]


def test_tree_shows_categories_missing_from_taxonomy(
    store: VaultStore, index: SearchIndex, organizer: Organizer
) -> None:
    """笔记上写着但 taxonomy.yaml 里没有的分类也要出现，不能让笔记在界面上消失。"""
    _add(store, index, "角动量", "物理/量子力学")
    tree = organizer.tree()
    physics = next(p for p in tree.categories if p.name == "物理")
    assert (physics.locked, physics.count) == (False, 1)
    assert [(c.path, c.locked, c.count) for c in physics.children] == [("物理/量子力学", False, 1)]


# ---------- 自动归类的落定 ----------


def test_classify_reuses_spelling_and_registers_new_categories(
    vault_root: Path, organizer: Organizer, classify
) -> None:
    classify({"甲": ("ai工程", "rag", 0.9), "乙": ("AI工程", "模型微调", 0.4), "丙": ("物理", "量子力学", 0.8)})
    briefs = [NoteBrief(title=t, summary="x") for t in ("甲", "乙", "丙", "丁")]

    assert organizer.classify(briefs) == [
        ("AI工程/RAG", 0.9),
        ("AI工程/模型微调", 0.4),
        ("物理/量子力学", 0.8),
        (None, 0.0),  # 模型没答这一条
    ]
    disk = _taxonomy_on_disk(vault_root)
    assert disk["AI工程"]["children"]["模型微调"] == {"locked": False}
    assert disk["物理"] == {"locked": False, "children": {"量子力学": {"locked": False}}}


def test_classify_never_raises(vault_root: Path, organizer: Organizer, classify) -> None:
    before = _taxonomy_on_disk(vault_root)
    classify({}).error = RuntimeError("429 限流")
    briefs = [NoteBrief(title=t, summary="x") for t in ("甲", "乙")]

    assert organizer.classify(briefs) == [(None, 0.0), (None, 0.0)]
    assert _taxonomy_on_disk(vault_root) == before


# ---------- 手动调整与锁定 ----------


def test_move_note_locks_it_and_makes_it_active(
    vault_root: Path, store: VaultStore, index: SearchIndex, organizer: Organizer, git_messages
) -> None:
    note = _add(store, index, "LoRA", "AI工程/模型微调", status="inbox", confidence=0.4)
    organizer.taxonomy.ensure("AI工程/模型微调")  # 录入时模型新建的小块
    body_before = store.read_note(note.path).body

    ref = organizer.move_note(note.frontmatter.id, "ai工程/rag")

    assert (ref.category, ref.category_locked, ref.status, ref.confidence) == (
        "AI工程/RAG",
        True,
        "active",
        1.0,
    )
    fm = _fm(store, note)
    assert (fm.category, fm.category_locked, fm.status) == ("AI工程/RAG", True, "active")
    assert fm.updated == note.frontmatter.updated  # 改分类不算改内容
    assert store.read_note(note.path).body == body_before
    # 被搬空的未锁定小块自动消失
    assert "模型微调" not in _taxonomy_on_disk(vault_root)["AI工程"]["children"]
    assert git_messages[-1] == "taxonomy: 「LoRA」归入「AI工程/RAG」"


def test_confirming_an_inbox_note_keeps_its_guess(
    store: VaultStore, index: SearchIndex, organizer: Organizer
) -> None:
    note = _add(store, index, "拿不准", "AI工程/Agent", status="inbox", confidence=0.4)
    ref = organizer.move_note(note.frontmatter.id, "AI工程/Agent")
    assert (ref.category, ref.status, ref.category_locked) == ("AI工程/Agent", "active", True)
    assert organizer.tree().inbox == 0


def test_moving_to_a_new_category_creates_it_locked(
    vault_root: Path, store: VaultStore, index: SearchIndex, organizer: Organizer
) -> None:
    note = _add(store, index, "虚拟语气")
    organizer.move_note(note.frontmatter.id, "英语/语法")
    assert _taxonomy_on_disk(vault_root)["英语"] == {
        "locked": True,
        "children": {"语法": {"locked": True}},
    }


def test_move_note_errors_change_nothing(
    vault_root: Path, store: VaultStore, index: SearchIndex, organizer: Organizer
) -> None:
    note = _add(store, index, "LoRA")
    before = (store.root / note.path).read_text(encoding="utf-8"), _taxonomy_on_disk(vault_root)

    with pytest.raises(NoteNotFound):
        organizer.move_note("不存在的id", "AI工程/RAG")
    for bad in ["只有大块", "AI工程/含/斜杠", "AI工程/一个超过十二个字的非常长的小块名字"]:
        with pytest.raises(ValueError):
            organizer.move_note(note.frontmatter.id, bad)

    assert ((store.root / note.path).read_text(encoding="utf-8"), _taxonomy_on_disk(vault_root)) == before


def test_rename_rewrites_every_note_in_the_category(
    vault_root: Path, store: VaultStore, index: SearchIndex, organizer: Organizer, git_messages
) -> None:
    plain = _add(store, index, "RAG 入门")
    pinned = _add(store, index, "向量检索", category_locked=True)
    guess = _add(store, index, "拿不准", "AI工程/RAG", status="inbox", confidence=0.4)
    other = _add(store, index, "动态规划", "计算机基础/算法")

    assert organizer.rename_category("AI工程/RAG", "检索增强") == 3

    assert [_fm(store, n).category for n in (plain, pinned, guess, other)] == [
        "AI工程/检索增强",
        "AI工程/检索增强",
        "AI工程/检索增强",
        "计算机基础/算法",
    ]
    # 改名不改每条笔记自己的锁定状态和状态
    assert [_fm(store, n).category_locked for n in (plain, pinned, guess)] == [False, True, False]
    assert _fm(store, guess).status == "inbox"
    children = _taxonomy_on_disk(vault_root)["AI工程"]["children"]
    assert "RAG" not in children and children["检索增强"] == {"locked": True}
    assert find_by_id(index, plain.frontmatter.id).category == "AI工程/检索增强"
    assert git_messages[-1] == "taxonomy: 「AI工程/RAG」改为「检索增强」(改写 3 条)"


def test_rename_to_existing_name_merges(
    vault_root: Path, store: VaultStore, index: SearchIndex, organizer: Organizer
) -> None:
    a = _add(store, index, "ReAct", "AI工程/Agent")
    b = _add(store, index, "少样本提示", "AI工程/提示工程")

    organizer.rename_category("AI工程/提示工程", "Agent")

    assert [_fm(store, n).category for n in (a, b)] == ["AI工程/Agent", "AI工程/Agent"]
    assert list(_taxonomy_on_disk(vault_root)["AI工程"]["children"]) == ["Agent", "RAG"]
    tree = organizer.tree()
    assert next(c.count for p in tree.categories for c in p.children if c.path == "AI工程/Agent") == 2


def test_rename_parent_rewrites_all_its_children(
    store: VaultStore, index: SearchIndex, organizer: Organizer
) -> None:
    a = _add(store, index, "ReAct", "AI工程/Agent")
    b = _add(store, index, "RAG 入门", "AI工程/RAG")
    assert organizer.rename_category("AI工程", "人工智能") == 2
    assert [_fm(store, n).category for n in (a, b)] == ["人工智能/Agent", "人工智能/RAG"]


def test_rename_can_be_redone_after_a_partial_failure(
    vault_root: Path, store: VaultStore, index: SearchIndex, organizer: Organizer
) -> None:
    """先改笔记、后落盘分类树：模拟改到一半中断（一条已改、分类树还是旧的），重做能补齐。"""
    done = _add(store, index, "已经改过的")
    todo = _add(store, index, "还没改的")
    index.upsert_note(store.update_frontmatter(done.path, category="AI工程/检索增强"))

    assert organizer.rename_category("AI工程/RAG", "检索增强") == 1

    assert [_fm(store, n).category for n in (done, todo)] == ["AI工程/检索增强"] * 2
    assert "RAG" not in _taxonomy_on_disk(vault_root)["AI工程"]["children"]


def test_rename_follows_the_disk_when_the_index_is_stale(
    store: VaultStore, index: SearchIndex, organizer: Organizer
) -> None:
    note = _add(store, index, "被手工改过的")
    # 文件在外面被改了分类，索引还以为它在 RAG 里
    store.update_frontmatter(note.path, category="计算机基础/算法")

    assert organizer.rename_category("AI工程/RAG", "检索增强") == 0
    assert _fm(store, note).category == "计算机基础/算法"


def test_delete_only_empty_categories(
    vault_root: Path, store: VaultStore, index: SearchIndex, organizer: Organizer
) -> None:
    _add(store, index, "拿不准", "AI工程/RAG", status="inbox", confidence=0.4)

    with pytest.raises(CategoryNotEmpty):
        organizer.delete_category("AI工程/RAG")  # 待整理笔记上猜的分类也算有笔记
    with pytest.raises(CategoryNotEmpty):
        organizer.delete_category("AI工程")
    with pytest.raises(CategoryNotFound):
        organizer.delete_category("不存在/小块")

    organizer.delete_category("AI工程/Agent")
    organizer.delete_category("计算机基础")
    assert list(_taxonomy_on_disk(vault_root)) == ["AI工程"]
    assert list(_taxonomy_on_disk(vault_root)["AI工程"]["children"]) == ["RAG", "提示工程"]


# ---------- 重新归类 ----------

LEGACY_NOTE = (
    "---\n"
    "id: 01M4N9MMZ8ZM4FXWMY3GARG867\n"
    "title: SFT\n"
    "category: null\n"
    "suggested_domain: 人工智能\n"
    "tags: []\n"
    "aliases: []\n"
    "summary: 使用高质量人工标注数据作为输入。\n"
    "status: inbox\n"
    "verified: false\n"
    "confidence: 0.0\n"
    "created: '2026-10-11T12:07:21+08:00'\n"
    "updated: '2026-10-11T12:07:21+08:00'\n"
    "source: {kind: screenshot, ref: assets/a.png}\n"
    "---\n\n## 一句话\n使用高质量人工标注数据作为输入。\n"
)


def test_reclassify_unsorted_only_touches_unsorted_notes(
    vault_root: Path, store: VaultStore, index: SearchIndex, organizer: Organizer, classify, git_messages
) -> None:
    (store.inbox_dir / "SFT.md").write_text(LEGACY_NOTE, encoding="utf-8")  # v3 留下的旧笔记
    index.rebuild_all()
    settled = _add(store, index, "已归类的", "计算机基础/算法")
    unplaced = _add(store, index, "没分上的", None)
    unsure = _add(store, index, "拿不准的", "AI工程/Agent", status="inbox", confidence=0.4)
    fake = classify(
        {
            "SFT": ("人工智能", "模型训练", 0.9),
            "没分上的": ("人工智能", "模型训练", 0.95),
            "拿不准的": ("AI工程", "RAG", 0.3),  # 还是拿不准：换了个猜测，继续待整理
            "已归类的": ("不该", "被问到", 1.0),
        }
    )
    progress: list[tuple[int, int]] = []

    organizer.start_reclassify()
    assert organizer.reclassify("unsorted", lambda done, total: progress.append((done, total))) == 3

    assert sorted(fake.calls[0][0]) == ["SFT", "拿不准的", "没分上的"]
    assert progress == [(0, 3), (3, 3)]
    legacy = store.read_note("inbox/SFT.md")  # 旧笔记原地改写，不搬目录
    assert (legacy.frontmatter.category, legacy.frontmatter.status) == ("人工智能/模型训练", "active")
    assert "suggested_domain" not in (store.inbox_dir / "SFT.md").read_text(encoding="utf-8")
    assert (_fm(store, unplaced).category, _fm(store, unplaced).status) == ("人工智能/模型训练", "active")
    fm = _fm(store, unsure)
    assert (fm.category, fm.status, fm.confidence) == ("AI工程/RAG", "inbox", 0.3)
    assert _fm(store, settled).category == "计算机基础/算法"
    assert _fm(store, unplaced).updated == unplaced.frontmatter.updated
    # 分类树：新分类已登记；unsorted 范围不清理别人的空分类之外的东西
    disk = _taxonomy_on_disk(vault_root)
    assert disk["人工智能"] == {"locked": False, "children": {"模型训练": {"locked": False}}}
    assert git_messages[-1] == "taxonomy: 重新归类 3 条"
    tree = organizer.tree()
    assert (tree.total, tree.inbox) == (4, 1)


def test_reclassify_all_keeps_what_the_user_locked(
    vault_root: Path, store: VaultStore, index: SearchIndex, organizer: Organizer, taxonomy: TaxonomyManager, classify
) -> None:
    pinned = _add(store, index, "我手动放的", "AI工程/RAG", category_locked=True)
    loose = _add(store, index, "模型放的", "AI工程/Agent")
    taxonomy.create("英语/语法")  # 使用者建的空分类
    fake = classify({"模型放的": ("人工智能", "智能体", 0.9), "我手动放的": ("不该", "被问到", 1.0)})

    organizer.start_reclassify()
    assert organizer.reclassify("all") == 1

    assert fake.calls[0][0] == ["模型放的"]
    # 开始时分类树只剩锁定的分类和含锁定笔记的分类，其余让模型重新长
    assert fake.calls[0][1] == {"AI工程/RAG": 1, "英语/语法": 0}
    assert _fm(store, pinned).category == "AI工程/RAG"
    assert _fm(store, loose).category == "人工智能/智能体"
    assert _taxonomy_on_disk(vault_root) == {
        "AI工程": {"locked": False, "children": {"RAG": {"locked": False}}},
        "英语": {"locked": True, "children": {"语法": {"locked": True}}},
        "人工智能": {"locked": False, "children": {"智能体": {"locked": False}}},
    }


def test_reclassify_batches_see_what_earlier_batches_built(
    store: VaultStore, index: SearchIndex, organizer: Organizer, classify, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "RECLASSIFY_BATCH", 2)
    for i in range(5):
        _add(store, index, f"笔记{i}", None)
    fake = classify({f"笔记{i}": ("人工智能", "模型训练", 0.9) for i in range(5)})
    progress: list[tuple[int, int]] = []

    organizer.start_reclassify()
    organizer.reclassify("unsorted", lambda done, total: progress.append((done, total)))

    assert [len(titles) for titles, _ in fake.calls] == [2, 2, 1]
    assert [counts.get("人工智能/模型训练") for _, counts in fake.calls] == [None, 2, 4]
    assert progress == [(0, 5), (2, 5), (4, 5), (5, 5)]


def test_reclassify_leaves_unanswered_notes_as_they_were(
    vault_root: Path, store: VaultStore, index: SearchIndex, organizer: Organizer, classify
) -> None:
    """全部重排时某条没分上：保持原样，而且它原来的分类要重新登记回分类树。"""
    kept = _add(store, index, "模型没答的", "AI工程/Agent")
    classify({}).error = RuntimeError("超时")

    organizer.start_reclassify()
    assert organizer.reclassify("all") == 0

    assert (_fm(store, kept).category, _fm(store, kept).status) == ("AI工程/Agent", "active")
    assert _taxonomy_on_disk(vault_root) == {
        "AI工程": {"locked": False, "children": {"Agent": {"locked": False}}}
    }


def test_only_one_reclassify_at_a_time(organizer: Organizer, classify) -> None:
    classify({})
    organizer.start_reclassify()
    with pytest.raises(ReclassifyRunning):
        organizer.start_reclassify()
    organizer.reclassify("unsorted")
    organizer.start_reclassify()  # 跑完后名额释放


# ---------- 接口 ----------


@pytest.fixture
def api(vault_root: Path, store: VaultStore, index: SearchIndex) -> TestClient:
    _add(store, index, "RAG 入门")
    _add(store, index, "没分上的", None)
    return TestClient(create_app(vault_root))


def _id(api: TestClient, title: str) -> str:
    return next(r["id"] for r in api.get("/api/notes").json() if r["title"] == title)


def test_api_tree(api: TestClient) -> None:
    data = api.get("/api/tree").json()
    assert (data["total"], data["inbox"]) == (2, 1)
    first = data["categories"][0]
    assert (first["name"], first["locked"], first["count"]) == ("AI工程", False, 1)
    assert first["children"][0] == {"name": "RAG", "path": "AI工程/RAG", "locked": False, "count": 1}


def test_api_notes_filters(api: TestClient) -> None:
    def titles(**params) -> list[str]:
        return [r["title"] for r in api.get("/api/notes", params=params).json()]

    assert titles(category="AI工程") == ["RAG 入门"]
    assert titles(category="AI工程/Agent") == []
    assert titles(status="inbox") == ["没分上的"]
    assert sorted(titles()) == ["RAG 入门", "没分上的"]
    assert api.get("/api/notes", params={"status": "active"}).status_code == 422


def test_api_move_note(api: TestClient) -> None:
    note_id = _id(api, "没分上的")
    resp = api.put(f"/api/notes/{note_id}/category", json={"category": "计算机基础/算法"})
    assert resp.status_code == 200
    assert (resp.json()["category"], resp.json()["category_locked"], resp.json()["status"]) == (
        "计算机基础/算法",
        True,
        "active",
    )
    assert api.get("/api/tree").json()["inbox"] == 0

    assert api.put("/api/notes/不存在/category", json={"category": "AI工程/RAG"}).status_code == 404
    assert api.put(f"/api/notes/{note_id}/category", json={"category": "只有大块"}).status_code == 400


def test_api_categories_crud(api: TestClient) -> None:
    created = api.post("/api/categories", json={"path": "英语/语法"})
    assert created.status_code == 200
    english = next(p for p in created.json()["categories"] if p["name"] == "英语")
    assert english["locked"] is True and english["children"][0]["path"] == "英语/语法"
    assert api.post("/api/categories", json={"path": "英语/语法"}).status_code == 409
    assert api.post("/api/categories", json={"path": "a/b/c"}).status_code == 400

    renamed = api.put("/api/categories", json={"path": "AI工程/RAG", "name": "检索增强"})
    assert renamed.status_code == 200
    assert [r["category"] for r in api.get("/api/notes", params={"category": "AI工程"}).json()] == [
        "AI工程/检索增强"
    ]
    assert api.put("/api/categories", json={"path": "AI工程/RAG", "name": "x"}).status_code == 404
    assert api.put("/api/categories", json={"path": "AI工程/Agent", "name": "a/b"}).status_code == 400

    conflict = api.delete("/api/categories", params={"path": "AI工程/检索增强"})
    assert conflict.status_code == 409 and "还有笔记" in conflict.json()["detail"]
    assert api.delete("/api/categories", params={"path": "英语"}).status_code == 200
    assert api.delete("/api/categories", params={"path": "英语"}).status_code == 404


def test_api_reclassify_runs_as_a_job(api: TestClient, classify) -> None:
    classify({"没分上的": ("AI工程", "RAG", 0.9)})

    resp = api.post("/api/reclassify", json={"scope": "unsorted"})
    assert resp.status_code == 202
    job = api.get(f"/api/jobs/{resp.json()['job_id']}").json()

    assert (job["status"], job["stage"], job["error"]) == ("done", "归类", None)
    assert job["progress"] == {"done": 1, "total": 1}
    assert (job["created"], job["inbox"]) == ([], [])
    assert api.get("/api/tree").json()["inbox"] == 0
    assert api.post("/api/reclassify", json={"scope": "一半"}).status_code == 422


def test_api_reclassify_refuses_a_second_job(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def busy(self) -> None:
        raise ReclassifyRunning()

    monkeypatch.setattr(Organizer, "start_reclassify", busy)
    assert api.post("/api/reclassify", json={"scope": "all"}).status_code == 409


def test_startup_registers_categories_found_on_notes(
    vault_root: Path, store: VaultStore
) -> None:
    store.create_note(make_note(title="角动量", category="物理/量子力学", aliases=[]))
    with TestClient(create_app(vault_root)) as started:
        tree = started.get("/api/tree").json()
    assert "物理" in [p["name"] for p in tree["categories"]]
    assert "物理" in _taxonomy_on_disk(vault_root)
