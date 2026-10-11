"""测试夹具：在 tmp_path 里搭一个临时 vault，绝不触碰真实 vault。"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.organize.service import Organizer
from app.search.index import SearchIndex
from app.vault.models import Note, NoteFrontmatter, SourceRef
from app.vault.store import VaultStore
from app.vault.taxonomy import TaxonomyManager

TZ = timezone(timedelta(hours=8))

TAXONOMY_YAML = """
version: 2
updated: 2026-07-30

categories:
  AI工程:
    locked: false
    children:
      Agent: { locked: false }
      RAG: { locked: false }
      提示工程: { locked: false }

  计算机基础:
    locked: false
    children:
      算法: { locked: false }
      数据结构: { locked: false }

tags:
  - LangGraph
  - Python
  - 向量检索
  - 上下文工程
  - 动态规划
  - 待深入
"""


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """任何测试都不许读写真实的 note-agent/settings.json（里面有密钥）。"""
    path = tmp_path / "settings.json"
    monkeypatch.setenv("NOTE_AGENT_SETTINGS", str(path))
    return path


@pytest.fixture(autouse=True)
def git_messages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """测试里的 vault 不是 git 仓库：把提交换成记录 message，也避免误提交到外层的某个仓库。"""
    from app.vault import git

    messages: list[str] = []

    def record(vault_root: Path, message: str) -> bool:
        messages.append(message)
        return True

    monkeypatch.setattr(git, "commit", record)
    return messages


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    for sub in ("notes", "inbox", "maps", "assets", ".noteagent"):
        (root / sub).mkdir(parents=True)
    (root / "taxonomy.yaml").write_text(TAXONOMY_YAML, encoding="utf-8")
    return root


@pytest.fixture
def store(vault_root: Path) -> VaultStore:
    return VaultStore(vault_root)


@pytest.fixture
def taxonomy(vault_root: Path) -> TaxonomyManager:
    return TaxonomyManager(vault_root / "taxonomy.yaml")


@pytest.fixture
def index(store: VaultStore) -> SearchIndex:
    return SearchIndex(store)


@pytest.fixture
def organizer(store: VaultStore, taxonomy: TaxonomyManager, index: SearchIndex) -> Organizer:
    return Organizer(store, taxonomy, index)


def make_note(
    title: str = "RAG 检索增强生成",
    category: str | None = "AI工程/RAG",
    tags: list[str] | None = None,
    aliases: list[str] | None = None,
    summary: str = "在生成前先检索相关片段，拼进上下文再作答。",
    status: str = "active",
    category_locked: bool = False,
    confidence: float = 0.86,
    kind: str = "text",
    body: str = "## 一句话\n在生成前先检索相关片段，拼进上下文再作答。\n\n## 要点\n- 检索质量决定上限\n",
) -> Note:
    now = datetime(2026, 7, 30, 14, 22, 11, tzinfo=TZ)
    return Note(
        frontmatter=NoteFrontmatter(
            title=title,
            category=category,
            category_locked=category_locked,
            tags=tags if tags is not None else ["向量检索"],
            aliases=aliases if aliases is not None else ["检索增强", "RAG"],
            summary=summary,
            status=status,
            confidence=confidence,
            created=now,
            updated=now,
            source=SourceRef(kind=kind, ref="原文前 100 字……"),
        ),
        body=body,
    )
