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
}

// 录入任务依次经过的四个阶段（重新归类的任务阶段是「归类」，不在此列）
export const STAGES = ["解析", "拆分", "分类", "写入"] as const

export interface JobState {
  status: "pending" | "running" | "done" | "failed"
  stage: (typeof STAGES)[number] | "归类"
  created: NoteRef[]
  inbox: NoteRef[]
  duplicate: boolean
  error: string | null
  progress: { done: number; total: number } | null
}

// 录入队列里的一个任务。created / inbox 是现查的：处理完之后被删、被合并的笔记不再列出来
export interface IngestJob extends JobState {
  id: string
  kind: "screenshot" | "text" | "file" | "url"
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

export interface SettingsView {
  base_url: string
  api_key_set: boolean
  api_key_hint: string | null
  extract_model: string
  classify_model: string
  answer_model: string
  disable_thinking: boolean
  extract_disable_thinking: boolean
}

// 只发要改的项；api_key 留空表示不修改
export type SettingsUpdate = Partial<
  Omit<SettingsView, "api_key_set" | "api_key_hint"> & { api_key: string }
>

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
  ask: (question: string) => post<AskResult>("/api/ask", { question }),

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
  ref: (id: string) => request<NoteRef>(`/api/refs/${encodeURIComponent(id)}`),

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

  settings: () => request<SettingsView>("/api/settings"),
  saveSettings: (update: SettingsUpdate) => send<SettingsView>("PUT", "/api/settings", update),
  testSettings: () =>
    post<{ text: ProbeResult; vision: ProbeResult }>("/api/settings/test", {}),
}
