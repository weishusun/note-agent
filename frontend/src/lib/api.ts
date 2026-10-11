// 类型与 docs/SCHEMA.md 第 5 节一一对应

export interface NoteRef {
  id: string | null
  path: string
  title: string
  category: string | null
  suggested_domain: string | null
  tags: string[]
  summary: string
  status: "active" | "inbox" | "archived"
  verified: boolean
  updated: string
  confidence: number
}

export const STAGES = ["解析", "拆分", "分类", "写入"] as const

export interface JobState {
  status: "pending" | "running" | "done" | "failed"
  stage: (typeof STAGES)[number]
  created: NoteRef[]
  inbox: NoteRef[]
  duplicate: boolean
  error: string | null
}

export interface AskResult {
  answer: string
  sources: NoteRef[]
  grounded: boolean
  keywords: string[]
}

export interface NoteDetail {
  frontmatter: {
    title: string
    category: string | null
    suggested_domain: string | null
    aliases: string[]
    verified: boolean
    updated: string
    source: { kind: string; ref: string }
  }
  body: string
}

// 要打开的笔记。从双链点进来时只知道文件名、不知道它在 notes/ 还是 inbox/，
// 所以 paths 是按顺序尝试的候选路径
export interface NoteTarget {
  title: string
  paths: string[]
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
    if (Array.isArray(detail)) return detail.map((d) => d.msg).join("；")
  } catch {
    // 响应体不是 JSON，落到下面的通用提示
  }
  return `请求失败（${resp.status}）`
}

function post<T>(url: string, body: unknown): Promise<T> {
  return request<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
}

export const api = {
  ingest: (input: IngestInput) => post<{ job_id: string }>("/api/ingest", input),
  job: (jobId: string) => request<JobState>(`/api/jobs/${encodeURIComponent(jobId)}`),
  ask: (question: string) => post<AskResult>("/api/ask", { question }),
  notes: (limit = 100) => request<NoteRef[]>(`/api/notes?limit=${limit}`),
  search: (q: string) =>
    request<{ results: NoteRef[] }>(
      `/api/search?include_inbox=true&limit=50&q=${encodeURIComponent(q)}`,
    ).then((data) => data.results),
  reindex: () => post<{ indexed: number }>("/api/reindex", {}),
  note: (path: string) =>
    request<NoteDetail>(`/api/notes/${path.split("/").map(encodeURIComponent).join("/")}`),
}
