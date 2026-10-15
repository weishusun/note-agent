"""模型补充：补充技能的输出整理、区块替换、录入后自动补、阅读区「重新补充」、合并时的取舍。"""

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.extract import supplement
from app.extract.models import Extraction
from app.ingest.assemble import SUPPLEMENT_HEADING, SUPPLEMENT_MARKER
from app.main import SUPPLEMENT_KIND, create_app
from app.organize.merge import merge_bodies
from app.organize.supplement import material_of, replace_supplement

# conftest 会把 write_supplement 换成假的，这里先拿到真的
from app.extract.supplement import write_supplement as real_write_supplement
from tests.conftest import FAKE_SUPPLEMENT
from tests.test_ingest import CONCEPTS, PNG, TOPIC, Stubs, _ingest, stubs  # noqa: F401

LONG = "这是一段足够长的补充内容，用来通过最短长度的检查。" * 8


# ---------- 整理模型的输出 ----------


def test_clean_keeps_a_good_answer() -> None:
    text = f"### 是什么\n\n{LONG}\n\n### 举个例子\n\n- 第一步\n- 第二步"
    assert supplement.clean_supplement(text) == text


def test_clean_strips_wrappers_preamble_and_big_headings() -> None:
    raw = (
        "```markdown\n## 模型补充\n> 以下内容由模型根据自身知识补充，不是来自你的资料。\n"
        f"以下是补充内容：\n\n# 原理\n\n{LONG}\n\n## 和 [[SFT]] 的区别\n\n见 [[Transformer|变换器]]。\n\n\n\n结尾\n```"
    )
    cleaned = supplement.clean_supplement(raw)
    assert cleaned.startswith("### 原理\n\n")
    assert "### 和 SFT 的区别" in cleaned and "见 变换器。" in cleaned
    assert "[[" not in cleaned and "```" not in cleaned and "模型补充" not in cleaned
    assert "\n\n\n" not in cleaned


def test_clean_turns_latex_into_code() -> None:
    """界面不渲染 LaTeX：公式块变成代码块，行内公式变成行内代码；价格里的 $ 不动。"""
    raw = (
        f"{LONG}\n\n1. **拼接**：\n   $$\n   \\text{{MultiHead}} = \\text{{Concat}}(h_1, h_2)W^O\n   $$\n\n"
        "\\[\nQ_i = XW_i^Q\n\\]\n\n"
        "其中 $d_k$ 是维度，\\(h\\) 是头数，$$x^2$$ 也算；售价 $5 到 $10 不算。"
    )
    cleaned = supplement.clean_supplement(raw)
    assert "   ```\n   \\text{MultiHead} = \\text{Concat}(h_1, h_2)W^O\n   ```" in cleaned
    assert "```\nQ_i = XW_i^Q\n```" in cleaned
    assert "其中 `d_k` 是维度，`h` 是头数，`x^2` 也算；售价 $5 到 $10 不算。" in cleaned
    assert "$$" not in cleaned and "\\[" not in cleaned


def test_clean_rejects_a_too_short_answer() -> None:
    with pytest.raises(supplement.SupplementError, match="太短"):
        supplement.clean_supplement("就一句话。")


def test_clean_cuts_a_runaway_answer_at_a_paragraph_boundary() -> None:
    paragraph = "段" * 900
    cleaned = supplement.clean_supplement("\n\n".join([paragraph] * 5))
    assert len(cleaned) <= supplement.SUPPLEMENT_MAX_CHARS
    assert cleaned == "\n\n".join([paragraph] * 3)
    assert not cleaned.endswith("…")


def test_write_supplement_asks_the_supplement_model(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    class FakeModel:
        def invoke(self, messages):
            prompts.append(messages[0].content)
            return SimpleNamespace(content=LONG)

    monkeypatch.setattr(supplement, "get_supplement_model", lambda: FakeModel())
    out = real_write_supplement(
        "多头注意力", "并行多个注意力头。", "## 要点\n- 多个头", note_type="concept", aliases=["MHA"]
    )
    assert out == LONG
    prompt = prompts[0]
    assert "标题：多头注意力" in prompt and "别名：MHA" in prompt and "- 多个头" in prompt
    assert "600 到 1000 字" in prompt and "概念笔记" in prompt


# ---------- 区块 ----------

BODY = """## 要点
- 包含多个注意力头

## 模型补充
> 以下内容由模型根据自身知识补充，不是来自你的资料。

- 旧的一条补充

## 关联
- [[Transformer]]

## 原始输入
![[assets/a.png]]
"""


def test_replace_supplement_swaps_only_that_block() -> None:
    body = replace_supplement(BODY, "### 是什么\n\n新的补充。")
    assert "旧的一条补充" not in body
    assert f"{SUPPLEMENT_HEADING}\n{SUPPLEMENT_MARKER}\n\n### 是什么\n\n新的补充。\n\n## 关联" in body
    assert body.startswith("## 要点\n- 包含多个注意力头") and body.endswith("![[assets/a.png]]\n")


def test_replace_supplement_inserts_before_links_or_appends() -> None:
    no_block = "## 要点\n- 一条\n\n## 关联\n- [[X]]\n"
    body = replace_supplement(no_block, "补充。")
    assert body.index(SUPPLEMENT_HEADING) < body.index("## 关联")
    assert replace_supplement("## 内容\n正文\n", "补充。").endswith(f"{SUPPLEMENT_MARKER}\n\n补充。\n")


def test_material_leaves_out_the_old_supplement_and_raw_input() -> None:
    material = material_of(BODY)
    assert "包含多个注意力头" in material and "[[Transformer]]" in material
    assert "旧的一条补充" not in material and "assets/a.png" not in material


def test_merging_keeps_one_prose_supplement() -> None:
    kept = replace_supplement("## 要点\n- 甲\n", "### 原理\n\n保留那条的补充。")
    other = replace_supplement("## 要点\n- 乙\n", "### 原理\n\n被合并那条的补充。")
    body, _summary = merge_bodies(kept, other, kept_summary="概要", merged_summary="概要")
    assert "保留那条的补充" in body and "被合并那条的补充" not in body
    assert body.count("### 原理") == 1


# ---------- 录入后自动补 ----------


@pytest.fixture
def auto_client(vault_root: Path, stubs: Stubs) -> TestClient:  # noqa: F811
    # 提取不再出补充：主题笔记和概念笔记都不带
    stubs.extraction = Extraction(
        topic=TOPIC.model_copy(update={"supplement": ""}),
        concepts=[c.model_copy(update={"supplement": []}) for c in CONCEPTS],
    )
    return TestClient(create_app(vault_root))


def _jobs(client: TestClient) -> list[dict]:
    assert client.app.state.queue.wait_idle()
    return client.get("/api/jobs").json()


def test_screenshot_ingest_queues_one_supplement_job(
    auto_client: TestClient, vault_root: Path, supplement_calls: list[dict], stubs: Stubs  # noqa: F811
) -> None:
    image = base64.b64encode(PNG).decode()
    ingest = _ingest(auto_client, kind="screenshot", image_b64=image)
    jobs = _jobs(auto_client)
    assert [j["kind"] for j in jobs] == [SUPPLEMENT_KIND, "screenshot"]
    job = jobs[0]
    assert job["status"] == "done" and job["label"].startswith("模型补充 · ")

    # 截图的主题笔记和两条概念笔记都补了，补的时候不给模型看「原始输入」
    assert sorted(c["title"] for c in supplement_calls) == ["Fine-tuning", "RAG", "RAG 与微调的对比"]
    assert all("assets/" not in c["body"] for c in supplement_calls)
    assert {c["note_type"] for c in supplement_calls} == {"topic", "concept"}
    created = {n["id"] for n in ingest["created"] + ingest["inbox"]}
    assert {n["id"] for n in job["created"] + job["inbox"]} == created
    text = (vault_root / "notes" / "RAG.md").read_text(encoding="utf-8")
    assert f"{SUPPLEMENT_HEADING}\n{SUPPLEMENT_MARKER}\n\n{FAKE_SUPPLEMENT}\n" in text
    assert any(m == "note: 模型补充「RAG」" for m in stubs.commits)


def test_long_documents_only_supplement_their_concepts(
    auto_client: TestClient, supplement_calls: list[dict]
) -> None:
    _ingest(auto_client, kind="text", text="长" * 3000)
    _jobs(auto_client)
    assert sorted(c["title"] for c in supplement_calls) == ["Fine-tuning", "RAG"]


def test_failed_supplements_fail_the_job_and_can_be_retried(
    auto_client: TestClient, monkeypatch: pytest.MonkeyPatch, vault_root: Path
) -> None:
    def broken(*args, **kwargs):
        raise RuntimeError("模型超时")

    monkeypatch.setattr(supplement, "write_supplement", broken)
    _ingest(auto_client, kind="text", text="RAG 与微调的对比")
    job = _jobs(auto_client)[0]
    assert job["kind"] == SUPPLEMENT_KIND and job["status"] == "failed"
    assert "模型超时" in job["error"]
    # 笔记照常在，只是没有补充
    assert SUPPLEMENT_HEADING not in (vault_root / "notes" / "RAG.md").read_text(encoding="utf-8")

    monkeypatch.setattr(supplement, "write_supplement", lambda *a, **k: FAKE_SUPPLEMENT)
    assert auto_client.post(f"/api/jobs/{job['id']}/retry").status_code == 202
    assert _jobs(auto_client)[0]["status"] == "done"
    assert FAKE_SUPPLEMENT in (vault_root / "notes" / "RAG.md").read_text(encoding="utf-8")


# ---------- 阅读区「重新补充」 ----------


def test_supplement_endpoint_rewrites_the_block(
    auto_client: TestClient, vault_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ingest = _ingest(auto_client, kind="text", text="RAG 与微调的对比")
    _jobs(auto_client)
    rag = next(n for n in ingest["created"] + ingest["inbox"] if n["title"] == "RAG")

    monkeypatch.setattr(supplement, "write_supplement", lambda *a, **k: "### 新写的\n\n" + LONG)
    resp = auto_client.post(f"/api/notes/{rag['id']}/supplement")
    assert resp.status_code == 200 and resp.json()["id"] == rag["id"]
    text = (vault_root / "notes" / "RAG.md").read_text(encoding="utf-8")
    assert "### 新写的" in text and FAKE_SUPPLEMENT not in text
    assert text.count(SUPPLEMENT_HEADING) == 1
    assert "检索质量决定上限" in text  # 别的区块不动

    assert auto_client.post("/api/notes/no-such-note/supplement").status_code == 404


def test_supplement_endpoint_reports_model_failure_and_keeps_the_note(
    auto_client: TestClient, vault_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ingest = _ingest(auto_client, kind="text", text="RAG 与微调的对比")
    _jobs(auto_client)
    rag = next(n for n in ingest["created"] + ingest["inbox"] if n["title"] == "RAG")
    before = (vault_root / "notes" / "RAG.md").read_text(encoding="utf-8")

    def broken(*args, **kwargs):
        raise supplement.SupplementError("模型写的补充太短")

    monkeypatch.setattr(supplement, "write_supplement", broken)
    resp = auto_client.post(f"/api/notes/{rag['id']}/supplement")
    assert resp.status_code == 502 and "太短" in resp.json()["detail"]
    assert (vault_root / "notes" / "RAG.md").read_text(encoding="utf-8") == before
