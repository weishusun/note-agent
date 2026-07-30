"""测试夹具：在 tmp_path 里搭一个临时 vault，绝不触碰真实 vault。"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.search.index import SearchIndex
from app.vault.models import Note, NoteFrontmatter, SourceRef
from app.vault.store import VaultStore
from app.vault.taxonomy import TaxonomyManager

TZ = timezone(timedelta(hours=8))

TAXONOMY_YAML = """\
version: 1
updated: 2026-07-30

categories:
  ai-engineering:
    label: AI 工程
    children:
      agent:       { label: Agent 与编排 }
      rag:         { label: 检索与上下文 }
      prompting:   { label: 提示工程 }

  cs-fundamentals:
    label: 计算机基础
    children:
      algorithm:       { label: 算法 }
      data-structure:  { label: 数据结构 }

tags:
  - LangGraph
  - Python
  - 向量检索
  - 上下文工程
  - 动态规划
  - 待深入
"""


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


def make_note(
    title: str = "RAG 检索增强生成",
    category: str | None = "ai-engineering/rag",
    tags: list[str] | None = None,
    aliases: list[str] | None = None,
    summary: str = "在生成前先检索相关片段，拼进上下文再作答。",
    status: str = "active",
    confidence: float = 0.86,
    body: str = "## 一句话\n在生成前先检索相关片段，拼进上下文再作答。\n\n## 要点\n- 检索质量决定上限\n",
) -> Note:
    now = datetime(2026, 7, 30, 14, 22, 11, tzinfo=TZ)
    return Note(
        frontmatter=NoteFrontmatter(
            title=title,
            category=category,
            tags=tags if tags is not None else ["向量检索"],
            aliases=aliases if aliases is not None else ["检索增强", "RAG"],
            summary=summary,
            status=status,
            confidence=confidence,
            created=now,
            updated=now,
            source=SourceRef(kind="text", ref="原文前 100 字……"),
        ),
        body=body,
    )
