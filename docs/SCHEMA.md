# SCHEMA — 数据契约

> 本文件是 note-agent 的数据契约,优先级高于任何实现代码。
> 实现与本文件冲突时,改实现,不改本文件。
> 确需改本文件时,必须同一次 commit 内同步修改 `backend/app/vault/models.py`。

**版本:v2**
v1 → v2 变更:新增 `generated` 来源与 `verified` 字段、新增 `suggested_domain`、新增输入分路策略(§2.4)、分类创建改为聚类驱动(§3.1)、精确匹配改大小写不敏感。

---

## 0. 术语

| 术语 | 含义 |
|---|---|
| note | 一条原子知识点,对应 `vault/notes/` 下的一个 `.md` 文件 |
| category | 分类,取自 `taxonomy.yaml`,**单值**,最多两层 |
| tag | 受控标签,取自 `taxonomy.yaml`,多值 |
| suggested_domain | agent 自由生成的领域猜测,**不进 taxonomy**,仅作聚类线索 |
| MOC | map of content,某分类的索引页,位于 `vault/maps/`,**完全由程序生成** |
| 录入循环 | 处理单条用户输入 → 落盘,全自动,只允许新建笔记 |
| 整理循环 | 周期性重整理,可改分类、提议新分类,输出报告供人审阅 |

---

## 1. Vault 布局

```
vault/                       # 独立 git 仓库
├── taxonomy.yaml            # 分类体系，唯一真相源。允许为空
├── notes/                   # 已分类的原子笔记，扁平
├── inbox/                   # 未分类笔记，扁平，等整理循环处理
├── maps/                    # MOC 索引页，程序生成，可随时删除重建
├── assets/                  # 截图、上传文件等原始素材
└── .noteagent/
    ├── index.db             # SQLite，派生物，可随时删除重建
    └── proposals.yaml       # 待人工确认的分类提议
```

**硬规则:`notes/` 和 `inbox/` 必须扁平。** 分类靠 frontmatter 表达,不靠目录。
理由:整理循环会频繁改分类,改一个 YAML 字段是零风险,移动文件会打断所有 `[[双链]]`。

---

## 2. 笔记文件格式

### 2.1 文件名

- 文件名 = `{title}.md`,title 原样保留中文
- 移除 Windows 非法字符:`\ / : * ? " < > |`,以及首尾空格和结尾的 `.`
- 冲突时追加 ` (2)`、` (3)`
- **文件名一旦创建不可变更。** 改名只能由整理循环执行,且必须在同一次操作内重写全库反链

### 2.2 Frontmatter

```yaml
---
title: 角动量算符的对易关系
category: physics/quantum-mechanics
suggested_domain: null
tags: [算符代数, 待深入]
aliases: [angular momentum commutator, 角动量对易子]
summary: 三个角动量分量互不对易，对易子按循环置换给出第三个分量。
status: active
verified: true
confidence: 0.88
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
| `suggested_domain` | str \| null | ✅ | **`category` 为 null 时必填**,否则必须为 null。自由文本,2–8 字,不受任何词表约束 |
| `tags` | list[str] | ✅ | 必须是 taxonomy `tags` 的子集,可为空数组,最多 6 个 |
| `aliases` | list[str] | ✅ | 可为空。导航层的一等公民,尽量多填,含英文名和缩写 |
| `summary` | str | ✅ | ≤ 50 字,一句话 |
| `status` | enum | ✅ | `active` \| `inbox` \| `archived` |
| `verified` | bool | ✅ | 人是否确认过本条内容。**创建时一律 false,任何来源都一样。**只有人工操作可翻为 true。`source.kind` 记录来源且不可变,两者是独立的两件事,不得互相推导 |
| `confidence` | float | ✅ | 0–1,**分类**置信度(不是内容置信度) |
| `created` / `updated` | ISO8601 带时区 | ✅ | 本地时区 |
| `source.kind` | enum | ✅ | `screenshot` \| `text` \| `url` \| `file` \| `generated` |
| `source.ref` | str | ✅ | 见下表 |

`source.ref` 按 kind 取值:

| kind | ref 内容 |
|---|---|
| `screenshot` | `assets/` 下的相对路径 |
| `text` | 用户原始输入的前 100 字 |
| `url` | 完整链接 |
| `file` | `assets/` 下的相对路径,可加 `#p12` 形式的页码锚点 |
| `generated` | 触发生成的用户原话,如「记录量子力学角动量」 |

### 2.3 正文结构

录入循环生成的正文**必须**是这个结构,顺序固定:

```markdown
## 一句话
三个角动量分量互不对易，对易子按循环置换给出第三个分量。

## 要点
- [Lx, Ly] = iħLz，另外两式按 x→y→z 循环
- 因此三个分量无法同时有确定值
- 但 L² 与任一分量对易，故常用 (L², Lz) 作共同本征态

## 关联
- [[不确定性原理]] — 对易关系的直接推论
- [[球谐函数]] — (L², Lz) 共同本征态的具体形式

## 原始输入
![[assets/2026-07-30-142211.png]]
```

- `## 一句话` 必须与 frontmatter 的 `summary` 完全一致
- `## 关联` 里的 `[[链接]]` 必须指向真实存在的文件名,不存在则不写
- `kind=text` 用引用块;`kind=file` 给出文件链接和位置锚点

**`kind=generated` 的额外规则:**

模型自行产出的内容**必须**单独放进 `## 待核实` 区块,不得与其他区块混排,且 `## 要点` 初始为空:

```markdown
## 要点
（待补充）

## 待核实
> 以下内容由模型生成，尚未核实。确认后请移入「要点」并把 verified 改为 true。

- [Lx, Ly] = iħLz
- L² 与任一分量对易
```

理由:一旦模型生成的内容和你自己记的内容在库里长得一样,整个库的可信度就归零了。物理、数学这类有大量精确表述的主题尤其危险——错一个系数你不会发现,而你查这条笔记时正是在拿它当参考。

### 2.4 输入分路策略

不同输入的信息密度差异极大,**不得用同一套原子化策略**。
一张截图出 3 条笔记合理;一个 50 页 PDF 出 200 条笔记会把库淹掉。

| 输入 | 判定 | 产出 |
|---|---|---|
| 截图 | `kind=screenshot` | 1–5 条原子笔记 |
| 短文本 | `kind=text`,< 2000 字 | 1–5 条原子笔记 |
| 长文本 / md 文件 | `kind=file`,≥ 2000 字 | 先出大纲,每个二级标题一条笔记,**上限 10 条** |
| PDF | `kind=file` | **默认只出 1 条**:全文摘要 + 章节大纲 + 关键概念列表。深挖某章需用户显式指定 |
| 「记录关于 X 的知识」 | `kind=generated` | **1 条骨架笔记**:标题、分类建议、常见子话题清单、相关概念双链 |

判定 `generated` 的依据:输入是**祈使句且不含实质内容**(「帮我记一下 X」「我要记录关于 X」),而不是「输入短」。

PDF 第一版只支持有文本层的。扫描件提取失败时明确报错,**不得静默转 OCR 猜测**。

---

## 3. taxonomy.yaml

```yaml
version: 1
updated: 2026-07-30

categories: {}    # 允许为空。分类从笔记聚类中长出，不预先设计
tags: []
```

约束:

1. **最多两层。** `category` 的值是 `parent/child`,不允许只写 `parent`
2. `tags` 是扁平的封闭词表
3. 任何代码**不得**硬编码分类名或标签名,一律从本文件读
4. **本文件只能由人工确认后写入。** 录入循环和整理循环都不得直接修改它
5. **允许完全为空。** 空 taxonomy 下所有笔记进 inbox,这是正常的冷启动状态,不是错误

### 3.1 分类如何产生

核心规则:**分类由「一批笔记的聚类」产生,不由「单条笔记」产生。**

危险的从来不是自动创建分类,而是写一条笔记时顺手创建一个分类——那必然导致大量只有一篇笔记的空壳分类。若创建依据是「inbox 里已堆积 9 条同领域笔记」,该分类天然有内容支撑。

流程:

1. 录入循环遇到装不下的笔记 → 落 `inbox/`,`category: null`,填 `suggested_domain`
2. `suggested_domain` 是**全系统唯一允许模型自由生成的字段**,它不进 taxonomy,只作聚类线索
3. 整理循环按 `suggested_domain` 聚类,达阈值则写提议进 `.noteagent/proposals.yaml`
4. 人工确认后才写入 `taxonomy.yaml`,并把相关 inbox 笔记重新归档

提议格式:

```yaml
proposals:
  - id: p-2026-07-30-001
    kind: new_top_level          # new_top_level | new_child | merge | split | rename
    proposed: physics
    label: 物理
    evidence_count: 9
    evidence:
      - inbox/角动量算符的对易关系.md
      - inbox/泡利矩阵.md
    suggested_children:
      quantum-mechanics: 6
      classical-mechanics: 3
    reason: inbox 中 9 条笔记的 suggested_domain 聚为此类
    status: pending              # pending | approved | rejected | deferred
```

门槛:

| 类型 | 门槛 |
|---|---|
| 新增二级分类 | 同聚类 ≥ 4 条 |
| 新增一级分类 | 同聚类 ≥ 8 条,且与所有现有一级分类的语义距离超过阈值 |
| 合并 | 两分类语义中心相似度超阈值,或其一 < 2 条且 60 天无新增 |
| 拆分 | 单叶子 > 20 条 |

**每条提议必须附带可点开的证据笔记列表。没有证据的提议不允许生成。**

---

## 4. SQLite 索引(派生物)

位于 `vault/.noteagent/index.db`。删掉它必须能从 `notes/` 和 `inbox/` 全量重建。
`NoteRef` **完全从本索引构造,不回读笔记文件**。

```sql
CREATE TABLE notes (
  path              TEXT PRIMARY KEY,   -- 相对 vault 根
  title             TEXT NOT NULL,
  category          TEXT,
  suggested_domain  TEXT,
  tags              TEXT NOT NULL,      -- JSON array
  aliases           TEXT NOT NULL,      -- JSON array
  summary           TEXT NOT NULL,
  status            TEXT NOT NULL,
  verified          INTEGER NOT NULL,   -- 0/1
  confidence        REAL NOT NULL,
  content_hash      TEXT NOT NULL,
  updated           TEXT NOT NULL
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

`rebuild_all()` 扫描 `notes/` 和 `inbox/` 两个目录。

---

## 5. HTTP API 契约

前端只认这份契约。后端改接口必须同步改这里。

### `POST /api/ingest`

```jsonc
{
  "kind": "file",              // screenshot | text | url | file | generated
  "text": null,
  "image_b64": null,
  "file_b64": "...",
  "filename": "讲义.pdf"
}
```
→ `202 { "job_id": "01J8X..." }`

`kind=generated` 时只需 `text`(用户原话)。

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
{ "results": [NoteRef], "matched_category": null }
```

`include_inbox` 默认 `false`。为 `true` 时 `status=inbox` 的笔记参与召回,且必须排在所有 `active` 笔记之后。

### `GET /api/notes/{path}` → `{ "frontmatter": {...}, "body": "..." }`

### `GET /api/taxonomy` → taxonomy.yaml 的解析结果

### `GET /api/inbox` → `[NoteRef]`

### `GET /api/proposals` → `[Proposal]` *(阶段 4)*

### `POST /api/proposals/{id}` *(阶段 4)*
```jsonc
{ "action": "approve", "rename_to": null }   // approve | reject | defer
```

### `POST /api/reorganize` → `{ "report_id": "..." }` *(阶段 4)*

### NoteRef

```jsonc
{
  "path": "notes/角动量算符的对易关系.md",
  "title": "角动量算符的对易关系",
  "category": "physics/quantum-mechanics",
  "suggested_domain": null,
  "tags": ["算符代数"],
  "summary": "三个角动量分量互不对易……",
  "status": "active",
  "verified": true,
  "updated": "2026-07-30T14:22:11+08:00",
  "confidence": 0.88
}
```

---

## 6. 不变量

违反任何一条即为 bug,不是风格问题。

1. **所有 vault 读写必须经过 `backend/app/vault/`。** 其他任何位置出现 `open()` 或 `Path.write_text()` 操作 vault 都是违规
2. 所有文件 IO 显式 `encoding="utf-8"`,路径一律 `pathlib.Path`
3. **分类只能由一批笔记的聚类产生,不能由单条笔记产生。** 录入循环遇到装不下的 → 落 inbox + 填 `suggested_domain`;新建分类一律走 `proposals.yaml` 并经人工确认
4. `tags` 中不在受控词表的项**直接丢弃**,不新建
5. **录入循环只能创建新文件**,不得修改或删除已有笔记
6. `maps/` 全部由程序生成,任何手写内容会在下次重建时丢失
7. 每次成功写入以一次 `git commit` 结尾,message 格式:`note: 新建 3 条 (来源 screenshot)`
8. `ingest` 前先算输入 SHA256,命中 `ingest_log` 直接返回已有结果,不重复处理
9. 写笔记文件用「先写临时文件再原子 rename」,不允许原地半写
10. `confidence < 0.6` → 强制 `inbox`(阈值待 eval 校准后确定)
11. 任何来源的笔记**创建时** `verified` 必须为 `false`。`verified` 只能由人工操作翻为 `true`,录入循环和整理循环都不得写入 `true`
12. 精确匹配档(标题、别名)**大小写不敏感**;FTS 档不受此约束
