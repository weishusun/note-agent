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


def test_invalid_category_goes_to_inbox(store: VaultStore, taxonomy) -> None:
    # 调用方流程：先判定，再决定落 inbox
    assert not taxonomy.is_valid_category("no-such/leaf")
    assert not taxonomy.is_valid_category("ai-engineering")  # parent 单独出现算非法

    note = make_note(category=None, status="inbox", confidence=0.3)
    rel = store.create_note(note)

    assert rel.parts[0] == "inbox"
    read_back = store.read_note(rel)
    assert read_back.frontmatter.status == "inbox"
    assert read_back.frontmatter.category is None


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


def test_no_leftover_tmp_files(store: VaultStore) -> None:
    store.create_note(make_note())
    store.save_asset(b"\x89PNG", "png")
    leftovers = list(store.root.rglob("*.tmp"))
    assert leftovers == []
