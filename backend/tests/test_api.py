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
        suggested_domain="检索技术",
        aliases=[],
    )
    store.create_note(inbox_note)
    index.upsert_note(inbox_note)
    return TestClient(create_app(vault_root))


def test_get_taxonomy(client: TestClient) -> None:
    resp = client.get("/api/taxonomy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == 1
    assert "rag" in data["categories"]["ai-engineering"]["children"]
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
        "path",
        "title",
        "category",
        "suggested_domain",
        "tags",
        "summary",
        "status",
        "verified",
        "updated",
        "confidence",
    }

    resp = client.get("/api/search", params={"q": "", "category": "ai-engineering/rag"})
    assert resp.json()["matched_category"] == "ai-engineering/rag"


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
    assert data[0]["suggested_domain"] == "检索技术"
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
