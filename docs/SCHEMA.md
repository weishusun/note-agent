# SCHEMA — 数据契约

> 本文件是 note-agent 的数据契约,优先级高于任何实现代码。
> 实现与本文件冲突时,改实现,不改本文件。
> 确需改本文件时,必须同一次 commit 内同步修改 `backend/app/vault/models.py`。

**版本:v3**
v2 → v3 变更:新增 `id` 字段(§2.2)、`title` 不再要求与文件名一致(§2.2)、`category` 为 null 时 `confidence` 统一写 0.0(§2.2)、明确 inbox 的两种成因(§2.2)、`notes` 表加 `id` 列且 `ingest_log.result` 改存 id(§4)、`matched_category` 定义为查询理解(§5)、`POST /api/ingest` 加 `force` 与 `GET /api/jobs/{job_id}` 加 `duplicate`(§5)、`NoteRef` 加 `id`(§5)、重写不变量 1(index.db 豁免范围)与不变量 8(幂等语义)、补充不变量 6 与 10、新增不变量 13(vault 非独占)。

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
id: 01JQ8ZK3M7F2VYB4N6R9T0XC5E
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
| `id` | str \| null | ✅ | 26 字符 ULID,创建时生成,**永不变更**。见下方「`id` 的语义」 |
| `title` | str | ✅ | 1–60 字。文件名由它派生,但不要求逐字相同,见下方「`title` 与文件名的关系」 |
| `category` | str \| null | ✅ | 必须是 taxonomy 中存在的**叶子**路径;`null` 仅当 `status=inbox` |
| `suggested_domain` | str \| null | ✅ | **`category` 为 null 时必填**,否则必须为 null。自由文本,2–8 字,不受任何词表约束 |
| `tags` | list[str] | ✅ | 必须是 taxonomy `tags` 的子集,可为空数组,最多 6 个 |
| `aliases` | list[str] | ✅ | 可为空。导航层的一等公民,尽量多填,含英文名和缩写 |
| `summary` | str | ✅ | ≤ 50 字,一句话 |
| `status` | enum | ✅ | `active` \| `inbox` \| `archived`。inbox 有两种成因,见下方「inbox 的两种成因」 |
| `verified` | bool | ✅ | 人是否确认过本条内容。**创建时一律 false,任何来源都一样。**只有人工操作可翻为 true。`source.kind` 记录来源且不可变,两者是独立的两件事,不得互相推导 |
| `confidence` | float | ✅ | 0–1,**分类**置信度(不是内容置信度)。见下方「`confidence` 与未分类」 |
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

**`id` 的语义:** 笔记的稳定标识符。`path` 是**位置**不是**身份**——用户在 Obsidian 里改标题会重命名文件,`path` 随之改变,`id` 不变。任何需要长期指向一条笔记的地方(`ingest_log`、提议证据、外部引用)都用 `id`,不用 `path`。

- **允许为 `null` 的唯一情形**:用户直接在 Obsidian 里手建的笔记。agent 创建的笔记 `id` 必填。
- **系统不得在读取时为无 `id` 的笔记补写 `id`。** 那是对已有笔记的修改,违反不变量 5。
- **`id` 由写入层(`store.create_note`)在创建时生成,模型层不生成默认值。** 模型层给默认值会连读取路径一起触发,等于绕过上一条。

**`title` 与文件名的关系:** 文件名由 `title` 经 §2.1 安全化规则得到,两者**不要求逐字相同**。`title` 在 frontmatter 中原样保留,不受文件名安全化影响。例如 `title: 问: RAG/FC 有何区别?` 对应的文件名是 `问 RAGFC 有何区别.md`。

**`confidence` 与未分类:** `category` 为 `null` 时 `confidence` 统一写 `0.0`,表示「**未做分类**」而非「分类置信度极低」。做 eval 统计时这些 `0.0` **必须排除**,否则会把「没分类」的样本当成「分错了」的样本,把整体指标压低。

**inbox 的两种成因:**

| inbox 成因 | 判据 | 整理循环的处理 |
|---|---|---|
| 装不下 | `category == null`,`suggested_domain` 必填 | 按 `suggested_domain` 聚类,达阈值提议**建新分类** |
| 低置信 | `category != null`,`suggested_domain` 为 `null` | 按 `category` 聚类,某叶子下堆积达阈值提议 **split 或标记该叶子边界模糊** |

`category != null` 且 `status == inbox` 是**合法组合**。低置信度落 inbox 时 `category` **保留、不清空**,`suggested_domain` 保持 `null`(见不变量 10)。
两种成因的信号完全不同:前者说明 taxonomy 缺一块,后者说明 taxonomy 有一块的边界不清楚。混成一种就都读不出来了。

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
- `## 关联` 里的 `[[链接]]` 必须指向真实存在的文件名,不存在则不写。一条可写的链接都没有时,
  该区块保留标题,内容写 `（暂无）`
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
  id                TEXT UNIQUE,        -- 26 字符 ULID，可空：UNIQUE 允许多个 NULL
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
  result        TEXT NOT NULL       -- JSON: {created: [id], skipped: bool}
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
  "filename": "讲义.pdf",
  "force": false              // true 则跳过幂等检查，强制重新处理（见不变量 8）
}
```
→ `202 { "job_id": "01J8X..." }`

`kind=generated` 时只需 `text`(用户原话)。

第一版只接受 `screenshot`(必填 `image_b64`,可带 `data:image/png;base64,` 前缀)和
`text`(必填 `text`,去首尾空白后 < 2000 字)。其余 kind 与不合法的请求体返回 `400`。

### `GET /api/jobs/{job_id}`

```jsonc
{
  "status": "done",            // pending | running | done | failed
  "stage": "写入",              // 解析 | 拆分 | 分类 | 写入
  "created": [NoteRef],
  "inbox":   [NoteRef],
  "duplicate": false,          // true 表示命中 ingest_log，created/inbox 是已有结果
  "error":   null
}
```

`created` 与 `inbox` **互不重叠**:本次新建的笔记里 `status=inbox` 的进 `inbox`,其余进 `created`。
`job_id` 不存在返回 `404`。任务状态只存在进程内存里,服务重启后查不到。

`duplicate: true` 时**不是错误**:输入此前处理过,返回的是当时的结果。要重新处理就带
`force: true` 重发(见不变量 8)。

### `GET /api/search?q=&category=&tag=&include_inbox=false&limit=20`

```jsonc
{ "results": [NoteRef], "matched_category": null }
```

`include_inbox` 默认 `false`。为 `true` 时 `status=inbox` 的笔记参与召回,且必须排在所有 `active` 笔记之后。

**`matched_category` 是查询理解,不是查询参数回显。** 定义:把 `q` 与 taxonomy 的叶子路径、
叶子名(路径的后半段,如 `rag`)、叶子 `label`、一级分类名及其 `label` 做**大小写不敏感精确匹配**
(与不变量 12 同一套规则),命中则填该分类路径,否则 `null`。叶子优先于一级分类。

- 它**不参与 `results` 的排序**,是与 results 并列的第四路结果。
- 用途:供前端提示「打开这个分类的 MOC」。用户搜「RAG」时,除了命中的笔记,还应该能一步
  跳到 `ai-engineering/rag` 的索引页。
- 它**不是**把请求里的 `category` 参数原样回显。

### `POST /api/ask`

```jsonc
{ "question": "RAG 和微调有什么区别？", "limit": 5 }   // question 1–500 字；limit 1–10，默认 5
```
→
```jsonc
{
  "answer": "RAG 在生成前先检索…… [1]。微调则…… [2]。",
  "sources": [NoteRef],        // 顺序即引用编号：sources[0] 对应回答里的 [1]
  "grounded": true,            // false 表示没检索到任何笔记，回答完全来自模型自身知识
  "keywords": ["RAG", "微调"]   // 模型为这个问题扩展出的检索词，最多 8 个；扩展失败时为空数组
}
```

- 检索**包含 inbox 笔记**(冷启动时所有笔记都在 inbox),只排除 `archived`。
- 召回方式与 `/api/search` 不同,按优先级:① 问题里**包含**某条笔记的标题 ② 问题里包含它的别名
  ③ 检索词与标题/别名**互相包含**(命中的词越多越靠前) ④ 把问题切成三字片段、连同检索词做全文检索。
- **检索词扩展**:检索前先让文本模型(`CLASSIFY_MODEL`)把问题扩展成若干检索词,弥补字面检索
  「换个说法就搜不到」的缺口。这一步超时(10 秒)或失败时**静默退回**只用原问题检索,不让问答失败。
- 笔记正文从磁盘读取;索引里有但磁盘上已不存在的笔记直接跳过(不变量 13)。
- 模型自身知识的补充必须单独成段并以「以下为模型补充,非知识库内容:」开头——与 §2.3 `## 待核实`
  同一个理由:模型生成的内容不能和你自己记的内容长得一样。
- 模型调用失败返回 `502`。问答模型读环境变量 `ANSWER_MODEL`,未配置时沿用 `CLASSIFY_MODEL`。

### `GET /api/notes?limit=50` → `[NoteRef]`

最近更新的笔记,新的在前。含 inbox,不含 `archived`。`limit` 1–200。

### `GET /api/notes/{path}` → `{ "frontmatter": {...}, "body": "..." }`

### `GET /api/assets/{name}` → 文件内容

`assets/` 下的一个素材(截图)。`name` 只能是文件名,不接受路径;不存在返回 `404`。

### `POST /api/reindex` → `{ "indexed": 12 }`

以磁盘为准全量重建索引(不变量 13)。读不了的笔记文件跳过并记日志,不让重建失败。
服务**启动时自动执行一次**;用户在 Obsidian 里改动笔记后可手动触发。

### `GET /api/settings` / `PUT /api/settings`

模型设置。不属于 vault:保存在 `note-agent/settings.json`(含密钥,不入 git),逐项覆盖 `.env` 的默认值,
保存后立即生效。

```jsonc
// GET 的响应，也是 PUT 的响应
{
  "base_url": "https://open.bigmodel.cn/api/paas/v4/",
  "api_key_set": true,
  "api_key_hint": "abcd",              // 末四位；密钥不足 8 位时为 null
  "extract_model": "…",                 // 必须支持图片
  "classify_model": "…",                // 也用于压缩超长文本和扩展检索词
  "answer_model": "",                   // 空 = 沿用 classify_model
  "structured_output_method": "function_calling",   // function_calling | json_mode，只影响提取
  "disable_thinking": true,             // 分类等小调用关闭思考（智谱专有参数）
  "extract_disable_thinking": false     // 提取也关闭思考：快，但可能漏内容
}
```

- **密钥只进不出。** 任何接口都不返回完整密钥。
- `PUT` 只改请求体里带了的字段;`api_key` 为空或不带表示保持不变。`base_url` 必须以 `http(s)://` 开头。
- 某项存成空字符串表示「没填」,退回 `.env` 的默认值。

### `POST /api/settings/test`

用已保存的设置各发一次真实请求:分类模型一句话,提取模型一张小图。

```jsonc
{ "text": { "ok": true, "detail": "正常", "seconds": 0.8 },
  "vision": { "ok": false, "detail": "…错误原因…", "seconds": 1.2 } }
```

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
  "id": "01JQ8ZK3M7F2VYB4N6R9T0XC5E",   // null 表示 Obsidian 手建、无 id 的笔记
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

1. **所有对 `notes/`、`inbox/`、`taxonomy.yaml`、`maps/`、`assets/`、`.noteagent/proposals.yaml` 的读写必须经过 `backend/app/vault/`。** 其他任何位置出现 `open()` 或 `Path.write_text()` 操作这些路径都是违规。
   `.noteagent/index.db` 是派生物,由 `app/search/` 管理,但其路径必须从 `VaultStore` 取得。
   **注意:豁免的是 `index.db` 这个派生物,不是 `.noteagent/` 这个目录。** `proposals.yaml`
   删了不能重建,它不在豁免范围内
2. 所有文件 IO 显式 `encoding="utf-8"`,路径一律 `pathlib.Path`
3. **分类只能由一批笔记的聚类产生,不能由单条笔记产生。** 录入循环遇到装不下的 → 落 inbox + 填 `suggested_domain`;新建分类一律走 `proposals.yaml` 并经人工确认
4. `tags` 中不在受控词表的项**直接丢弃**,不新建
5. **录入循环只能创建新文件**,不得修改或删除已有笔记
6. `maps/` 全部由程序生成,任何手写内容会在下次重建时丢失。
   **`maps/` 下每个 MOC 文件顶部必须有一行提示:「本文件由程序生成,手动修改会在下次重建时丢失。」**
   ——用户在 Obsidian 里看不到本契约文档,唯一能看到的警告就是文件里这一行
7. 每次成功写入以一次 `git commit` 结尾,message 格式:`note: 新建 3 条 (来源 screenshot)`
8. **幂等。** hash 在 `POST /api/ingest` 的 API 边界计算,输入为 `sha256(kind + "\0" + (text or file_bytes))`。
   **filename 不进 hash**——内容相同即同一次输入。
   命中 `ingest_log` 时**不静默跳过**:返回已有结果并置 `duplicate: true`,由用户决定是否带
   `force: true` 重发;`force` 请求跳过幂等检查。
   命中时须校验 `result` 里的 id 仍存在于索引中,**全部失效则删除该条 `ingest_log` 记录**(笔记已被
   用户在 Obsidian 里删掉,这次应该当新输入处理)。
   理由:`kind=generated` 的输入是用户原话(如「记录量子力学角动量」),用户隔一段时间故意重发是
   正常行为,那时 taxonomy 已经不同,应该产出不同结果,而不是被静默挡回
9. 写笔记文件用「先写临时文件再原子 rename」,不允许原地半写
10. `confidence < 0.6` → 强制 `inbox`。阈值定义在 `app/config.py`,**单一阈值 0.6**,待 eval 校准。
    触发时 `status` 置 `inbox`,`category` **保留不清空**(见 §2.2「inbox 的两种成因」),
    `suggested_domain` 保持 `null`
11. 任何来源的笔记**创建时** `verified` 必须为 `false`。`verified` 只能由人工操作翻为 `true`,录入循环和整理循环都不得写入 `true`
12. 精确匹配档(标题、别名)**大小写不敏感**;FTS 档不受此约束
13. **vault 不是本程序独占的。** 用户会直接在 Obsidian 中编辑、重命名、删除笔记。索引必须能容忍
    外部修改:**SQLite 索引与磁盘不一致时以磁盘为准。** 任何依赖「只有本程序写过 vault」的逻辑都是错的
