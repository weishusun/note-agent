"""store.py：round-trip、重名、inbox 落盘、文件名安全化。"""

from pathlib import Path

from app.vault.models import Note
from app.vault.store import VaultStore, sanitize_filename
from tests.conftest import make_note


def test_round_trip(store: VaultStore) -> None:
    note = make_note()
    rel = store.create_note(note)

    assert rel == Path("notes/RAG 检索增强生成.md")
    read_back = store.read_note(rel)

    assert read_back.frontmatter == note.frontmatter
    assert read_back.body.strip() == note.body.strip()


def test_duplicate_title_gets_numbered_suffix(store: VaultStore) -> None:
    first = make_note(body="第一篇的内容")
    second = make_note(body="第二篇的内容")

    rel1 = store.create_note(first)
    rel2 = store.create_note(second)

    assert rel1 == Path("notes/RAG 检索增强生成.md")
    assert rel2 == Path("notes/RAG 检索增强生成 (2).md")
    # 第一篇内容未被覆盖
    assert store.read_note(rel1).body.strip() == "第一篇的内容"
    assert store.read_note(rel2).body.strip() == "第二篇的内容"


def test_inbox_notes_also_land_in_notes_dir(store: VaultStore) -> None:
    """v4：状态只写在 frontmatter 里，待整理的笔记也在 notes/，以后被归类时文件不用移动。"""
    rel = store.create_note(make_note(category=None, status="inbox", confidence=0.0))

    assert rel == Path("notes/RAG 检索增强生成.md")
    read_back = store.read_note(rel)
    assert (read_back.frontmatter.status, read_back.frontmatter.category) == ("inbox", None)
    assert list(store.inbox_dir.glob("*.md")) == []


def test_filename_is_unique_across_notes_and_inbox(store: VaultStore) -> None:
    """双链只写文件名：旧版本留在 inbox/ 里的同名文件也要避开。"""
    (store.inbox_dir / "RAG 检索增强生成.md").write_text("旧版本留下的笔记", encoding="utf-8")

    rel = store.create_note(make_note())

    assert rel == Path("notes/RAG 检索增强生成 (2).md")


def test_create_note_never_starts_locked(store: VaultStore) -> None:
    note = make_note(category_locked=True)
    rel = store.create_note(note)
    assert store.read_note(rel).frontmatter.category_locked is False
    assert note.frontmatter.category_locked is False


LEGACY_NOTE = (
    "---\n"
    "id: 01M4N9MMZ8ZM4FXWMY3GARG867\n"
    "title: 旧版本的笔记\n"
    "category: null\n"
    "suggested_domain: 人工智能\n"
    "tags: []\n"
    "aliases: [旧笔记]\n"
    "summary: v3 写出来的笔记，带着两个已经删除的字段。\n"
    "status: inbox\n"
    "verified: false\n"
    "confidence: 0.0\n"
    "created: '2026-10-11T12:07:21.702215+08:00'\n"
    "updated: '2026-10-11T12:07:21.702215+08:00'\n"
    "source: {kind: screenshot, ref: assets/2026-10-11-120721.png}\n"
    "---\n"
    "\n"
    "## 一句话\n"
    "正文一个字都不能动。\n"
)


def test_legacy_fields_are_ignored_on_read_and_dropped_on_rewrite(store: VaultStore) -> None:
    """SCHEMA 2.2「旧字段的兼容」：读取时忽略不报错，下次改写时自然消失。"""
    target = store.inbox_dir / "旧版本的笔记.md"
    target.write_text(LEGACY_NOTE, encoding="utf-8")
    rel = Path("inbox/旧版本的笔记.md")

    note = store.read_note(rel)
    assert (note.frontmatter.category, note.frontmatter.category_locked) == (None, False)
    assert target.read_text(encoding="utf-8") == LEGACY_NOTE  # 读取没有副作用

    updated = store.update_frontmatter(
        rel, category="人工智能/模型训练", status="active", confidence=0.9
    )

    text = target.read_text(encoding="utf-8")
    assert "suggested_domain" not in text and "verified" not in text
    assert "category: 人工智能/模型训练" in text
    # 文件原地改写：没有搬到 notes/，id、标题、正文、时间都不变
    assert updated.path == rel and not (store.notes_dir / "旧版本的笔记.md").exists()
    read_back = store.read_note(rel)
    assert read_back.frontmatter.id == "01M4N9MMZ8ZM4FXWMY3GARG867"
    assert read_back.frontmatter.updated == note.frontmatter.updated
    assert read_back.body.strip() == "## 一句话\n正文一个字都不能动。"


def test_update_frontmatter_validates(store: VaultStore) -> None:
    rel = store.create_note(make_note())
    before = (store.root / rel).read_text(encoding="utf-8")

    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        store.update_frontmatter(rel, category="只有大块")
    with pytest.raises(ValidationError):
        store.update_frontmatter(rel, category=None, status="active")
    assert (store.root / rel).read_text(encoding="utf-8") == before


def test_filename_sanitized(store: VaultStore) -> None:
    assert sanitize_filename('a\\b/c:d*e?f"g<h>i|j') == "abcdefghij"
    assert sanitize_filename("  末尾点和空格.. ") == "末尾点和空格"

    note = make_note(title='问: RAG/FC 有何区别? 何时用*哪个?')
    rel = store.create_note(note)

    assert rel.name == "问 RAGFC 有何区别 何时用哪个.md"
    for ch in '\\/:*?"<>|':
        assert ch not in rel.name
    # title 原样保留在 frontmatter 里，不受文件名安全化影响
    assert store.read_note(rel).frontmatter.title == '问: RAG/FC 有何区别? 何时用*哪个?'


def test_create_note_generates_id(store: VaultStore) -> None:
    note = make_note()
    assert note.frontmatter.id is None  # 模型层不生成，构造出来就是 None

    rel = store.create_note(note)
    new_id = note.frontmatter.id

    assert new_id is not None
    assert len(new_id) == 26
    # 内存对象与落盘内容一致
    assert store.read_note(rel).frontmatter.id == new_id


def test_create_note_ignores_caller_supplied_id(store: VaultStore) -> None:
    """id 是写入层的创建时语义，调用方传什么都不算数。"""
    note = make_note()
    note.frontmatter = note.frontmatter.model_copy(update={"id": "调用方伪造的 id"})

    rel = store.create_note(note)

    assert note.frontmatter.id != "调用方伪造的 id"
    assert len(note.frontmatter.id) == 26
    assert store.read_note(rel).frontmatter.id == note.frontmatter.id


def test_read_note_never_backfills_id(store: VaultStore) -> None:
    """Obsidian 手建的笔记 frontmatter 里没有 id 键，读多少次都必须是 None。

    这条钉住的是「读取不得补写 id」（SCHEMA 2.2 / 不变量 5）：
    模型层一旦给 id 配 default_factory，这里每次读都会拿到一个不同的新 ULID。
    """
    target = store.notes_dir / "Obsidian 手建的笔记.md"
    target.write_text(
        "---\n"
        "title: Obsidian 手建的笔记\n"
        "category: AI工程/RAG\n"
        "suggested_domain: null\n"
        "tags: []\n"
        "aliases: []\n"
        "summary: 用户直接在 Obsidian 里建的，frontmatter 里没有 id 键。\n"
        "status: active\n"
        "verified: false\n"
        "confidence: 0.5\n"
        "created: 2026-07-30T14:22:11+08:00\n"
        "updated: 2026-07-30T14:22:11+08:00\n"
        "source:\n"
        "  kind: text\n"
        "  ref: 手写\n"
        "---\n"
        "\n"
        "正文\n",
        encoding="utf-8",
    )
    rel = Path("notes/Obsidian 手建的笔记.md")

    first = store.read_note(rel)
    second = store.read_note(rel)

    assert first.frontmatter.id is None
    assert second.frontmatter.id is None
    assert first.frontmatter.id == second.frontmatter.id
    # 文件本身也没有被补写
    assert "id:" not in target.read_text(encoding="utf-8")


def test_no_leftover_tmp_files(store: VaultStore) -> None:
    store.create_note(make_note())
    store.save_asset(b"\x89PNG", "png")
    leftovers = list(store.root.rglob("*.tmp"))
    assert leftovers == []


def test_next_stem_predicts_what_create_note_will_use(store: VaultStore) -> None:
    assert store.next_stem("RAG 检索增强生成") == "RAG 检索增强生成"
    assert store.next_stem("问: A/B?") == "问 AB"

    store.create_note(make_note())
    (store.inbox_dir / "RAG 检索增强生成 (2).md").write_text("旧目录里的同名文件", encoding="utf-8")

    predicted = store.next_stem("RAG 检索增强生成")
    assert predicted == "RAG 检索增强生成 (3)"
    assert store.create_note(make_note()).stem == predicted  # 只看不占，随后真的拿到它


def test_rewrite_note_replaces_body_in_place(store: VaultStore) -> None:
    rel = store.create_note(make_note())
    before = store.read_note(rel)

    after = store.rewrite_note(rel, body="## 要点\n- 改过的正文\n", summary="改过的概要。")

    assert after.path == rel and after.frontmatter.id == before.frontmatter.id
    read = store.read_note(rel)
    assert (read.body.strip(), read.frontmatter.summary) == ("## 要点\n- 改过的正文", "改过的概要。")


def test_save_asset_can_keep_the_original_filename(store: VaultStore) -> None:
    first = store.save_asset(b"%PDF-1", "pdf", name="讲义: 第 1 章?")
    second = store.save_asset(b"%PDF-2", ".PDF", name="讲义: 第 1 章?")
    assert (first, second) == ("assets/讲义 第 1 章.pdf", "assets/讲义 第 1 章 (2).pdf")
    assert store.read_asset("讲义 第 1 章 (2).pdf") == b"%PDF-2"
    # 不给名字：按时间起名
    assert store.save_asset(b"x", "txt").startswith("assets/20")
