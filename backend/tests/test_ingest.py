"""POST /api/ingest 与 GET /api/jobs：落盘、入索引、幂等、失败路径。模型调用全部打桩。"""

import base64
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.extract.extractor import ExtractError
from app.extract.models import ClassifyResult, NoteDraft
from app.ingest import pipeline
from app.main import create_app
from app.vault import git

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"

DRAFTS = [
    NoteDraft(
        title="RAG",
        summary="生成前先检索相关片段再作答。",
        points=["检索质量决定上限"],
        aliases=["检索增强生成"],
        related=["Fine-tuning"],
    ),
    NoteDraft(
        title="Fine-tuning",
        summary="用领域数据继续训练模型。",
        points=["成本高于检索增强"],
        related=["RAG"],
    ),
]
RESULTS = [
    ClassifyResult(category="ai-engineering/rag", tags=["向量检索"], confidence=0.9),
    ClassifyResult(category=None, suggested_domain="模型训练", confidence=0.0),
]


class Stubs:
    def __init__(self) -> None:
        self.extract_calls: list[tuple[bytes | None, str | None]] = []
        self.commits: list[str] = []
        self.extract_error: Exception | None = None
        self.commit_error: Exception | None = None

    def extract(self, image, text, override=None):
        self.extract_calls.append((image, text))
        if self.extract_error is not None:
            raise self.extract_error
        return DRAFTS, {}

    def classify_batch(self, drafts, leaves, tags, known_domains):
        return RESULTS

    def commit(self, vault_root, message):
        if self.commit_error is not None:
            raise self.commit_error
        self.commits.append(message)
        return True


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> Stubs:
    s = Stubs()
    monkeypatch.setattr(pipeline, "extract_drafts", s.extract)
    monkeypatch.setattr(pipeline, "classify_batch", s.classify_batch)
    monkeypatch.setattr(git, "commit", s.commit)
    return s


@pytest.fixture
def client(vault_root: Path, stubs: Stubs) -> TestClient:
    return TestClient(create_app(vault_root))


def _ingest(client: TestClient, **body) -> dict:
    resp = client.post("/api/ingest", json=body)
    assert resp.status_code == 202, resp.text
    job = client.get(f"/api/jobs/{resp.json()['job_id']}")
    assert job.status_code == 200
    return job.json()


def test_text_ingest_writes_notes_and_indexes(
    client: TestClient, vault_root: Path, stubs: Stubs
) -> None:
    job = _ingest(client, kind="text", text="RAG 与微调的对比")

    assert set(job) == {"status", "stage", "created", "inbox", "duplicate", "error"}
    assert (job["status"], job["stage"], job["duplicate"], job["error"]) == (
        "done",
        "写入",
        False,
        None,
    )
    assert [r["path"] for r in job["created"]] == ["notes/RAG.md"]
    assert [r["path"] for r in job["inbox"]] == ["inbox/Fine-tuning.md"]
    assert all(len(r["id"]) == 26 for r in job["created"] + job["inbox"])

    # 同批草稿互相建了双链，且文件真实存在
    rag = (vault_root / "notes/RAG.md").read_text(encoding="utf-8")
    assert "- [[Fine-tuning]]" in rag
    assert "## 原始输入\n> RAG 与微调的对比" in rag
    assert (vault_root / "inbox/Fine-tuning.md").is_file()

    # 立刻可检索
    found = client.get("/api/search", params={"q": "RAG"}).json()["results"]
    assert [r["id"] for r in found] == [job["created"][0]["id"]]
    assert [r["title"] for r in client.get("/api/inbox").json()] == ["Fine-tuning"]

    assert stubs.commits == ["note: 新建 2 条 (来源 text)"]


@pytest.mark.parametrize("prefix", ["", "data:image/png;base64,"])
def test_screenshot_ingest_saves_asset(
    client: TestClient, vault_root: Path, stubs: Stubs, prefix: str
) -> None:
    job = _ingest(client, kind="screenshot", image_b64=prefix + base64.b64encode(PNG).decode())

    assert job["status"] == "done"
    assert stubs.extract_calls == [(PNG, None)]
    assets = list((vault_root / "assets").glob("*.png"))
    assert len(assets) == 1 and assets[0].read_bytes() == PNG
    rag = (vault_root / "notes/RAG.md").read_text(encoding="utf-8")
    assert f"![[assets/{assets[0].name}]]" in rag
    assert f"ref: assets/{assets[0].name}" in rag
    assert stubs.commits == ["note: 新建 2 条 (来源 screenshot)"]


def test_duplicate_returns_previous_result_and_force_reprocesses(
    client: TestClient, vault_root: Path, stubs: Stubs
) -> None:
    first = _ingest(client, kind="text", text="同一段输入")
    again = _ingest(client, kind="text", text="  同一段输入\n")  # 首尾空白不算不同输入

    assert again["status"] == "done" and again["duplicate"] is True
    assert again["created"] == first["created"]
    assert again["inbox"] == first["inbox"]
    assert len(stubs.extract_calls) == 1
    assert len(list((vault_root / "notes").glob("*.md"))) == 1

    forced = _ingest(client, kind="text", text="同一段输入", force=True)
    assert forced["duplicate"] is False
    assert len(stubs.extract_calls) == 2
    assert [r["path"] for r in forced["created"]] == ["notes/RAG (2).md"]
    assert forced["created"][0]["id"] != first["created"][0]["id"]


def test_same_content_different_kind_is_not_duplicate(client: TestClient, stubs: Stubs) -> None:
    _ingest(client, kind="text", text="abcd")
    job = _ingest(client, kind="screenshot", image_b64=base64.b64encode(b"abcd").decode())
    assert job["duplicate"] is False
    assert len(stubs.extract_calls) == 2


def test_duplicate_whose_notes_are_gone_is_reprocessed(
    client: TestClient, vault_root: Path, stubs: Stubs
) -> None:
    """不变量 8：当时建的笔记已全部不在索引里，这次按新输入处理。"""
    _ingest(client, kind="text", text="同一段输入")
    for sub in ("notes", "inbox"):
        for f in (vault_root / sub).glob("*.md"):
            f.unlink()
    from app.search.index import SearchIndex
    from app.vault.store import VaultStore

    SearchIndex(VaultStore(vault_root)).rebuild_all()

    job = _ingest(client, kind="text", text="同一段输入")
    assert job["duplicate"] is False
    assert len(stubs.extract_calls) == 2
    assert [r["path"] for r in job["created"]] == ["notes/RAG.md"]


def test_extract_failure_marks_job_failed_and_is_not_logged(
    client: TestClient, vault_root: Path, stubs: Stubs
) -> None:
    stubs.extract_error = ExtractError("结构化输出解析失败")
    body = dict(kind="screenshot", image_b64=base64.b64encode(PNG).decode())

    job = _ingest(client, **body)
    assert job["status"] == "failed"
    assert "结构化输出解析失败" in job["error"]
    assert job["created"] == [] and job["inbox"] == []
    # 失败不落任何文件，包括截图素材
    assert list((vault_root / "notes").glob("*")) == []
    assert list((vault_root / "assets").glob("*")) == []
    assert stubs.commits == []

    # 失败的输入没进幂等日志，重发会真正重跑
    stubs.extract_error = None
    retry = _ingest(client, **body)
    assert retry["status"] == "done" and retry["duplicate"] is False


def test_git_commit_failure_does_not_fail_job(client: TestClient, stubs: Stubs) -> None:
    stubs.commit_error = subprocess.CalledProcessError(128, ["git", "add", "-A"])
    job = _ingest(client, kind="text", text="vault 不是 git 仓库")
    assert job["status"] == "done"
    assert len(job["created"]) == 1


@pytest.mark.parametrize(
    "body, detail",
    [
        (dict(kind="text", text="   "), "非空 text"),
        (dict(kind="text", text="长" * 2000), "短文本"),
        (dict(kind="screenshot"), "image_b64"),
        (dict(kind="screenshot", image_b64="不是 base64!"), "base64"),
        (dict(kind="file", file_b64="aGk=", filename="讲义.pdf"), "暂不支持"),
        (dict(kind="generated", text="记录量子力学"), "暂不支持"),
    ],
)
def test_invalid_requests_rejected(
    client: TestClient, stubs: Stubs, body: dict, detail: str
) -> None:
    resp = client.post("/api/ingest", json=body)
    assert resp.status_code == 400
    assert detail in resp.json()["detail"]
    assert stubs.extract_calls == []


def test_unknown_job_is_404(client: TestClient) -> None:
    assert client.get("/api/jobs/不存在").status_code == 404
