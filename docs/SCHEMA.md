# SCHEMA — 数据契约

> 本文件是 note-agent 的数据契约,优先级高于任何实现代码。
> 实现与本文件冲突时,改实现,不改本文件。
> 确需改本文件时,必须同一次 commit 内同步修改 `backend/app/vault/models.py`。

**版本:v5**
v4 → v5 变更(REQUIREMENTS 第 2 步「笔记质量和编辑」):笔记分**主题笔记**和**概念笔记**两种,frontmatter 新增 `type`(§2.2、§2.3);
正文结构重写,`summary` 只在 frontmatter 里、正文不再有 `## 一句话`(§2.3);允许**模型补充**,只能出现在 `## 模型补充` 区块(§2.3);
新增**疑似重复**:frontmatter 新增 `duplicate_of`,合并与保留(§2.5);新增**编辑、删除、手写新建**,改标题会重命名文件并改写全库链接(§2.6);
`source.kind` 新增 `manual`;索引加 `type`、`duplicate_of` 两列(§4);新增 `POST /api/notes`、`PUT/DELETE /api/notes/{id}`、
`POST /api/notes/{id}/merge`、`POST /api/notes/{id}/keep`、`GET /api/refs/{id}`(§5);重写不变量 5,新增不变量 14。

v3 → v4 变更(需求基准见 [REQUIREMENTS.md](REQUIREMENTS.md),本版只覆盖其第 1 步「自动分类和目录树」):
分类改为**模型自动产生并写入**、可手动修改且手动改动被锁定(§3 整节重写);`category` 直接用中文名 `大块/小块`,
不再有 key 与 label 之分(§3);frontmatter 删除 `suggested_domain` 与 `verified`、新增 `category_locked`(§2.2);
新笔记一律写入 `notes/`,`status` 只写在 frontmatter、文件不再随状态移动(§1);删除分类提议与审批流程
(`proposals.yaml`、`/api/proposals`、`/api/reorganize`);新增 `GET /api/tree`、`PUT /api/notes/{id}/category`、
`/api/categories`、`POST /api/reclassify`(§5);重写不变量 3、5、10,删除不变量 11。

v2 → v3 变更:新增 `id` 字段(§2.2)、`title` 不再要求与文件名一致(§2.2)、`category` 为 null 时 `confidence` 统一写 0.0(§2.2)、明确 inbox 的两种成因(§2.2)、`notes` 表加 `id` 列且 `ingest_log.result` 改存 id(§4)、`matched_category` 定义为查询理解(§5)、`POST /api/ingest` 加 `force` 与 `GET /api/jobs/{job_id}` 加 `duplicate`(§5)、`NoteRef` 加 `id`(§5)、重写不变量 1(index.db 豁免范围)与不变量 8(幂等语义)、补充不变量 6 与 10、新增不变量 13(vault 非独占)。

v1 → v2 变更:新增 `generated` 来源与 `verified` 字段、新增 `suggested_domain`、新增输入分路策略(§2.4)、分类创建改为聚类驱动(§3.1)、精确匹配改大小写不敏感。

---

## 0. 术语

| 术语 | 含义 |
|---|---|
| note | 一条笔记,对应 `vault/notes/` 下的一个 `.md` 文件。分主题笔记和概念笔记两种 |
| 主题笔记 | `type=topic`。一份输入(一张图、一份文档)对应一条,**保留原来的整体结构**:流程、表格、层次 |
| 概念笔记 | `type=concept`。一个值得独立成条的概念一条,链接回它出自的主题笔记 |
| 模型补充 | 模型用自身知识写的、输入里没有的内容。只能出现在笔记的 `## 模型补充` 区块里 |
| 疑似重复 | 新录入的概念笔记与已有笔记同名或同别名。新笔记照常保存并打上标记,等使用者决定合并还是保留 |
| category | 分类,**固定两层**:`大块/小块`,如 `人工智能/模型训练`。单值,登记在 `taxonomy.yaml` |
| 大块 / 小块 | 分类的第一层 / 第二层。大块是学科或领域,小块是该领域下的一个主题 |
| 锁定 | 使用者手动改过的分类或归属。模型此后不得覆盖,见 §3.3 |
| 待整理 | `status=inbox` 的笔记:模型拿不准该归到哪,等使用者确认或改正 |
| tag | 受控标签,取自 `taxonomy.yaml`,多值。v4 暂不由模型填写 |
| 录入 | 处理一份输入 → 生成笔记 → 自动归类 → 落盘 |
| 重新归类 | 让模型对已有的、未锁定的笔记重新分类,见 §3.4 |

---

## 1. Vault 布局

```
vault/                       # 独立 git 仓库
├── taxonomy.yaml            # 分类树，程序自动维护，使用者可通过应用修改
├── notes/                   # 全部笔记，扁平
├── inbox/                   # 旧目录：v3 及以前的未分类笔记留在这里，v4 起不再写入
├── maps/                    # 暂不使用（v4 的目录树由应用实时生成，不落文件）
├── assets/                  # 截图、上传文件等原始素材
└── .noteagent/
    └── index.db             # SQLite，派生物，可随时删除重建
```

**硬规则:`notes/` 必须扁平,文件创建后不移动。** 分类和状态都只写在 frontmatter 里,不靠目录表达。
理由:分类会被频繁调整,改一个 YAML 字段是零风险,移动文件会打断所有 `[[双链]]`。
因此「待整理」的笔记也在 `notes/` 里,被归类时文件原地不动;`inbox/` 里的旧笔记同理,留在原处照常使用。

---

## 2. 笔记文件格式

### 2.1 文件名

- 文件名 = `{title}.md`,title 原样保留中文
- 移除 Windows 非法字符:`\ / : * ? " < > |`,以及首尾空格和结尾的 `.`
- 冲突时追加 ` (2)`、` (3)`。**在 `notes/` 与 `inbox/` 两个目录范围内唯一**:双链只写文件名、不带目录
- **文件名跟着标题走。** 使用者改标题时,文件按新标题重命名(同样的安全化和避重规则),
  并在**同一次操作内**把全库正文里指向旧文件名的 `[[双链]]` 改写成新文件名(§2.6)。`id` 不变

### 2.2 Frontmatter

```yaml
---
id: 01JQ8ZK3M7F2VYB4N6R9T0XC5E
type: concept
title: 角动量算符的对易关系
category: 物理/量子力学
category_locked: false
tags: []
aliases: [angular momentum commutator, 角动量对易子]
summary: 三个角动量分量互不对易，对易子按循环置换给出第三个分量。
status: active
confidence: 0.88
duplicate_of: null
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
| `type` | enum | ✅ | `topic` \| `concept`,见 §2.3。**v4 及以前的笔记没有这个键,读取时按 `concept` 处理** |
| `title` | str | ✅ | 1–60 字。文件名由它派生,但不要求逐字相同,见下方「`title` 与文件名的关系」 |
| `category` | str \| null | ✅ | `大块/小块`,两段都必须有,见 §3.1 的命名规则。`null` 仅当 `status=inbox`(分类调用失败时) |
| `category_locked` | bool | ✅ | 使用者是否手动指定过本条的分类。为 `true` 时重新归类必须跳过它,见 §3.3。创建时一律 `false` |
| `tags` | list[str] | ✅ | 必须是 taxonomy `tags` 的子集,可为空数组,最多 6 个。v4 录入时一律写 `[]` |
| `aliases` | list[str] | ✅ | 可为空。导航层的一等公民,尽量多填,含英文名和缩写 |
| `summary` | str | ✅ | ≤ 80 字,一句话。**只存在这里**,正文不再重复(§2.3) |
| `status` | enum | ✅ | `active` \| `inbox` \| `archived`。`inbox` 即「待整理」,见下方 |
| `confidence` | float | ✅ | 0–1,**分类**置信度(不是内容置信度)。使用者手动指定分类后写 `1.0`;`category` 为 null 时写 `0.0` |
| `duplicate_of` | str \| null | ✅ | 疑似与之重复的那条已有笔记的 `id`,见 §2.5。没有疑似重复时为 `null`。旧笔记没有这个键,按 `null` 处理 |
| `created` / `updated` | ISO8601 带时区 | ✅ | 本地时区。`updated` 只在**内容**被改动时更新(编辑、合并);改分类不算 |
| `source.kind` | enum | ✅ | `screenshot` \| `text` \| `url` \| `file` \| `generated` \| `manual` |
| `source.ref` | str | ✅ | 见下表 |

`source.ref` 按 kind 取值:

| kind | ref 内容 |
|---|---|
| `screenshot` | `assets/` 下的相对路径 |
| `text` | 用户原始输入的前 100 字 |
| `url` | 完整链接 |
| `file` | `assets/` 下的相对路径,可加 `#p12` 形式的页码锚点 |
| `generated` | 触发生成的用户原话,如「记录量子力学角动量」 |
| `manual` | 固定写 `手写` |

**`id` 的语义:** 笔记的稳定标识符。`path` 是**位置**不是**身份**——用户在 Obsidian 里改标题会重命名文件,`path` 随之改变,`id` 不变。任何需要长期指向一条笔记的地方(`ingest_log`、提议证据、外部引用)都用 `id`,不用 `path`。

- **允许为 `null` 的唯一情形**:用户直接在 Obsidian 里手建的笔记。agent 创建的笔记 `id` 必填。
- **系统不得在读取时为无 `id` 的笔记补写 `id`。** 读取不应该有副作用。
- **`id` 由写入层(`store.create_note`)在创建时生成,模型层不生成默认值。** 模型层给默认值会连读取路径一起触发,等于绕过上一条。

**`title` 与文件名的关系:** 文件名由 `title` 经 §2.1 安全化规则得到,两者**不要求逐字相同**。`title` 在 frontmatter 中原样保留,不受文件名安全化影响。例如 `title: 问: RAG/FC 有何区别?` 对应的文件名是 `问 RAGFC 有何区别.md`。

**待整理(`status=inbox`):** 只有两种情况会进待整理,都应该是少数:

| 成因 | 判据 | 界面上怎么处理 |
|---|---|---|
| 模型拿不准 | `category` 有值,`confidence < 0.6` | 显示模型猜的分类,使用者点一下确认,或改到别处 |
| 分类调用失败 | `category == null`,`confidence == 0.0` | 使用者手动指定,或稍后「重新归类」 |

做统计时 `confidence == 0.0` 的是「没分上类」,不是「分错了」,必须排除。

**旧字段的兼容:** v3 及以前的笔记里有 `suggested_domain` 和 `verified` 两个键。**读取时忽略、不报错**;
该笔记下一次被程序改写(比如重新归类)时这两个键随之消失。不做一次性的批量迁移。

### 2.3 两种笔记与正文结构

**为什么分两种:** 一张流程图被拆成「RLHF:输入为自生成数据,输出到 llm 阶段」这类碎片之后,流程本身反而没有
任何一条笔记记下来,每条碎片单独看也没有意义。所以整体结构交给主题笔记,单个概念交给概念笔记。

**一份输入产出什么**(截图和短文本):

| 输入的内容 | 产出 |
|---|---|
| 只讲一个概念 | 1 条概念笔记,没有主题笔记 |
| 其他情况(流程、表格、对比、多个概念) | **1 条主题笔记** + 0–8 条概念笔记 |

概念笔记只为**值得以后单独查到**的概念建立。流程图里的一个普通节点、表格里的一个普通取值,不必各建一条。

**主题笔记(`type=topic`)的正文**,区块顺序固定:

```markdown
## 内容
1. **多层 Transformer**(随机初始化)
2. **文字接龙训练** ← 非结构化文本数据
3. **SFT** ← 高质量人工标注数据
4. **RLHF** ⇄ 自生成数据
5. **LLM** → 蒸馏模型 / 量化模型;另可做 LoRA 微调

## 概念
- [[文字接龙训练]]
- [[SFT]]
- [[RLHF]]

## 模型补充
> 以下内容由模型根据自身知识补充,不是来自你的资料。

这是大模型从随机初始化到可用的典型训练流程……

## 原始输入
![[assets/2026-10-11-191241.png]]
```

- `## 内容` **保留原来的结构**:表格写成 Markdown 表格,流程写成带箭头的有序列表,层次写成嵌套列表。
  只转写输入里有的东西。
- `## 概念` 列出本次一起产出的概念笔记。一条都没有时整个区块省略。

**概念笔记(`type=concept`)的正文**:

```markdown
## 要点
- 训练流程中 SFT 之后的阶段
- 与自生成数据双向交互

## 模型补充
> 以下内容由模型根据自身知识补充,不是来自你的资料。

- RLHF 即基于人类反馈的强化学习:用人对回答的偏好训练奖励信号,再据此优化模型

## 关联
- [[大模型的训练流程]]
- [[SFT]]
```

- `## 要点` 1–6 条,每条 ≤ 40 字,只写输入里有的。
- 标题不带括号:模型写出「对齐 (RLHF/DPO)」这样的标题时,程序把括号里的内容拆进 `aliases`。与标题相同的别名去掉。
- `## 关联` 第一条是它出自的主题笔记(如果有),其后是相关的其他概念。
- 没有主题笔记的概念笔记(输入只讲一个概念),在末尾带 `## 原始输入`;有主题笔记的不带,原始输入只放在主题笔记里。

**两种笔记共同的规则:**

- **`summary` 只在 frontmatter 里,正文不再有 `## 一句话`。** 界面在标题下显示 `summary`。
  (v4 及以前的笔记正文里还留着 `## 一句话`,不处理,照常显示。)
- **模型补充(不变量 14):** 模型可以用自身知识补充输入里没有、但有助于理解的内容——典型情况是图里只出现了一个术语、
  没有任何解释。补充**只能**写在 `## 模型补充` 区块里,区块第一行固定是上面那句引用;`summary`、`## 内容`、`## 要点`
  里**不得**混入。没什么可补充时整个区块省略。概念笔记的补充最多 4 条,主题笔记的补充最多 300 字。
- **字数上限由程序兜底,不让录入失败:**模型写超了的概要、要点、补充先交给文本模型压缩,压不下来再截断(以「…」结尾);
  主题笔记的 `## 内容` 最长 4000 字,超出只截断并注明。条数超出的取前几条。
- 所有 `[[双链]]` 必须指向真实存在的文件名,不存在则不写。`## 关联` 一条可写的都没有时写 `（暂无）`。
- 原始输入:`kind=screenshot` 用 `![[assets/…]]`,`kind=text` 用引用块。
- 上面的结构只约束**录入时生成**的正文。使用者手写或编辑过的笔记可以是任意 Markdown。

### 2.4 输入分路策略

不同输入的信息密度差异极大,**不得用同一套策略**。

| 输入 | 判定 | 产出 |
|---|---|---|
| 截图 | `kind=screenshot` | 见 §2.3:0–1 条主题笔记 + 0–8 条概念笔记 |
| 短文本 | `kind=text`,< 2000 字 | 同上 |
| 长文本 / md 文件 | `kind=file`,≥ 2000 字 | *(第 3 步)* |
| PDF | `kind=file` | *(第 3 步)* 摘要级:1 条主题笔记,含摘要、大纲、关键概念 |
| 网页链接 | `kind=url` | *(第 3 步)* 同 PDF |

### 2.5 疑似重复

长期使用一定会重复录入同一个概念。**不自动合并,也不打断录入**:

1. **判定(只对概念笔记):** 新概念笔记的**标题**,与某条已有概念笔记的标题或任一别名**大小写不敏感地相同**。
   只做字面比对,不做语义判断——名字完全不同但讲同一件事的,检测不到。
   **只拿新笔记的标题去比,不拿它的别名去比:**真实数据里「对齐」的别名是 RLHF、DPO,它和已有的「RLHF」是两个概念,
   用别名比对会把它们误判成重复。同一次录入产出的笔记之间不互相比。
2. **命中时:** 新笔记照常保存、照常归类,只是 `duplicate_of` 写上那条已有笔记的 `id`。命中多条时取最早创建的那条。
3. **使用者决定**(在界面的「疑似重复」里):
   - **合并:** 把新笔记并进已有的那条(见 §2.6「合并」),新笔记删除。
   - **保留:** 两条都留着,新笔记的 `duplicate_of` 清空。
4. 被指向的那条笔记如果后来被删了,指向它的 `duplicate_of` 一并清空。

主题笔记不做重复判定:同一份输入重复提交由 `ingest_log` 拦住(不变量 8)。

### 2.6 编辑、删除、合并、手写

这些都是使用者在界面上主动做的操作,每次成功后一次 git 提交。它们会改写已有笔记,不受「录入只新建」的限制(不变量 5)。

| 操作 | 效果 |
|---|---|
| **编辑** | 可改 `title`、`summary`、`aliases`、正文(任意 Markdown)。`updated` 更新为当前时间。分类另走 `PUT /api/notes/{id}/category` |
| **改标题** | 编辑的一种。文件按新标题重命名(§2.1),并把全库正文里的 `[[旧文件名]]`、`[[旧文件名\|显示名]]` 改成新文件名。`id` 不变 |
| **删除** | 删掉文件。其他笔记正文里指向它的 `[[双链]]` 改成纯文字(去掉方括号);指向它的 `duplicate_of` 清空。素材文件不删 |
| **合并** A ← B | 把 B 的内容并进 A:要点、模型补充、别名、关联取并集并去重(由文本模型整理,失败时退回简单拼接),A 的 `updated` 更新;随后按「删除」处理 B,但指向 B 的双链改成指向 A。A 的 `id`、文件名、分类、锁定状态都不变 |
| **手写新建** | 使用者给出 `title`、`summary`、正文,可选别名和分类。`type=concept`,`source.kind=manual`。给了分类就按手动指定处理(锁定);没给就自动归类 |

删除和合并在应用内**没有撤销**。vault 是 git 仓库,每次操作都有一次提交,需要时可以用 git 找回。

---

## 3. 分类

### 3.1 taxonomy.yaml

```yaml
version: 2
updated: 2026-10-11

categories:
  人工智能:                  # 大块
    locked: false
    children:
      模型训练: { locked: true }     # 小块；使用者改过名，已锁定
      模型结构: { locked: false }
  算法:
    locked: true
    children:
      动态规划: { locked: false }

tags: []
```

- **固定两层。** 笔记的 `category` 是 `大块/小块`,不允许只写大块,也不允许第三层。
- **名字就是标识。** 不再有英文 key 加中文 label 的区分,文件里写的就是界面上显示的。
  代价是改名要改写该分类下所有笔记的 frontmatter(见 §3.3),这个代价可以接受:移动和合并本来就要改写。
- **命名规则**(大块、小块相同):去首尾空白后 1–12 个字,不含 `/`。同一层内**大小写不敏感地唯一**。
- 任何代码**不得**硬编码分类名或标签名。
- **允许完全为空。** 空分类树是新知识库的正常起点,录入第一条笔记时就会长出第一个大块和小块。
- 本文件由程序维护。对它的读写必须经过 `app/vault/taxonomy.py`,写入用原子替换。

### 3.2 录入时自动归类

**一份输入产出的所有笔记,在一次模型调用里一起归类。** 调用时给模型看当前整棵分类树(大块、小块、各自的笔记数),
模型对每条笔记给出 `大块`、`小块`、`置信度`:

1. 已有的小块合适 → 用它。
2. 大块合适但没有合适的小块 → 在该大块下**新建小块**。
3. 没有合适的大块 → **新建大块和小块**。

程序拿到结果后:

- 按 §3.1 的命名规则校验;与已有分类大小写不敏感相同的,**沿用已有的写法**(模型写 `Rag` 而库里是 `RAG`,用 `RAG`)。
- 缺的大块、小块立即写进 `taxonomy.yaml`,`locked: false`。**不需要使用者确认。**
- `置信度 < 0.6` → 笔记 `status=inbox`,`category` 保留模型的猜测。阈值定义在 `app/config.py`。
- 名字不合法,或整次分类调用失败 → 该笔记 `category=null`、`status=inbox`、`confidence=0.0`。
  **分类失败不得让录入失败**:笔记照常保存,只是进待整理。

给模型的约束(写在提示词里):优先复用已有分类;不造近义分类(已有「人工智能」就不要再建「AI」);
大块是学科或领域这一级,小块是领域下的主题、不能具体到这条笔记自己的概念;大块总数要克制。

### 3.3 手动修改与锁定

使用者在应用里可以:把一条笔记移到别的分类、确认一条待整理笔记、新建分类、给分类改名、
把一个分类并入另一个(改成同层已有的名字即合并)、删除空分类。

**锁定规则——手动改过的地方,模型此后不得覆盖:**

| 使用者的操作 | 被锁定的东西 | 锁定后模型不能做什么 |
|---|---|---|
| 移动一条笔记 / 确认一条待整理笔记 | 该笔记:`category_locked: true`,同时 `status=active`、`confidence=1.0` | 重新归类时改它的分类 |
| 新建、改名、合并分类 | 该分类:`locked: true` | 重新归类时给它改名、把它并掉、把它删掉 |

锁定**不妨碍**模型把新笔记归进一个锁定的分类,也不妨碍使用者自己再改。

- **改名与合并**会改写该分类下所有笔记的 `category`(不改 `category_locked`)。先改笔记、再改 `taxonomy.yaml`、
  再更新索引,最后一次 git 提交。中途失败时重做同一个操作必须能把状态补齐(操作要幂等)。
- **空分类的清理:**未锁定的分类一旦没有笔记就自动从 `taxonomy.yaml` 删掉;锁定的空分类保留,直到使用者自己删。
  删除只允许对空分类执行。

### 3.4 重新归类

使用者主动触发,后台执行,有进度。两种范围:

| 范围 | 处理哪些笔记 | 用途 |
|---|---|---|
| `unsorted` | `category` 为 null 的,和 `status=inbox` 的 | 把待整理的清掉;旧版本留下的未分类笔记迁移进来 |
| `all` | 所有 `category_locked=false` 的 | 分类树长歪了,让模型整体重排 |

- 每次调用处理一批(20 条左右),批与批之间分类树是累积的,后面的批能看到前面新建的分类。
- `all` 范围开始时,分类树只保留**锁定的分类**和**含有锁定笔记的分类**,其余由模型重新长出来;结束后按 §3.3 清理空分类。
- 被处理的笔记按 §3.2 的规则落定:够置信的 `status=active`,不够的留在待整理。
- 整个过程结束后一次 git 提交。中途失败时已经改完的笔记保持已改状态,不回滚。

---

## 4. SQLite 索引(派生物)

位于 `vault/.noteagent/index.db`。删掉它必须能从 `notes/` 和 `inbox/` 全量重建。
`NoteRef` **完全从本索引构造,不回读笔记文件**。

```sql
CREATE TABLE notes (
  path              TEXT PRIMARY KEY,   -- 相对 vault 根
  id                TEXT UNIQUE,        -- 26 字符 ULID，可空：UNIQUE 允许多个 NULL
  type              TEXT NOT NULL,      -- topic | concept
  title             TEXT NOT NULL,
  category          TEXT,               -- 「大块/小块」，可空
  category_locked   INTEGER NOT NULL,   -- 0/1
  tags              TEXT NOT NULL,      -- JSON array
  aliases           TEXT NOT NULL,      -- JSON array
  summary           TEXT NOT NULL,
  status            TEXT NOT NULL,
  confidence        REAL NOT NULL,
  duplicate_of      TEXT,               -- 疑似与之重复的笔记 id，可空
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

**表结构版本:**用 `PRAGMA user_version` 记录(与本文件的版本号一致,v5 = 5)。打开时版本不符就丢弃并重建 `notes` 与 `notes_fts` 两张表,
随后由启动时的全量重建补回数据。`ingest_log` **不随之丢弃**——它不是从笔记文件派生的,丢了就失去重复录入的检测。

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

重新归类(`POST /api/reclassify`)的任务也用这个接口查,此时 `stage` 为 `归类`,并多一个字段
`"progress": { "done": 40, "total": 132 }`;`created` 与 `inbox` 为空数组。录入任务的 `progress` 为 `null`。

`duplicate: true` 时**不是错误**:输入此前处理过,返回的是当时的结果。要重新处理就带
`force: true` 重发(见不变量 8)。

### `GET /api/search?q=&category=&tag=&include_inbox=false&limit=20`

```jsonc
{ "results": [NoteRef], "matched_category": null }
```

`include_inbox` 默认 `false`。为 `true` 时 `status=inbox` 的笔记参与召回,且必须排在所有 `active` 笔记之后。

**`matched_category` 是查询理解,不是查询参数回显。** 定义:把 `q` 与完整路径(`大块/小块`)、
小块名、大块名做**大小写不敏感精确匹配**(与不变量 12 同一套规则),命中则填该分类路径
(命中大块时填大块名),否则 `null`。小块优先于大块。

- 它**不参与 `results` 的排序**,是与 results 并列的第四路结果。
- 用途:供前端提示「打开这个分类」。用户搜「RAG」时,除了命中的笔记,还应该能一步
  跳到 `人工智能/RAG` 在目录树里的位置。
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

- 检索**包含待整理的笔记**,只排除 `archived`。
- 召回方式与 `/api/search` 不同,按优先级:① 问题里**包含**某条笔记的标题 ② 问题里包含它的别名
  ③ 检索词与标题/别名**互相包含**(命中的词越多越靠前) ④ 把问题切成三字片段、连同检索词做全文检索。
- **检索词扩展**:检索前先让文本模型(`CLASSIFY_MODEL`)把问题扩展成若干检索词,弥补字面检索
  「换个说法就搜不到」的缺口。这一步超时(10 秒)或失败时**静默退回**只用原问题检索,不让问答失败。
- 笔记正文从磁盘读取;索引里有但磁盘上已不存在的笔记直接跳过(不变量 13)。
- 模型自身知识的补充必须单独成段并以「以下为模型补充,非知识库内容:」开头——与 §2.3 `## 待核实`
  同一个理由:模型生成的内容不能和你自己记的内容长得一样。
- 模型调用失败返回 `502`。问答模型读环境变量 `ANSWER_MODEL`,未配置时沿用 `CLASSIFY_MODEL`。

### `GET /api/notes?category=&status=&duplicates=&limit=50` → `[NoteRef]`

最近更新的笔记,新的在前,不含 `archived`。`limit` 1–200。

- `category`:`大块/小块` 只列该小块;只给 `大块` 则列它下面所有小块的笔记。只列 `status=active` 的。
- `status=inbox`:只列待整理的。与 `category` 不同时使用。
- `duplicates=true`:只列 `duplicate_of` 非空的,即等使用者决定合并还是保留的。
- 都不给:全部笔记,含待整理。

### `GET /api/tree`

目录树,给界面左侧用。

```jsonc
{
  "total": 37,                 // 全部笔记数，不含 archived
  "inbox": 2,                  // 待整理的笔记数
  "duplicates": 1,             // 疑似重复、等使用者决定的笔记数
  "categories": [
    { "name": "人工智能", "locked": false, "count": 21,
      "children": [
        { "name": "模型训练", "path": "人工智能/模型训练", "locked": true, "count": 9 }
      ] }
  ]
}
```

- `count` 只数 `status=active` 的笔记;大块的 `count` 是它所有小块之和。待整理的笔记只计入 `inbox`,不计入任何分类。
- 顺序:大块、小块都按 `count` 从多到少,同数按名字。
- 笔记上写着、但 `taxonomy.yaml` 里没有的分类也要出现在树里(`locked: false`)——不能因为两边不一致就让笔记在界面上消失。

### `PUT /api/notes/{id}/category`

```jsonc
{ "category": "人工智能/模型训练" }
```
→ 改完后的 `NoteRef`。

把一条笔记移到指定分类,或确认一条待整理的笔记(传它当前的分类即可)。效果见 §3.3:该笔记被锁定、
转为 `active`、`confidence=1.0`。目标分类不存在时自动创建,且因为是使用者建的,`locked: true`。
`id` 不存在返回 `404`;分类名不合 §3.1 返回 `400`。

### `POST /api/categories` / `PUT /api/categories` / `DELETE /api/categories`

分类名含中文和 `/`,所以路径一律放在请求体或查询参数里,不放在 URL 路径里。三个接口都返回最新的目录树(同 `GET /api/tree`)。

```jsonc
// POST：新建。path 可以是「大块」或「大块/小块」，后者在大块不存在时一并创建
{ "path": "人工智能/强化学习" }

// PUT：改名。name 只是这一层的新名字，不是完整路径
// 改成同层已有的名字 = 把这个分类并进那一个
{ "path": "人工智能/模型训练", "name": "训练流程" }

// DELETE /api/categories?path=人工智能/强化学习
```

- 新建、改名、合并得到的分类都 `locked: true`(§3.3)。
- 删除只允许对没有笔记的分类执行,否则 `409`;删大块要求它下面所有小块都没有笔记。
- 名字不合 §3.1 返回 `400`;`path` 不存在返回 `404`;新建已存在的分类返回 `409`。

### `POST /api/reclassify`

```jsonc
{ "scope": "unsorted" }      // unsorted | all，见 §3.4
```
→ `202 { "job_id": "01J8X..." }`,进度用 `GET /api/jobs/{job_id}` 查。同一时间只允许一个重新归类任务,已有一个在跑时返回 `409`。

### `GET /api/notes/{path}` → `{ "frontmatter": {...}, "body": "..." }`

### `POST /api/notes`

手写新建一条笔记(§2.6)。

```jsonc
{
  "title": "LoRA",
  "summary": "只训练低秩矩阵的参数高效微调方法。",
  "body": "## 要点\n- 冻结预训练权重\n",
  "aliases": ["Low-Rank Adaptation"],     // 可选
  "category": "人工智能/模型微调"            // 可选。不给则自动归类
}
```
→ `201` 新笔记的 `NoteRef`。字段不合 §2.2 的约束返回 `400`。

### `PUT /api/notes/{id}`

```jsonc
{ "title": "…", "summary": "…", "aliases": ["…"], "body": "…" }   // 只带要改的字段
```
→ 改完后的 `NoteRef`(改了标题时 `path` 会变)。`id` 不存在返回 `404`;字段不合约束返回 `400`。

### `DELETE /api/notes/{id}` → `{ "deleted": true }`

### `POST /api/notes/{id}/merge`

```jsonc
{ "into": "01JQ8ZK3M7F2VYB4N6R9T0XC5E" }    // 把 {id} 这条并进 into 那条
```
→ 合并后的那条(`into`)的 `NoteRef`。任一 `id` 不存在返回 `404`;两个 `id` 相同返回 `400`。
合并要调用文本模型整理内容;模型调用失败时退回简单拼接,**不让合并失败**。

### `POST /api/notes/{id}/keep` → `NoteRef`

对一条疑似重复的笔记选择「保留」:清空它的 `duplicate_of`。

### `GET /api/refs/{id}` → `NoteRef`

按 `id` 取一条笔记的摘要信息(界面要显示「疑似与《X》重复」里的 X)。不存在返回 `404`。

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
  "disable_thinking": true,             // 分类等小调用关闭思考（智谱专有参数）
  "extract_disable_thinking": true      // 看图提取是否关闭思考，默认关：开着一张图要 100 秒以上
}
```

- **密钥只进不出。** 任何接口都不返回完整密钥。
- `PUT` 只改请求体里带了的字段;`api_key` 为空或不带表示保持不变。`base_url` 必须以 `http(s)://` 开头。
- 某项存成空字符串表示「没填」,退回 `.env` 的默认值。
- **所有结构化输出一律用 JSON 模式**,输出结构写在提示词里;不用函数调用——实测智谱的函数调用处理不了嵌套结构,
  关闭思考后更是直接输出坏的内容。所以没有「结构化输出方式」这个设置。

### `POST /api/settings/test`

用已保存的设置各发一次真实请求:分类模型一句话,提取模型一张小图。

```jsonc
{ "text": { "ok": true, "detail": "正常", "seconds": 0.8 },
  "vision": { "ok": false, "detail": "…错误原因…", "seconds": 1.2 } }
```

### `GET /api/taxonomy` → taxonomy.yaml 的解析结果

### `GET /api/inbox` → `[NoteRef]`

等同于 `GET /api/notes?status=inbox`,保留是为了兼容。

### NoteRef

```jsonc
{
  "id": "01JQ8ZK3M7F2VYB4N6R9T0XC5E",   // null 表示手工放进 vault、无 id 的笔记
  "type": "concept",                    // topic | concept
  "path": "notes/角动量算符的对易关系.md",
  "title": "角动量算符的对易关系",
  "category": "物理/量子力学",             // 待整理的笔记这里是模型的猜测，也可能是 null
  "category_locked": false,
  "tags": [],
  "summary": "三个角动量分量互不对易……",
  "status": "active",
  "duplicate_of": null,                 // 疑似与之重复的笔记 id
  "updated": "2026-07-30T14:22:11+08:00",
  "confidence": 0.88
}
```

---

## 6. 不变量

违反任何一条即为 bug,不是风格问题。

1. **所有对 `notes/`、`inbox/`、`taxonomy.yaml`、`maps/`、`assets/` 的读写必须经过 `backend/app/vault/`。** 其他任何位置出现 `open()` 或 `Path.write_text()` 操作这些路径都是违规。
   `.noteagent/index.db` 是派生物,由 `app/search/` 管理,但其路径必须从 `VaultStore` 取得。
   **注意:豁免的是 `index.db` 这个派生物,不是 `.noteagent/` 这个目录。**
2. 所有文件 IO 显式 `encoding="utf-8"`,路径一律 `pathlib.Path`
3. **模型可以自动新建分类、自动给笔记归类,但不得覆盖使用者手动改过的地方。** `category_locked=true` 的笔记,
   重新归类时不得改它的分类;`locked: true` 的分类,模型不得给它改名、合并或删除(§3.3)。
   手动操作本身不受锁定限制
4. `tags` 中不在受控词表的项**直接丢弃**,不新建
5. **录入只新建笔记文件,不修改也不删除已有笔记。** 会改写或删除已有笔记的只有使用者在界面上主动做的操作:
   移动或确认笔记、分类改名与合并、重新归类(这三种只改 frontmatter 的 `category`、`category_locked`、`status`、
   `confidence`,不动正文、文件名和 `updated`),以及编辑、删除、合并笔记(§2.6)。
   **发现疑似重复时录入也不改已有笔记**——只在新笔记上打标记(§2.5)
6. `maps/` 全部由程序生成,任何手写内容会在下次重建时丢失。
   **`maps/` 下每个 MOC 文件顶部必须有一行提示:「本文件由程序生成,手动修改会在下次重建时丢失。」**
   ——用户在 Obsidian 里看不到本契约文档,唯一能看到的警告就是文件里这一行
7. 每次成功写入以一次 `git commit` 结尾。message 格式:录入 `note: 新建 3 条 (来源 screenshot)`;
   分类改动 `taxonomy: <做了什么>`,如 `taxonomy: 「模型训练」改名为「训练流程」(改写 9 条)`、
   `taxonomy: 重新归类 132 条`。提交失败只记日志,不让操作本身算失败
8. **幂等。** hash 在 `POST /api/ingest` 的 API 边界计算,输入为 `sha256(kind + "\0" + (text or file_bytes))`。
   **filename 不进 hash**——内容相同即同一次输入。
   命中 `ingest_log` 时**不静默跳过**:返回已有结果并置 `duplicate: true`,由用户决定是否带
   `force: true` 重发;`force` 请求跳过幂等检查。
   命中时须校验 `result` 里的 id 仍存在于索引中,**全部失效则删除该条 `ingest_log` 记录**(笔记已被
   用户在 Obsidian 里删掉,这次应该当新输入处理)。
   理由:`kind=generated` 的输入是用户原话(如「记录量子力学角动量」),用户隔一段时间故意重发是
   正常行为,那时 taxonomy 已经不同,应该产出不同结果,而不是被静默挡回
9. 写笔记文件用「先写临时文件再原子 rename」,不允许原地半写
10. 模型给出的分类 `confidence < 0.6` → `status=inbox`(待整理),`category` **保留模型的猜测、不清空**。
    阈值定义在 `app/config.py`,**单一阈值 0.6**,待 eval 校准。使用者手动指定的分类不受此限
11. (v4 删除:`verified` 字段已移除)
12. 精确匹配档(标题、别名)**大小写不敏感**;FTS 档不受此约束
13. **vault 不是本程序独占的。** 用户会直接在 Obsidian 中编辑、重命名、删除笔记。索引必须能容忍
    外部修改:**SQLite 索引与磁盘不一致时以磁盘为准。** 任何依赖「只有本程序写过 vault」的逻辑都是错的
14. **模型用自身知识补充的内容只能出现在 `## 模型补充` 区块里**,且该区块第一行固定为
    「以下内容由模型根据自身知识补充,不是来自你的资料。」的引用。录入时生成的 `summary`、`## 内容`、`## 要点`
    只写输入里有的东西
