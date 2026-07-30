# SCHEMA — 数据契约

> 本文件是 note-agent 的数据契约,优先级高于任何实现代码。
> 实现与本文件冲突时,改实现,不改本文件。
> 确需改本文件时,必须同一次 commit 内同步修改 `backend/app/vault/models.py`。

---

## 0. 术语

| 术语 | 含义 |
|---|---|
| note | 一条原子知识点,对应 `vault/notes/` 下的一个 `.md` 文件 |
| category | 分类,取自 `taxonomy.yaml`,**单值**,最多两层 |
| tag | 受控标签,取自 `taxonomy.yaml`,多值 |
| MOC | map of content,某分类的索引页,位于 `vault/maps/`,**完全由程序生成** |
| 录入循环 | 处理单条用户输入 → 落盘,全自动,只允许新建 |
| 整理循环 | 周期性重整理,可改分类/合并/拆分,输出报告供人审阅 |

---

## 1. Vault 布局

```
vault/                       # 独立 git 仓库
├── taxonomy.yaml            # 分类体系，唯一真相源
├── notes/                   # 原子笔记，扁平，不分子目录
│   └── RAG 检索增强生成.md
├── maps/                    # MOC 索引页，程序生成，可随时删除重建
│   └── ai-engineering__rag.md
├── inbox/                   # 分类置信度不足的笔记，等整理循环处理
├── assets/                  # 截图等原始素材
│   └── 2026-07-30-142211.png
└── .noteagent/
    └── index.db             # SQLite，派生物，可随时删除重建
```

**硬规则:`notes/` 必须扁平。** 分类靠 frontmatter 表达,不靠目录。
理由:整理循环会频繁改分类,改一个 YAML 字段是零风险,移动文件会打断所有 `[[双链]]`。

---

## 2. 笔记文件格式

### 2.1 文件名

- 文件名 = `{title}.md`,title 原样保留中文
- 移除 Windows 非法字符:`\ / : * ? " < > |`,以及首尾空格和结尾的 `.`
- 冲突时追加 ` (2)`、` (3)`
- **文件名一旦创建不可变更。** 需要改名只能由整理循环执行,且必须在同一次操作内重写全库反链

### 2.2 Frontmatter

```yaml
---
title: RAG 检索增强生成
category: ai-engineering/rag
tags: [向量检索, 上下文工程]
aliases: [检索增强, retrieval augmented generation, RAG]
summary: 在生成前先从外部知识源检索相关片段，拼进上下文再让模型作答。
status: active
confidence: 0.86
created: 2026-07-30T14:22:11+08:00
updated: 2026-07-30T14:22:11+08:00
source:
  kind: screenshot
  ref: assets/2026-07-30-142211.png
---
```

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `title` | str | ✅ | 1–60 字,与文件名一致 |
| `category` | str \| null | ✅ | 必须是 taxonomy 中存在的**叶子**路径;`null` 仅当 `status=inbox` |
| `tags` | list[str] | ✅ | 必须是 taxonomy `tags` 的子集,可为空数组,最多 6 个 |
| `aliases` | list[str] | ✅ | 可为空。导航层的一等公民,尽量多填 |
| `summary` | str | ✅ | ≤ 50 字,一句话。导航结果列表显示这一行 |
| `status` | enum | ✅ | `active` \| `inbox` \| `archived` |
| `confidence` | float | ✅ | 0–1,分类置信度 |
| `created` / `updated` | ISO8601 带时区 | ✅ | 本地时区 |
| `source.kind` | enum | ✅ | `screenshot` \| `text` \| `url` |
| `source.ref` | str | ✅ | 截图为 assets 相对路径;文本为原文前 100 字;URL 为链接 |

### 2.3 正文结构

录入循环生成的正文**必须**是这个结构,顺序固定:

```markdown
## 一句话
在生成前先从外部知识源检索相关片段，拼进上下文再让模型作答。

## 要点
- 解决的是模型知识过时和幻觉问题
- 检索质量决定上限，模型只负责组织语言
- 与 Function Calling 的区别：RAG 取的是文本，FC 调的是能力

## 关联
- [[Function Calling]] — 演进路径上的下一阶段
- [[向量数据库]] — RAG 的常见实现载体

## 原始输入
![[assets/2026-07-30-142211.png]]
```

- `## 一句话` 内容必须与 frontmatter 的 `summary` 完全一致
- `## 关联` 里的 `[[链接]]` 必须指向 `notes/` 下真实存在的文件名,不存在则不写
- `## 原始输入` 保留溯源,`kind=text` 时用引用块

---

## 3. taxonomy.yaml

```yaml
version: 1
updated: 2026-07-30

categories:
  ai-engineering:
    label: AI 工程
    children:
      agent:      { label: Agent 与编排 }
      rag:        { label: 检索与上下文 }
      prompting:  { label: 提示工程 }

tags:
  - 向量检索
  - 上下文工程
  - 评估与测试
```

约束:

1. **最多两层。** `category` 的值是 `parent/child`,不允许只写 `parent`
2. `tags` 是扁平的封闭词表
3. 任何代码**不得**硬编码分类名或标签名,一律从本文件读
4. 新增分类/标签只能由整理循环提议,写入 `vault/.noteagent/proposals.yaml`,人工确认后才落进 taxonomy.yaml
5. **录入循环不得写入本文件**

---

## 4. SQLite 索引(派生物)

位于 `vault/.noteagent/index.db`。删掉它必须能全量重建:`rebuild_all()` 扫描 `notes/` 和 `inbox/` 两个目录。
搜索结果的 NoteRef 完全从本索引构造,不回读笔记文件。

```sql
CREATE TABLE notes (
  path          TEXT PRIMARY KEY,   -- 相对 vault 根，如 notes/RAG 检索增强生成.md
  title         TEXT NOT NULL,
  category      TEXT,
  tags          TEXT NOT NULL,      -- JSON array
  aliases       TEXT NOT NULL,      -- JSON array
  summary       TEXT NOT NULL,
  status        TEXT NOT NULL,
  confidence    REAL NOT NULL,
  content_hash  TEXT NOT NULL,
  updated       TEXT NOT NULL
);

CREATE TABLE ingest_log (
  input_hash    TEXT PRIMARY KEY,   -- 输入内容 SHA256，用于幂等
  created_at    TEXT NOT NULL,
  result        TEXT NOT NULL       -- JSON: {created: [path], skipped: bool}
);

CREATE VIRTUAL TABLE notes_fts USING fts5(
  path UNINDEXED, title, aliases, summary, body,
  tokenize = 'trigram'
);
```

> **`tokenize='trigram'` 不可改成默认值。** FTS5 默认分词器按空格切词,对中文等于整段当一个 token,搜「检索增强」会一条都搜不到。trigram 做三字滑窗,中文子串检索才能工作。

---

## 5. HTTP API 契约

前端只认这份契约。后端改接口必须同步改这里。

### `POST /api/ingest`

```jsonc
// req
{ "kind": "screenshot", "text": null, "image_b64": "..." }
// resp 202
{ "job_id": "01J8X..." }
```

### `GET /api/jobs/{job_id}`

```jsonc
{
  "status": "done",            // pending | running | done | failed
  "stage": "写入",              // 解析 | 拆分 | 分类 | 写入
  "created": [NoteRef],
  "inbox":   [NoteRef],
  "error":   null
}
```

### `GET /api/search?q=&category=&tag=&include_inbox=false&limit=20`

```jsonc
{ "results": [NoteRef], "matched_category": "ai-engineering/rag" }
```

- `include_inbox` 默认 `false`。为 `true` 时 `status=inbox` 的笔记参与召回,且必须排在所有 active 笔记之后

### `GET /api/notes/{path}` → `{ "frontmatter": {...}, "body": "..." }`

### `GET /api/taxonomy` → taxonomy.yaml 的解析结果

### `GET /api/inbox` → `[NoteRef]`

### `POST /api/reorganize` → `{ "report_id": "..." }` *(阶段 4)*

### NoteRef

```jsonc
{
  "path": "notes/RAG 检索增强生成.md",
  "title": "RAG 检索增强生成",
  "category": "ai-engineering/rag",
  "tags": ["向量检索"],
  "summary": "在生成前先从外部知识源检索相关片段……",
  "updated": "2026-07-30T14:22:11+08:00",
  "confidence": 0.86
}
```

---

## 6. 不变量

违反任何一条即为 bug,不是风格问题。

1. **所有 vault 读写必须经过 `backend/app/vault/`。** 其他任何位置出现 `open()` 或 `Path.write_text()` 操作 vault 都是违规
2. 所有文件 IO 显式 `encoding="utf-8"`,路径一律 `pathlib.Path`
3. `category` 不在 taxonomy 中 → 落 `inbox/`,`status=inbox`,**绝不静默新建分类**
4. `tags` 中不在受控词表的项**直接丢弃**,不新建
5. **录入循环只能创建新文件**,不得修改或删除 `notes/` 下已有文件
6. `maps/` 全部由程序生成,任何手写内容会在下次重建时丢失
7. 每次成功写入以一次 `git commit` 结尾,message 格式:`note: 新建 3 条 (来源 screenshot)`
8. `ingest` 前先算输入 SHA256,命中 `ingest_log` 直接返回已有结果,不重复处理
9. 写笔记文件用「先写临时文件再原子 rename」,不允许原地半写
10. `confidence < 0.6` → 强制 `inbox`,不猜分类
