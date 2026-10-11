"""app/organize/editor.py：编辑、改标题（重命名并改写链接）、删除、手写新建，以及对应的接口。"""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.extract import classifier
from app.extract.classifier import Placement
from app.main import create_app
from app.organize.editor import NoteEditor
from app.organize.service import NoteNotFound, Organizer
from app.search.index import SearchIndex
from app.search.query import find_by_id, search
from app.vault.store import VaultStore
from tests.conftest import make_note


def _add(store: VaultStore, index: SearchIndex, title: str, body: str = "## 要点\n- 一条要点\n", **kw):
    note = make_note(title=title, aliases=kw.pop("aliases", []), body=body, **kw)
    store.create_note(note)
    index.upsert_note(note)
    return note


@pytest.fixture
def editor(organizer: Organizer) -> NoteEditor:
    return NoteEditor(organizer)


def _body(store: VaultStore, name: str) -> str:
    """读回正文。落盘时末尾的换行会被 frontmatter 库去掉，这里统一补上一个。"""
    return store.read_note(f"notes/{name}.md").body.rstrip("\n") + "\n"


# ---------- 编辑 ----------


def test_edit_changes_only_the_given_fields(
    store: VaultStore, index: SearchIndex, editor: NoteEditor, git_messages
) -> None:
    note = _add(store, index, "SFT", aliases=["监督微调"], category_locked=False)
    before = store.read_note(note.path).frontmatter

    ref = editor.edit(note.frontmatter.id, summary="  改过的\n概要。 ", body="## 要点\r\n- 改过的要点\r\n\r\n")

    assert (ref.title, ref.summary, ref.path) == ("SFT", "改过的 概要。", "notes/SFT.md")
    after = store.read_note(note.path)
    assert _body(store, "SFT") == "## 要点\n- 改过的要点\n"
    assert after.frontmatter.aliases == ["监督微调"]  # 没给的字段不动
    assert (after.frontmatter.id, after.frontmatter.category) == (before.id, before.category)
    assert after.frontmatter.updated > before.updated  # 改内容要更新时间
    assert [r.title for r in search(index, "改过的要点")] == ["SFT"]
    assert git_messages[-1] == "note: 编辑「SFT」"


def test_edit_aliases_are_cleaned(store: VaultStore, index: SearchIndex, editor: NoteEditor) -> None:
    note = _add(store, index, "SFT")
    ref_id = note.frontmatter.id
    editor.edit(ref_id, aliases=[" 监督微调 ", "", "sft", "监督微调", "Supervised Fine-Tuning"])
    # 去空白、去空、去重、去掉与标题相同的
    assert store.read_note(note.path).frontmatter.aliases == ["监督微调", "Supervised Fine-Tuning"]
    assert [r.title for r in search(index, "Supervised Fine-Tuning")] == ["SFT"]


def test_changing_the_title_renames_the_file_and_rewrites_links(
    store: VaultStore, index: SearchIndex, editor: NoteEditor
) -> None:
    target = _add(store, index, "SFT", body="## 要点\n- 参见 [[SFT]] 自己\n")
    _add(store, index, "训练流程", body="## 概念\n- [[SFT]]\n- [[SFT|监督微调阶段]]\n- [[SFTP]]\n")
    _add(store, index, "无关的笔记", body="## 要点\n- 没有链接\n")
    untouched_before = (store.root / "notes/无关的笔记.md").read_text(encoding="utf-8")

    ref = editor.edit(target.frontmatter.id, title="监督微调")

    assert (ref.title, ref.path, ref.id) == ("监督微调", "notes/监督微调.md", target.frontmatter.id)
    assert not (store.root / "notes/SFT.md").exists()
    # 全库里指向旧文件名的链接都改了，显示名保留；名字只是前缀相同的不动
    assert _body(store, "训练流程") == "## 概念\n- [[监督微调]]\n- [[监督微调|监督微调阶段]]\n- [[SFTP]]\n"
    assert _body(store, "监督微调") == "## 要点\n- 参见 [[监督微调]] 自己\n"
    assert (store.root / "notes/无关的笔记.md").read_text(encoding="utf-8") == untouched_before
    # 索引：旧路径没了，新路径能搜到，按 id 还是同一条
    assert find_by_id(index, target.frontmatter.id).path == "notes/监督微调.md"
    paths = [r.path for r in search(index, "监督微调")]
    assert paths[0] == "notes/监督微调.md" and "notes/SFT.md" not in paths
    assert "notes/SFT.md" not in [r.path for r in search(index, "SFT")]


def test_title_edit_that_keeps_the_filename_does_not_rename(
    store: VaultStore, index: SearchIndex, editor: NoteEditor
) -> None:
    """文件名带着避重的序号（SFT (2).md）时，没改标题就不该被重命名。"""
    _add(store, index, "SFT")
    second = _add(store, index, "SFT")
    assert second.path.as_posix() == "notes/SFT (2).md"

    ref = editor.edit(second.frontmatter.id, title="SFT", summary="只改了概要。")
    assert ref.path == "notes/SFT (2).md"
    # 只是换了文件名里不允许的字符，文件名其实不变：也不重命名
    ref = editor.edit(second.frontmatter.id, title="SFT?")
    assert (ref.title, ref.path) == ("SFT?", "notes/SFT (2).md")


def test_renaming_to_an_existing_name_gets_a_suffix(
    store: VaultStore, index: SearchIndex, editor: NoteEditor
) -> None:
    _add(store, index, "监督微调")
    note = _add(store, index, "SFT")
    assert editor.edit(note.frontmatter.id, title="监督微调").path == "notes/监督微调 (2).md"


@pytest.mark.parametrize(
    "changes, message",
    [
        (dict(title="   "), "标题不能为空"),
        (dict(title="长" * 61), "标题最长"),
        (dict(summary=""), "概要不能为空"),
        (dict(summary="长" * 81), "概要最长"),
        (dict(aliases=[f"别名{i}" for i in range(11)]), "别名最多"),
    ],
)
def test_invalid_edits_change_nothing(
    store: VaultStore, index: SearchIndex, editor: NoteEditor, changes: dict, message: str
) -> None:
    note = _add(store, index, "SFT")
    before = (store.root / note.path).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        editor.edit(note.frontmatter.id, **changes)
    assert (store.root / note.path).read_text(encoding="utf-8") == before


def test_edit_unknown_note(editor: NoteEditor) -> None:
    with pytest.raises(NoteNotFound):
        editor.edit("不存在的id", summary="x")


# ---------- 删除 ----------


def test_delete_turns_links_into_plain_text_and_clears_duplicate_marks(
    vault_root: Path, store: VaultStore, index: SearchIndex, editor: NoteEditor, git_messages
) -> None:
    doomed = _add(store, index, "SFT", category="AI工程/Agent")
    _add(store, index, "训练流程", body="## 概念\n- [[SFT]]\n- [[SFT|监督微调阶段]]\n")
    marked = _add(store, index, "监督微调")
    index.upsert_note(store.rewrite_note(marked.path, duplicate_of=doomed.frontmatter.id))

    editor.delete(doomed.frontmatter.id)

    assert not (store.root / "notes/SFT.md").exists()
    assert find_by_id(index, doomed.frontmatter.id) is None
    assert "notes/SFT.md" not in [r.path for r in search(index, "SFT")]
    assert _body(store, "训练流程") == "## 概念\n- SFT\n- 监督微调阶段\n"
    # 指向它的疑似重复标记清空（文件和索引都要清）
    assert store.read_note(marked.path).frontmatter.duplicate_of is None
    assert find_by_id(index, marked.frontmatter.id).duplicate_of is None
    # 被删空的未锁定分类自动消失
    categories = yaml.safe_load((vault_root / "taxonomy.yaml").read_text(encoding="utf-8"))["categories"]
    assert "Agent" not in categories["AI工程"]["children"]
    assert git_messages[-1] == "note: 删除「SFT」"

    with pytest.raises(NoteNotFound):
        editor.delete(doomed.frontmatter.id)


def test_delete_when_the_file_is_already_gone(
    store: VaultStore, index: SearchIndex, editor: NoteEditor
) -> None:
    note = _add(store, index, "SFT")
    (store.root / note.path).unlink()  # 在应用之外被删了，索引里还有
    editor.delete(note.frontmatter.id)
    assert find_by_id(index, note.frontmatter.id) is None


# ---------- 手写新建 ----------


def test_create_with_a_chosen_category_is_locked(
    vault_root: Path, store: VaultStore, index: SearchIndex, editor: NoteEditor, git_messages
) -> None:
    ref = editor.create(
        title="虚拟语气",
        summary="表示假设或与事实相反的情况。",
        body="## 要点\n- if 从句用过去式\n",
        aliases=["subjunctive mood", "虚拟语气"],
        category="英语/语法",
    )

    assert (ref.type, ref.path, ref.category, ref.category_locked, ref.status, ref.confidence) == (
        "concept",
        "notes/虚拟语气.md",
        "英语/语法",
        True,
        "active",
        1.0,
    )
    note = store.read_note(ref.path)
    assert (note.frontmatter.source.kind, note.frontmatter.source.ref) == ("manual", "手写")
    assert note.frontmatter.aliases == ["subjunctive mood"]
    assert note.frontmatter.category_locked is True and len(note.frontmatter.id) == 26
    assert _body(store, "虚拟语气") == "## 要点\n- if 从句用过去式\n"
    categories = yaml.safe_load((vault_root / "taxonomy.yaml").read_text(encoding="utf-8"))["categories"]
    assert categories["英语"] == {"locked": True, "children": {"语法": {"locked": True}}}
    assert [r.title for r in search(index, "subjunctive mood")] == ["虚拟语气"]
    assert git_messages[-1] == "note: 手写新建「虚拟语气」"


def test_create_without_category_is_classified_automatically(
    editor: NoteEditor, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list = []

    def fake(briefs, tree):
        seen.append([(b.title, b.summary) for b in briefs])
        return [Placement(parent="ai工程", child="rag", confidence=0.9)]

    monkeypatch.setattr(classifier, "classify_notes", fake)

    ref = editor.create(title="重排序", summary="对召回结果二次排序。", body="正文")

    assert seen == [[("重排序", "对召回结果二次排序。")]]
    assert (ref.category, ref.category_locked, ref.status, ref.confidence) == (
        "AI工程/RAG",
        False,
        "active",
        0.9,
    )


def test_create_when_classification_fails_goes_to_inbox(
    editor: NoteEditor, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(briefs, tree):
        raise RuntimeError("429 限流")

    monkeypatch.setattr(classifier, "classify_notes", boom)
    ref = editor.create(title="重排序", summary="对召回结果二次排序。", body="正文")
    assert (ref.category, ref.status) == (None, "inbox")


@pytest.mark.parametrize(
    "fields, message",
    [
        (dict(title=""), "标题不能为空"),
        (dict(summary="  "), "概要不能为空"),
        (dict(category="只有大块"), "两层"),
        (dict(body="长" * 20001), "正文最长"),
    ],
)
def test_invalid_create_writes_nothing(
    vault_root: Path, editor: NoteEditor, fields: dict, message: str
) -> None:
    args = dict(title="标题", summary="概要。", body="正文", category="英语/语法")
    args.update(fields)
    with pytest.raises(ValueError, match=message):
        editor.create(**args)
    assert list((vault_root / "notes").glob("*.md")) == []
    assert "英语" not in (vault_root / "taxonomy.yaml").read_text(encoding="utf-8")


# ---------- 接口 ----------


@pytest.fixture
def api(vault_root: Path, store: VaultStore, index: SearchIndex) -> TestClient:
    _add(store, index, "SFT")
    _add(store, index, "训练流程", body="## 概念\n- [[SFT]]\n")
    return TestClient(create_app(vault_root))


def _id(api: TestClient, title: str) -> str:
    return next(r["id"] for r in api.get("/api/notes").json() if r["title"] == title)


def test_api_edit_and_ref(api: TestClient) -> None:
    note_id = _id(api, "SFT")
    resp = api.put(f"/api/notes/{note_id}", json={"title": "监督微调", "aliases": ["SFT"]})
    assert resp.status_code == 200
    assert (resp.json()["title"], resp.json()["path"]) == ("监督微调", "notes/监督微调.md")
    assert api.get(f"/api/refs/{note_id}").json()["path"] == "notes/监督微调.md"
    detail = api.get("/api/notes/notes/训练流程.md").json()
    assert detail["body"].strip() == "## 概念\n- [[监督微调]]"

    assert api.put(f"/api/notes/{note_id}", json={"title": " "}).status_code == 400
    assert api.put("/api/notes/不存在", json={"summary": "x"}).status_code == 404
    assert api.get("/api/refs/不存在").status_code == 404


def test_api_delete(api: TestClient) -> None:
    note_id = _id(api, "SFT")
    resp = api.delete(f"/api/notes/{note_id}")
    assert (resp.status_code, resp.json()) == (200, {"deleted": True})
    assert api.get("/api/notes/notes/SFT.md").status_code == 404
    assert api.get("/api/notes/notes/训练流程.md").json()["body"].strip() == "## 概念\n- SFT"
    assert api.delete(f"/api/notes/{note_id}").status_code == 404


def test_api_create(api: TestClient) -> None:
    resp = api.post(
        "/api/notes",
        json={"title": "虚拟语气", "summary": "表示假设。", "body": "正文", "category": "英语/语法"},
    )
    assert resp.status_code == 201
    assert (resp.json()["category"], resp.json()["category_locked"]) == ("英语/语法", True)
    assert api.get("/api/notes", params={"category": "英语"}).json()[0]["title"] == "虚拟语气"

    bad = api.post("/api/notes", json={"title": "", "summary": "x", "body": "y", "category": "英语/语法"})
    assert bad.status_code == 400 and "标题不能为空" in bad.json()["detail"]
    assert api.post("/api/notes", json={"title": "只有标题"}).status_code == 422
