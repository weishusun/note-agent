import type { MindNode } from "@/lib/mindmapDoc"

// 类型与 docs/SCHEMA.md 第 5 节一一对应

export interface NoteRef {
  id: string | null
  // 主题笔记保留一份输入的整体结构；概念笔记是一个值得单独查到的概念
  type: "topic" | "concept"
  path: string
  title: string
  // 「大块/小块」。待整理的笔记这里是模型的猜测，也可能是 null
  category: string | null
  category_locked: boolean
  tags: string[]
  summary: string
  status: "active" | "inbox" | "archived"
  // 疑似与之重复的那条已有笔记的 id
  duplicate_of: string | null
  updated: string
  confidence: number
  // 来自截图时那张截图的路径（assets/…），用来显示缩略图
  image: string | null
}

// 录入任务依次经过的四个阶段（重新归类的任务阶段是「归类」，不在此列）
export const STAGES = ["解析", "拆分", "分类", "写入"] as const

export interface JobState {
  status: "pending" | "running" | "done" | "failed"
  stage: (typeof STAGES)[number] | "归类" | "模型补充"
  created: NoteRef[]
  inbox: NoteRef[]
  duplicate: boolean
  error: string | null
  progress: { done: number; total: number } | null
}

// 录入队列里的一个任务。created / inbox 是现查的：处理完之后被删、被合并的笔记不再列出来
export interface IngestJob extends JobState {
  id: string
  // supplement：录入完自动排的「模型补充」任务，不是使用者提交的
  kind: "screenshot" | "text" | "file" | "url" | "supplement"
  // 列表里显示的名字：文本的开头、文件名、网址；截图就是「截图」
  label: string
  created_at: string
  finished_at: string | null
}

export interface AskResult {
  answer: string
  sources: NoteRef[]
  grounded: boolean
  keywords: string[]
  // 追问时：结合上文改写出来、实际拿去检索的那句话；不是追问或没改动时为 null
  rewritten: string | null
}

export interface NoteDetail {
  frontmatter: {
    id: string | null
    type: NoteRef["type"]
    title: string
    summary: string
    category: string | null
    category_locked: boolean
    aliases: string[]
    status: NoteRef["status"]
    duplicate_of: string | null
    updated: string
    source: { kind: string; ref: string }
  }
  body: string
}

export interface TreeChild {
  name: string
  path: string
  locked: boolean
  count: number
}

export interface TreeParent {
  name: string
  locked: boolean
  count: number
  children: TreeChild[]
}

export interface Tree {
  total: number
  inbox: number
  duplicates: number
  categories: TreeParent[]
}

// 关系图谱和思维导图的数据（SCHEMA GET /api/graph）
export interface GraphNode {
  // 节点的键：旧笔记可能没有 id
  path: string
  id: string | null
  title: string
  type: NoteRef["type"]
  category: string | null
  status: NoteRef["status"]
  // 不在所选分类里，只是和里面的某条笔记有链接
  outside: boolean
}

export interface Graph {
  nodes: GraphNode[]
  // 无向、去重；source 和 target 都是节点的 path
  edges: { source: string; target: string }[]
}

// 知识库文件夹和设置文件（含密钥）在本机的位置
export interface Locations {
  vault: string
  settings: string
}

// 「我的导图」（SCHEMA §8）
export interface MindmapSummary {
  id: string
  title: string
  updated: string
  node_count: number
}

export interface Mindmap {
  version: 1
  id: string
  title: string
  created: string
  updated: string
  root: string
  nodes: Record<string, MindNode>
}

export interface MindmapView {
  map: Mindmap
  // 导图里链到的笔记现在的样子；已经删掉的不在里面
  notes: Record<string, NoteRef>
}

// 「@ 链接笔记」的候选
export interface NoteTitle {
  id: string
  title: string
  aliases: string[]
  type: NoteRef["type"]
  path: string
}

// 设置页列出的服务商（backend/app/providers.yaml）
export type ModelKind = "vision" | "text" | "embed"
export interface ProviderInfo {
  id: string
  name: string
  base_url: string
  key_url: string | null
  key_hint: string | null
  // 只有格式独特的 Key 才有：粘贴进别家的输入框时据此提示
  key_pattern: string | null
  // GET /models 能列出哪些：all 全部；text 只有文字模型（看图、向量的列不出来）
  model_list: "all" | "text"
  // 在本项目里用真实接口测过
  verified: boolean
  // 推荐模型，第一个是默认值；某类为空表示这家没有
  models: Record<ModelKind, string[]>
  note: string | null
}

// 自定义服务商（清单之外，自己填地址）的 id
export const CUSTOM_PROVIDER = "custom"

export type RoleName = "extract" | "classify" | "answer" | "embed"

export interface ModelList {
  status: "ok" | "bad_key" | "unsupported" | "error"
  models: string[]
  detail: string
}

export interface AccountView {
  base_url: string
  api_key_set: boolean
  api_key_hint: string | null
}

export interface RoleChoice {
  provider: string
  model: string
}

export interface SettingsView {
  // 已经填了 Key 的服务商
  providers: Record<string, AccountView>
  extract: RoleChoice
  classify: RoleChoice
  // 模型为空 = 沿用文字模型（classify）
  answer: RoleChoice
  // 模型为空 = 不做向量检索
  embed: RoleChoice
  disable_thinking: boolean
  extract_disable_thinking: boolean
}

// 只发要改的项。某家服务商给 null 表示删掉它；api_key 留空表示不修改
export interface SettingsUpdate {
  providers?: Record<string, { api_key?: string; base_url?: string } | null>
  extract?: Partial<RoleChoice>
  classify?: Partial<RoleChoice>
  answer?: Partial<RoleChoice>
  embed?: Partial<RoleChoice>
  disable_thinking?: boolean
  extract_disable_thinking?: boolean
}

// 向量索引的进度：total 条笔记里有 embedded 条已经算好了向量
export interface VectorStatus {
  enabled: boolean
  model: string | null
  total: number
  embedded: number
}

export interface ProbeResult {
  ok: boolean
  detail: string
  seconds: number
}

// 要打开的笔记。从双链点进来时只知道文件名、不知道它在 notes/ 还是 inbox/，
// 所以 paths 是按顺序尝试的候选路径
export interface NoteTarget {
  title: string
  paths: string[]
}

// 「人工智能/模型训练」在界面上显示成「人工智能 › 模型训练」
export function categoryLabel(category: string): string {
  return category.replace("/", " › ")
}

export function linkTarget(stem: string): NoteTarget {
  return { title: stem, paths: [`notes/${stem}.md`, `inbox/${stem}.md`] }
}

export type IngestInput =
  | { kind: "text"; text: string; force?: boolean }
  | { kind: "screenshot"; image_b64: string; force?: boolean }
  // PDF、Markdown、纯文本。file_b64 可以带 data URL 前缀
  | { kind: "file"; file_b64: string; filename: string; force?: boolean }
  // 网页链接：后端去抓取正文。需要登录的页面抓不到
  | { kind: "url"; url: string; force?: boolean }

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(url, init)
  } catch {
    throw new Error("连不上后端服务，请确认它已启动")
  }
  if (!resp.ok) {
    throw new Error(await errorMessage(resp))
  }
  return (await resp.json()) as T
}

async function errorMessage(resp: Response): Promise<string> {
  try {
    const { detail } = await resp.json()
    if (typeof detail === "string") return detail
    // FastAPI 的 422：detail 是校验错误数组
    if (Array.isArray(detail)) {
      return detail.map((d) => String(d.msg).replace(/^Value error, /, "")).join("；")
    }
  } catch {
    // 响应体不是 JSON，落到下面的通用提示
  }
  return `请求失败（${resp.status}）`
}

// 编辑一条笔记时可以改的字段；只发要改的
export interface NoteEdit {
  title?: string
  summary?: string
  aliases?: string[]
  body?: string
}

function send<T>(method: "POST" | "PUT", url: string, body: unknown): Promise<T> {
  return request<T>(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
}

const post = <T,>(url: string, body: unknown) => send<T>("POST", url, body)

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value))
  }
  return search.toString()
}

export const api = {
  ingest: (input: IngestInput) => post<{ job_id: string }>("/api/ingest", input),
  job: (jobId: string) => request<JobState>(`/api/jobs/${encodeURIComponent(jobId)}`),
  // 录入队列：最近提交的在前。重试 = 失败的再来一次，或者「之前录入过」的重新处理
  jobs: () => request<IngestJob[]>("/api/jobs"),
  retryJob: (jobId: string) =>
    post<IngestJob>(`/api/jobs/${encodeURIComponent(jobId)}/retry`, {}),
  removeJob: (jobId: string) =>
    request<{ removed: boolean }>(`/api/jobs/${encodeURIComponent(jobId)}`, {
      method: "DELETE",
    }),
  clearJobs: () => request<{ removed: number }>("/api/jobs", { method: "DELETE" }),
  // history：这段对话里之前问完了的几轮（从早到晚）。带上它，这次的问题按追问处理
  ask: (question: string, history: { question: string; answer: string }[] = []) =>
    post<AskResult>("/api/ask", { question, history }),

  notes: (filter: { category?: string; status?: "inbox"; duplicates?: "true" } = {}) =>
    request<NoteRef[]>(`/api/notes?${query({ ...filter, limit: 200 })}`),
  search: (q: string) =>
    request<{ results: NoteRef[] }>(
      `/api/search?${query({ q, include_inbox: "true", limit: 50 })}`,
    ).then((data) => data.results),
  note: (path: string) =>
    request<NoteDetail>(`/api/notes/${path.split("/").map(encodeURIComponent).join("/")}`),
  reindex: () => post<{ indexed: number }>("/api/reindex", {}),

  // 手写新建、编辑、删除。category 不给则由模型自动归类
  createNote: (note: Required<NoteEdit> & { category?: string }) =>
    post<NoteRef>("/api/notes", note),
  editNote: (id: string, changes: NoteEdit) =>
    send<NoteRef>("PUT", `/api/notes/${encodeURIComponent(id)}`, changes),
  // replacedBy：疑似重复里「只留一条」——留下的是 replacedBy，指向被删这条的链接改指它
  deleteNote: (id: string, replacedBy?: string) =>
    request<{ deleted: boolean }>(
      `/api/notes/${encodeURIComponent(id)}?${query({ replaced_by: replacedBy })}`,
      { method: "DELETE" },
    ),
  // 疑似重复的另外两种处理：把 id 这条并进 into 那条；两条都留（清掉标记）
  mergeNote: (id: string, into: string) =>
    post<NoteRef>(`/api/notes/${encodeURIComponent(id)}/merge`, { into }),
  keepNote: (id: string) => post<NoteRef>(`/api/notes/${encodeURIComponent(id)}/keep`, {}),
  // 批量补充：outdated = 还没有、或只有旧的短补充的笔记；all = 全部重写
  supplementCandidates: (scope: "outdated" | "all" = "outdated") =>
    request<{ count: number }>(`/api/supplements?${query({ scope })}`),
  batchSupplement: (scope: "outdated" | "all" = "outdated") =>
    post<{ count: number; jobs: number }>("/api/supplements", { scope }),
  // （重新）写模型补充，写完才返回，十几到几十秒
  supplementNote: (id: string) =>
    post<NoteRef>(`/api/notes/${encodeURIComponent(id)}/supplement`, {}),
  ref: (id: string) => request<NoteRef>(`/api/refs/${encodeURIComponent(id)}`),

  // category 是「大块」或「大块/小块」；不给就是全部笔记
  graph: (category: string | null) =>
    request<Graph>(`/api/graph?${query({ category: category ?? undefined })}`),

  // 分类：路径里有中文和「/」，一律放在请求体或查询参数里
  tree: () => request<Tree>("/api/tree"),
  moveNote: (id: string, category: string) =>
    send<NoteRef>("PUT", `/api/notes/${encodeURIComponent(id)}/category`, { category }),
  createCategory: (path: string) => post<Tree>("/api/categories", { path }),
  renameCategory: (path: string, name: string) =>
    send<Tree>("PUT", "/api/categories", { path, name }),
  deleteCategory: (path: string) =>
    request<Tree>(`/api/categories?${query({ path })}`, { method: "DELETE" }),
  reclassify: (scope: "unsorted" | "all") => post<{ job_id: string }>("/api/reclassify", { scope }),

  locations: () => request<Locations>("/api/locations"),
  titles: () => request<NoteTitle[]>("/api/titles"),
  mindmaps: () => request<MindmapSummary[]>("/api/mindmaps"),
  mindmap: (id: string) => request<MindmapView>(`/api/mindmaps/${encodeURIComponent(id)}`),
  createMindmap: (body: { title: string; root?: string; nodes?: Mindmap["nodes"] }) =>
    post<MindmapView>("/api/mindmaps", body),
  saveMindmap: (id: string, body: { title: string; root: string; nodes: Mindmap["nodes"] }) =>
    send<MindmapView>("PUT", `/api/mindmaps/${encodeURIComponent(id)}`, body),
  deleteMindmap: (id: string) =>
    request<{ deleted: boolean }>(`/api/mindmaps/${encodeURIComponent(id)}`, { method: "DELETE" }),
  providers: () => request<ProviderInfo[]>("/api/providers"),
  // 用这家的 Key 问它有哪些模型（顺便验证 Key），不保存。不带 Key 就用已保存的
  providerModels: (provider: string, body: { api_key?: string; base_url?: string } = {}) =>
    post<ModelList>(`/api/providers/${encodeURIComponent(provider)}/models`, body),
  settings: () => request<SettingsView>("/api/settings"),
  saveSettings: (update: SettingsUpdate) => send<SettingsView>("PUT", "/api/settings", update),
  // 没填向量模型时结果里没有 embedding
  testSettings: () =>
    post<{ text: ProbeResult; vision: ProbeResult; answer?: ProbeResult; embedding?: ProbeResult }>(
      "/api/settings/test",
      {},
    ),
  vectors: () => request<VectorStatus>("/api/vectors"),
  // 在后台把缺的向量补上，马上返回当前进度
  syncVectors: () => post<VectorStatus>("/api/vectors/sync", {}),
}
