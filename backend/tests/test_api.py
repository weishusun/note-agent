"""三个只读 API 的契约测试（SCHEMA 第 5 节）。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.search.index import SearchIndex
from app.vault.store import VaultStore
from tests.conftest import make_note


@pytest.fixture
def client(vault_root: Path, store: VaultStore, index: SearchIndex) -> TestClient:
    note = make_note()
    store.create_note(note)
    index.upsert_note(note)
    inbox_note = make_note(
        title="检索增强踩坑记录",
        category=None,
        status="inbox",
        confidence=0.4,
        aliases=[],
    )
    store.create_note(inbox_note)
    index.upsert_note(inbox_note)
    return TestClient(create_app(vault_root))


def test_get_taxonomy(client: TestClient) -> None:
    resp = client.get("/api/taxonomy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == 2
    assert data["categories"]["AI工程"]["children"]["RAG"] == {"locked": False}
    assert "向量检索" in data["tags"]


def test_get_search(client: TestClient) -> None:
    resp = client.get("/api/search", params={"q": "检索增强", "limit": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert data["matched_category"] is None
    ref = data["results"][0]
    assert ref["path"] == "notes/RAG 检索增强生成.md"
    assert set(ref) == {
        "id",
        "type",
        "path",
        "title",
        "category",
        "category_locked",
        "duplicate_of",
        "image",
        "tags",
        "summary",
        "status",
        "updated",
        "confidence",
    }

    # matched_category 是对 q 的查询理解，不是 category 参数的回显
    resp = client.get("/api/search", params={"q": "", "category": "AI工程/RAG"})
    assert resp.json()["matched_category"] is None
    resp = client.get("/api/search", params={"q": "rag"})
    assert resp.json()["matched_category"] == "AI工程/RAG"


def test_search_include_inbox_param(client: TestClient) -> None:
    default = client.get("/api/search", params={"q": "检索增强"}).json()
    assert [r["title"] for r in default["results"]] == ["RAG 检索增强生成"]

    with_inbox = client.get(
        "/api/search", params={"q": "检索增强", "include_inbox": "true"}
    ).json()
    assert [r["title"] for r in with_inbox["results"]] == [
        "RAG 检索增强生成",
        "检索增强踩坑记录",
    ]


def test_get_inbox_returns_only_inbox_notes(client: TestClient) -> None:
    resp = client.get("/api/inbox")
    assert resp.status_code == 200
    data = resp.json()
    assert [r["title"] for r in data] == ["检索增强踩坑记录"]
    assert data[0]["status"] == "inbox"
    assert data[0]["category"] is None
    # active 笔记（RAG 检索增强生成）不得出现
    assert all(r["status"] == "inbox" for r in data)


def test_get_note(client: TestClient) -> None:
    resp = client.get("/api/notes/notes/RAG 检索增强生成.md")
    assert resp.status_code == 200
    data = resp.json()
    assert data["frontmatter"]["title"] == "RAG 检索增强生成"
    assert data["body"].startswith("## 一句话")

    assert client.get("/api/notes/notes/不存在.md").status_code == 404
    assert client.get("/api/notes/..%2f..%2fsecret.md").status_code in (400, 404)


# ---------- 笔记列表 / 重建索引 / 素材 ----------


def test_list_notes_recent_first_including_inbox(client: TestClient) -> None:
    data = client.get("/api/notes").json()
    assert {r["title"] for r in data} == {"RAG 检索增强生成", "检索增强踩坑记录"}
    assert len(client.get("/api/notes", params={"limit": 1}).json()) == 1
    assert client.get("/api/notes", params={"limit": 0}).status_code == 422


def test_list_notes_orders_by_updated_desc(
    vault_root: Path, store: VaultStore, index: SearchIndex
) -> None:
    for title, day in (("旧笔记", 1), ("新笔记", 9), ("中间的笔记", 5)):
        note = make_note(title=title, aliases=[])
        stamp = note.frontmatter.updated.replace(day=day)
        note.frontmatter = note.frontmatter.model_copy(update={"updated": stamp})
        store.create_note(note)
        index.upsert_note(note)

    titles = [r["title"] for r in TestClient(create_app(vault_root)).get("/api/notes").json()]
    assert titles == ["新笔记", "中间的笔记", "旧笔记"]


def test_reindex_follows_the_disk(client: TestClient, vault_root: Path) -> None:
    """不变量 13：用户在 Obsidian 里删了一条、手写了一条坏的、又加了一条好的。"""
    (vault_root / "notes/检索增强踩坑记录.md").unlink()
    (vault_root / "notes/手写的坏笔记.md").write_text("没有 frontmatter 的纯文本\n", encoding="utf-8")
    good = (vault_root / "notes/RAG 检索增强生成.md").read_text(encoding="utf-8")
    (vault_root / "notes/手动复制的笔记.md").write_text(
        good.replace("title: RAG 检索增强生成", "title: 手动复制的笔记").replace(
            good.split("id: ")[1].split("\n")[0], "null"
        ),
        encoding="utf-8",
    )

    resp = client.post("/api/reindex")
    assert resp.status_code == 200
    assert resp.json() == {"indexed": 2}  # 坏笔记被跳过，不让重建失败
    assert {r["title"] for r in client.get("/api/notes").json()} == {
        "RAG 检索增强生成",
        "手动复制的笔记",
    }


def test_startup_rebuilds_index_from_disk(vault_root: Path, store: VaultStore) -> None:
    store.create_note(make_note())  # 只落盘、不入索引：模拟索引落后于磁盘
    app = create_app(vault_root)

    assert TestClient(app).get("/api/notes").json() == []  # 不走 lifespan 时索引是空的
    with TestClient(app) as started:
        assert [r["title"] for r in started.get("/api/notes").json()] == ["RAG 检索增强生成"]


def test_get_asset(client: TestClient, store: VaultStore) -> None:
    rel = store.save_asset(b"\x89PNG-bytes", "png")
    name = rel.split("/")[1]

    resp = client.get(f"/api/assets/{name}")
    assert resp.status_code == 200
    assert resp.content == b"\x89PNG-bytes"
    assert resp.headers["content-type"] == "image/png"

    assert client.get("/api/assets/不存在.png").status_code == 404
    # 不能借素材接口读 vault 里别的文件
    assert client.get("/api/assets/..%2ftaxonomy.yaml").status_code in (400, 404)
    assert client.get("/api/assets/%2e%2e%5ctaxonomy.yaml").status_code in (400, 404)
