"""「我的导图」：存成 JSON、必须是一棵树、链到的笔记按 id 认、提交节流（SCHEMA §8）。"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import mindmaps_api
from app.main import create_app
from app.search.index import SearchIndex
from app.vault.mindmaps import MindmapBody, MindmapStore
from app.vault.store import VaultStore
from tests.conftest import make_note


@pytest.fixture
def note_id(store: VaultStore, index: SearchIndex) -> str:
    note = make_note()
    store.create_note(note)
    index.upsert_note(note)
    return note.frontmatter.id


@pytest.fixture
def client(vault_root: Path, note_id: str) -> TestClient:
    return TestClient(create_app(vault_root, auto_supplement=False))


def _body(note_id: str | None = None, **overrides) -> dict:
    nodes = {
        "root": {"text": "Transformer", "children": ["a", "b"], "x": 0, "y": 0},
        "a": {"text": "注意力", "children": ["a1"], "x": 200, "y": -40, "color": "blue"},
        "a1": {"text": "多头", "x": 380, "y": -40, "note": "多个头并行，\n各看一个子空间。"},
        "b": {"text": "位置编码", "x": 200, "y": 40, "collapsed": True},
    }
    if note_id:
        nodes["a1"]["link"] = {"id": note_id, "title": "旧标题"}
    return {"title": "复习", "root": "root", "nodes": nodes, **overrides}


def test_create_empty_map_has_only_a_root(client: TestClient, vault_root: Path, git_messages: list[str]) -> None:
    resp = client.post("/api/mindmaps", json={"title": "  我的 导图 "})
    assert resp.status_code == 201
    m = resp.json()["map"]
    assert m["title"] == "我的 导图" and m["root"] == "root"
    assert m["nodes"] == {
        "root": {"text": "我的 导图", "children": [], "x": 0, "y": 0, "color": None, "note": None, "link": None, "collapsed": False}
    }
    saved = json.loads((vault_root / "mindmaps" / f"{m['id']}.json").read_text(encoding="utf-8"))
    assert saved["version"] == 1 and saved["id"] == m["id"]
    assert git_messages == ["mindmap: 新建「我的 导图」"]


def test_save_get_list_delete(client: TestClient, vault_root: Path, note_id: str) -> None:
    map_id = client.post("/api/mindmaps", json={"title": "复习"}).json()["map"]["id"]
    saved = client.put(f"/api/mindmaps/{map_id}", json=_body(note_id))
    assert saved.status_code == 200
    got = client.get(f"/api/mindmaps/{map_id}").json()
    assert got["map"]["nodes"]["a"]["color"] == "blue"
    assert got["map"]["nodes"]["a1"]["note"] == "多个头并行，\n各看一个子空间。"
    # 链到的笔记按 id 现查：笔记改过名也能拿到现在的标题
    assert got["notes"][note_id]["title"] == "RAG 检索增强生成"

    listing = client.get("/api/mindmaps").json()
    assert [(s["id"], s["title"], s["node_count"]) for s in listing] == [(map_id, "复习", 4)]

    assert client.delete(f"/api/mindmaps/{map_id}").json() == {"deleted": True}
    assert client.get(f"/api/mindmaps/{map_id}").status_code == 404
    assert client.get("/api/mindmaps").json() == []


def test_deleted_notes_are_left_out(client: TestClient, note_id: str) -> None:
    body = _body(note_id)
    body["nodes"]["b"]["link"] = {"id": "01M0000000000000000000GAWN", "title": "删掉的"}
    map_id = client.post("/api/mindmaps", json=body).json()["map"]["id"]
    notes = client.get(f"/api/mindmaps/{map_id}").json()["notes"]
    assert set(notes) == {note_id}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(root="nope"),
        lambda b: b["nodes"]["a"]["children"].append("ghost"),
        lambda b: b["nodes"]["b"].update(children=["a1"]),  # 一个节点两个父亲
        lambda b: b["nodes"]["a1"].update(children=["root"]),  # 成环
        lambda b: b["nodes"].update(lonely={"text": "孤儿"}),
        lambda b: b["nodes"]["a"].update(color="#ff0000"),
        lambda b: b["nodes"]["a"].update(text="长" * 201),
        lambda b: b["nodes"].update({"../x": {"text": "坏 id"}}),
        lambda b: b.update(title="   "),
    ],
)
def test_invalid_maps_are_rejected(client: TestClient, mutate) -> None:
    map_id = client.post("/api/mindmaps", json={"title": "复习"}).json()["map"]["id"]
    body = _body()
    mutate(body)
    assert client.put(f"/api/mindmaps/{map_id}", json=body).status_code == 422
    assert client.post("/api/mindmaps", json=body).status_code == 422


def test_bad_ids(client: TestClient) -> None:
    assert client.get("/api/mindmaps/..%2F..%2Fsecret").status_code in (400, 404)
    assert client.get("/api/mindmaps/not-a-ulid").status_code == 400
    assert client.put("/api/mindmaps/01M00000000000000000000000", json=_body()).status_code == 404


def test_saving_commits_at_most_every_ten_minutes(
    vault_root: Path, index: SearchIndex, git_messages: list[str]
) -> None:
    now = [1000.0]
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(mindmaps_api.build_router(MindmapStore(vault_root), index, vault_root, clock=lambda: now[0]))
    client = TestClient(app)
    map_id = client.post("/api/mindmaps", json={"title": "复习"}).json()["map"]["id"]
    for _ in range(5):
        now[0] += 30
        client.put(f"/api/mindmaps/{map_id}", json=_body())
    now[0] += mindmaps_api.COMMIT_INTERVAL_SECONDS
    client.put(f"/api/mindmaps/{map_id}", json=_body())
    assert git_messages == ["mindmap: 新建「复习」", "mindmap: 编辑「复习」"]


def test_unreadable_files_are_skipped_in_the_list(vault_root: Path) -> None:
    store = MindmapStore(vault_root)
    (vault_root / "mindmaps").mkdir()
    (vault_root / "mindmaps" / "01M0000000000000000000BAD0.json").write_text("{坏的", encoding="utf-8")
    assert store.list() == []


def test_body_model_accepts_the_example() -> None:
    body = MindmapBody.model_validate(_body())
    assert len(body.nodes) == 4


def test_titles_for_at_mentions(client: TestClient, note_id: str) -> None:
    titles = client.get("/api/titles").json()
    assert titles == [
        {
            "id": note_id,
            "title": "RAG 检索增强生成",
            "aliases": ["检索增强", "RAG"],
            "type": "concept",
            "path": "notes/RAG 检索增强生成.md",
        }
    ]
