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
  frontmatter: { title: string; source: { kind: string; ref: string } }
  body: string
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
  note: (path: string) =>
    request<NoteDetail>(`/api/notes/${path.split("/").map(encodeURIComponent).join("/")}`),
}
