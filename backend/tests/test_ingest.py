"""POST /api/ingest 与 GET /api/jobs：落盘、两种笔记互相链接、入索引、幂等、失败路径。模型调用全部打桩。"""

import base64
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.extract import classifier
from app.extract.classifier import Placement
from app.extract.extractor import ExtractError
from app.extract.models import Extraction, NoteDraft, TopicDraft
from app.ingest import pipeline
from app.main import create_app
from app.vault import git
from app.vault.store import VaultStore

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"

TOPIC = TopicDraft(
    title="RAG 与微调的对比",
    summary="两种让模型用上领域知识的办法各自的做法和代价。",
    content="| 方法 | 做法 |\n|---|---|\n| RAG | 先检索再生成 |\n| 微调 | 继续训练模型 |",
    supplement="两者可以结合使用。",
)
CONCEPTS = [
    NoteDraft(
        title="RAG",
        summary="生成前先检索相关片段再作答。",
        points=["检索质量决定上限"],
        aliases=["检索增强生成"],
        related=["Fine-tuning"],
        supplement=["RAG 即 Retrieval-Augmented Generation"],
    ),
    NoteDraft(
        title="Fine-tuning",
        summary="用领域数据继续训练模型。",
        points=["成本高于检索增强"],
        related=["RAG"],
    ),
]
# 主题笔记和 RAG 明确归进已有的小块（模型写的大小写与库里不同）；
# Fine-tuning 模型新建了一个小块，但把握不足 → 进待整理
PLACEMENTS = {
    "RAG 与微调的对比": Placement(parent="ai工程", child="rag", confidence=0.9),
    "RAG": Placement(parent="ai工程", child="rag", confidence=0.9),
    "Fine-tuning": Placement(parent="AI工程", child="模型微调", confidence=0.4),
}


class Stubs:
    def __init__(self) -> None:
        self.extraction = Extraction(topic=TOPIC, concepts=CONCEPTS)
        self.extract_calls: list[tuple[bytes | None, str | None]] = []
        self.classify_calls: list = []
        self.commits: list[str] = []
        self.extract_error: Exception | None = None
        self.classify_error: Exception | None = None
        self.commit_error: Exception | None = None

    def extract(self, image, text, override=None):
        self.extract_calls.append((image, text))
        if self.extract_error is not None:
            raise self.extract_error
        return self.extraction, {}

    def classify_notes(self, briefs, tree):
        self.classify_calls.append((briefs, tree))
        if self.classify_error is not None:
            raise self.classify_error
        return [PLACEMENTS[b.title] for b in briefs]

    def commit(self, vault_root, message):
        if self.commit_error is not None:
            raise self.commit_error
        self.commits.append(message)
        return True


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> Stubs:
    s = Stubs()
    monkeypatch.setattr(pipeline, "extract_drafts", s.extract)
    monkeypatch.setattr(classifier, "classify_notes", s.classify_notes)
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


def _read(vault_root: Path, name: str) -> str:
    return (vault_root / "notes" / name).read_text(encoding="utf-8")


def test_text_ingest_writes_topic_and_concept_notes(
    client: TestClient, vault_root: Path, stubs: Stubs
) -> None:
    job = _ingest(client, kind="text", text="RAG 与微调的对比")

    assert set(job) == {"status", "stage", "created", "inbox", "duplicate", "error", "progress"}
    assert (job["status"], job["stage"], job["duplicate"], job["error"], job["progress"]) == (
        "done",
        "写入",
        False,
        None,
        None,
    )
    # 主题笔记排第一，其后是概念笔记；待整理的单独一组，但文件也在 notes/
    assert [(r["type"], r["path"]) for r in job["created"]] == [
        ("topic", "notes/RAG 与微调的对比.md"),
        ("concept", "notes/RAG.md"),
    ]
    assert [(r["type"], r["path"]) for r in job["inbox"]] == [("concept", "notes/Fine-tuning.md")]
    assert all(len(r["id"]) == 26 for r in job["created"] + job["inbox"])
    assert list((vault_root / "inbox").glob("*.md")) == []

    # 自动归类：沿用库里已有的写法；把握不足的进待整理但保留模型的猜测
    assert [(r["category"], r["status"]) for r in job["created"]] == [("AI工程/RAG", "active")] * 2
    pending = job["inbox"][0]
    assert (pending["category"], pending["status"], pending["confidence"]) == (
        "AI工程/模型微调",
        "inbox",
        0.4,
    )
    # 这份输入的所有笔记在一次调用里一起归类（主题笔记排第一），模型能看到当前的分类树
    (briefs, tree), = stubs.classify_calls
    assert [b.title for b in briefs] == ["RAG 与微调的对比", "RAG", "Fine-tuning"]
    assert [p.name for p in tree.categories] == ["AI工程", "计算机基础"]
    taxonomy = client.get("/api/taxonomy").json()
    assert taxonomy["categories"]["AI工程"]["children"]["模型微调"] == {"locked": False}

    # 主题笔记：保留原结构的内容、指向概念笔记的链接、模型补充单独成块、原始输入
    topic = _read(vault_root, "RAG 与微调的对比.md")
    assert "type: topic" in topic
    assert "## 内容\n| 方法 | 做法 |\n|---|---|\n| RAG | 先检索再生成 |" in topic
    assert "## 概念\n- [[RAG]]\n- [[Fine-tuning]]\n" in topic
    assert "## 模型补充\n> 以下内容由模型根据自身知识补充，不是来自你的资料。\n\n两者可以结合使用。" in topic
    assert topic.rstrip().endswith("## 原始输入\n> RAG 与微调的对比")
    # 概念笔记：链接回主题笔记和同批的其他概念；原始输入只放在主题笔记里
    rag = _read(vault_root, "RAG.md")
    assert "type: concept" in rag
    assert "## 要点\n- 检索质量决定上限\n" in rag
    assert "- RAG 即 Retrieval-Augmented Generation\n\n## 关联\n" in rag
    assert rag.rstrip().endswith("## 关联\n- [[RAG 与微调的对比]]\n- [[Fine-tuning]]")
    assert "## 原始输入" not in rag and "## 一句话" not in rag

    # 立刻可检索
    found = client.get("/api/search", params={"q": "检索增强生成"}).json()["results"]
    assert [r["title"] for r in found] == ["RAG"]
    assert [r["title"] for r in client.get("/api/inbox").json()] == ["Fine-tuning"]
    assert stubs.commits == ["note: 新建 3 条 (来源 text)"]


def test_single_concept_input_makes_one_self_contained_note(
    client: TestClient, vault_root: Path, stubs: Stubs
) -> None:
    """输入只讲一个概念：没有主题笔记，原始输入放在概念笔记里。"""
    stubs.extraction = Extraction(topic=None, concepts=CONCEPTS[:1])
    job = _ingest(client, kind="text", text="RAG 是先检索再生成")

    assert [(r["type"], r["title"]) for r in job["created"]] == [("concept", "RAG")]
    rag = _read(vault_root, "RAG.md")
    assert "## 关联\n（暂无）\n\n## 原始输入\n> RAG 是先检索再生成" in rag
    assert stubs.commits == ["note: 新建 1 条 (来源 text)"]


def test_topic_only_input(client: TestClient, vault_root: Path, stubs: Stubs) -> None:
    stubs.extraction = Extraction(topic=TOPIC, concepts=[])
    job = _ingest(client, kind="text", text="一段没有可拆概念的文字")

    assert [(r["type"], r["title"]) for r in job["created"]] == [("topic", "RAG 与微调的对比")]
    assert "## 概念" not in _read(vault_root, "RAG 与微调的对比.md")


@pytest.mark.parametrize("prefix", ["", "data:image/png;base64,"])
def test_screenshot_ingest_saves_asset(
    client: TestClient, vault_root: Path, stubs: Stubs, prefix: str
) -> None:
    job = _ingest(client, kind="screenshot", image_b64=prefix + base64.b64encode(PNG).decode())

    assert job["status"] == "done"
    assert stubs.extract_calls == [(PNG, None)]
    assets = list((vault_root / "assets").glob("*.png"))
    assert len(assets) == 1 and assets[0].read_bytes() == PNG
    topic, rag = _read(vault_root, "RAG 与微调的对比.md"), _read(vault_root, "RAG.md")
    assert topic.rstrip().endswith(f"## 原始输入\n![[assets/{assets[0].name}]]")
    # 概念笔记正文里不重复放截图，但来源仍然记在 frontmatter
    assert "![[" not in rag and f"ref: assets/{assets[0].name}" in rag
    assert stubs.commits == ["note: 新建 3 条 (来源 screenshot)"]


def test_duplicate_returns_previous_result_and_force_reprocesses(
    client: TestClient, vault_root: Path, stubs: Stubs
) -> None:
    first = _ingest(client, kind="text", text="同一段输入")
    again = _ingest(client, kind="text", text="  同一段输入\n")  # 首尾空白不算不同输入

    assert again["status"] == "done" and again["duplicate"] is True
    assert again["created"] == first["created"]
    assert again["inbox"] == first["inbox"]
    assert len(stubs.extract_calls) == 1
    assert len(list((vault_root / "notes").glob("*.md"))) == 3  # 没有重复生成

    forced = _ingest(client, kind="text", text="同一段输入", force=True)
    assert forced["duplicate"] is False
    assert len(stubs.extract_calls) == 2
    assert [r["path"] for r in forced["created"]] == [
        "notes/RAG 与微调的对比 (2).md",
        "notes/RAG (2).md",
    ]
    assert forced["created"][0]["id"] != first["created"][0]["id"]
    # 重名时文件名带了序号：新的主题笔记和新的概念笔记要互相指向**实际**的文件名
    assert "## 概念\n- [[RAG (2)]]\n- [[Fine-tuning (2)]]\n" in _read(vault_root, "RAG 与微调的对比 (2).md")
    assert "## 关联\n- [[RAG 与微调的对比 (2)]]\n" in _read(vault_root, "RAG (2).md")


def test_concept_links_are_fixed_when_the_predicted_topic_name_was_taken(
    client: TestClient, vault_root: Path, stubs: Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """概念笔记先落盘，写的是「预测的」主题笔记文件名；预测错了（并发录入）要改回来。"""
    monkeypatch.setattr(VaultStore, "next_stem", lambda self, title: "被别人占了的名字")
    _ingest(client, kind="text", text="并发时文件名预测错了")

    rag = _read(vault_root, "RAG.md")
    assert "[[RAG 与微调的对比]]" in rag and "被别人占了的名字" not in rag
    # 索引里的正文也跟着改了
    assert client.get("/api/search", params={"q": "被别人占了的名字"}).json()["results"] == []


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
    for f in (vault_root / "notes").glob("*.md"):
        f.unlink()
    client.post("/api/reindex")

    job = _ingest(client, kind="text", text="同一段输入")
    assert job["duplicate"] is False
    assert len(stubs.extract_calls) == 2
    assert [r["path"] for r in job["created"]] == ["notes/RAG 与微调的对比.md", "notes/RAG.md"]


def test_extract_failure_marks_job_failed_and_is_not_logged(
    client: TestClient, vault_root: Path, stubs: Stubs
) -> None:
    stubs.extract_error = ExtractError("模型输出不合法")
    body = dict(kind="screenshot", image_b64=base64.b64encode(PNG).decode())

    job = _ingest(client, **body)
    assert job["status"] == "failed"
    assert "模型输出不合法" in job["error"]
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
    assert len(job["created"]) == 2


def test_classification_failure_does_not_fail_ingest(
    client: TestClient, vault_root: Path, stubs: Stubs
) -> None:
    """SCHEMA 3.2：分类失败不得让录入失败——笔记照常保存，进待整理。"""
    stubs.classify_error = RuntimeError("429 限流")
    job = _ingest(client, kind="text", text="分类这一步会失败")

    assert job["status"] == "done" and job["created"] == []
    assert [(r["title"], r["category"], r["confidence"]) for r in job["inbox"]] == [
        ("RAG 与微调的对比", None, 0.0),
        ("RAG", None, 0.0),
        ("Fine-tuning", None, 0.0),
    ]
    assert (vault_root / "notes/RAG.md").is_file()


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
