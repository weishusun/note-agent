import { useState } from "react"
import {
  AlertCircleIcon,
  CheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ClockIcon,
  LoaderIcon,
  XIcon,
} from "lucide-react"

import { Alert } from "@/components/Alert"
import { NoteCard } from "@/components/NoteCard"
import { Button } from "@/components/ui/button"
import { api, STAGES, type IngestJob, type NoteRef } from "@/lib/api"
import { cn } from "@/lib/utils"

const KIND_LABEL: Record<string, string> = {
  screenshot: "截图",
  text: "文本",
  file: "文件",
  url: "网页",
}

// 录入队列：排队中、处理中、处理完的任务，最近提交的在前
export function IngestQueue({
  jobs,
  onChanged,
  onOpenNote,
  onCompare,
}: {
  jobs: IngestJob[]
  // 取消、重试、清除之后：让队列重新加载
  onChanged: () => void
  onOpenNote: (note: NoteRef) => void
  onCompare: (ids: string[]) => void
}) {
  // 使用者手动展开 / 收起过的任务；没动过的按默认（只展开最近完成的那一个）
  const [toggled, setToggled] = useState<ReadonlyMap<string, boolean>>(new Map())
  const [error, setError] = useState<string | null>(null)

  async function act(action: () => Promise<unknown>) {
    setError(null)
    try {
      await action()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      onChanged()
    }
  }

  const newestDone = jobs.find((job) => job.status === "done")?.id
  const cleanable = jobs.some((job) => job.status === "done")

  return (
    <section aria-label="录入队列" className="flex flex-col gap-2">
      <div className="flex min-h-7 items-center gap-2">
        <h2 className="flex-1 text-sm font-semibold">录入队列</h2>
        {cleanable && (
          <Button
            size="sm"
            variant="ghost"
            className="text-muted-foreground"
            title="只清掉这里的记录，已经写进知识库的笔记不受影响"
            onClick={() => void act(() => api.clearJobs())}
          >
            清除已完成
          </Button>
        )}
      </div>
      {error && <Alert tone="error">{error}</Alert>}
      <ul className="flex flex-col gap-2">
        {jobs.map((job) => {
          const open = toggled.get(job.id) ?? job.id === newestDone
          return (
            <JobRow
              key={job.id}
              job={job}
              open={open}
              onToggle={() => setToggled(new Map(toggled).set(job.id, !open))}
              onRetry={() => void act(() => api.retryJob(job.id))}
              onRemove={() => void act(() => api.removeJob(job.id))}
              onOpenNote={onOpenNote}
              onCompare={onCompare}
            />
          )
        })}
      </ul>
    </section>
  )
}

function JobRow({
  job,
  open,
  onToggle,
  onRetry,
  onRemove,
  onOpenNote,
  onCompare,
}: {
  job: IngestJob
  open: boolean
  onToggle: () => void
  onRetry: () => void
  onRemove: () => void
  onOpenNote: (note: NoteRef) => void
  onCompare: (ids: string[]) => void
}) {
  const notes = [...job.created, ...job.inbox]
  const suspected = notes.flatMap((n) => (n.duplicate_of && n.id ? [n.id] : []))
  const expandable = job.status === "done" && notes.length > 0
  // 处理完之后用主题笔记的标题当名字，比「截图」好认
  const title = job.status === "done" && notes.length > 0 ? notes[0].title : job.label
  const meta = [KIND_LABEL[job.kind] ?? job.kind, job.created_at.slice(11, 16)].join(" · ")

  return (
    <li className="rounded-lg border bg-card">
      <div className="flex items-center gap-3 p-3">
        <StatusIcon status={job.status} />
        <button
          type="button"
          disabled={!expandable}
          aria-expanded={expandable ? open : undefined}
          onClick={onToggle}
          className="flex min-w-0 flex-1 flex-col items-start gap-0.5 text-left focus-visible:outline-none"
        >
          <span className="flex w-full items-center gap-1 text-sm font-medium">
            {expandable &&
              (open ? (
                <ChevronDownIcon className="size-3.5 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronRightIcon className="size-3.5 shrink-0 text-muted-foreground" />
              ))}
            <span className="truncate">{title}</span>
          </span>
          <span className="text-xs text-muted-foreground">
            {meta} · <StatusText job={job} count={notes.length} suspected={suspected.length} />
          </span>
          {job.status === "failed" && (
            <span className="text-xs text-destructive">{job.error ?? "处理失败"}</span>
          )}
        </button>
        {job.status === "running" && <StageProgress stage={job.stage} />}
        {job.status === "pending" && (
          <Button size="sm" variant="ghost" onClick={onRemove}>
            取消
          </Button>
        )}
        {job.status === "failed" && (
          <Button size="sm" variant="outline" onClick={onRetry}>
            重试
          </Button>
        )}
        {job.status === "done" && job.duplicate && (
          <Button
            size="sm"
            variant="outline"
            title="不管之前录入过，重新生成一遍笔记"
            onClick={onRetry}
          >
            重新处理
          </Button>
        )}
        {(job.status === "done" || job.status === "failed") && (
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="清除这条记录"
            title="只清掉这条记录，笔记不受影响"
            onClick={onRemove}
          >
            <XIcon />
          </Button>
        )}
      </div>
      {expandable && open && (
        <div className="flex flex-col gap-2 border-t p-3">
          {suspected.length > 0 && (
            <Alert tone="info">
              <span className="flex-1">
                其中 {suspected.length} 条和知识库里已有的笔记同名，疑似重复。
              </span>
              <Button size="sm" onClick={() => onCompare(suspected)}>
                对比并处理
              </Button>
            </Alert>
          )}
          {notes.map((note) => (
            <NoteCard key={note.path} note={note} onOpen={onOpenNote} />
          ))}
        </div>
      )}
    </li>
  )
}

function StatusText({
  job,
  count,
  suspected,
}: {
  job: IngestJob
  count: number
  suspected: number
}) {
  if (job.status === "pending") return <>排队中</>
  if (job.status === "running") return <>处理中</>
  if (job.status === "failed") return <>失败</>
  if (count === 0) return <>笔记已经不在了</>
  if (job.duplicate) return <>之前录入过，这是当时的 {count} 条笔记</>
  return (
    <>
      {count} 条笔记
      {job.inbox.length > 0 && `，${job.inbox.length} 条待整理`}
      {suspected > 0 && `，${suspected} 条疑似重复`}
    </>
  )
}

function StatusIcon({ status }: { status: IngestJob["status"] }) {
  const base = "size-4 shrink-0"
  if (status === "running") {
    return <LoaderIcon className={cn(base, "animate-spin text-primary")} aria-label="处理中" />
  }
  if (status === "pending") {
    return <ClockIcon className={cn(base, "text-muted-foreground")} aria-label="排队中" />
  }
  if (status === "failed") {
    return <AlertCircleIcon className={cn(base, "text-destructive")} aria-label="失败" />
  }
  return <CheckIcon className={cn(base, "text-muted-foreground")} aria-label="完成" />
}

function StageProgress({ stage }: { stage: IngestJob["stage"] }) {
  const current = (STAGES as readonly string[]).indexOf(stage)
  return (
    <ol className="hidden items-center gap-1 text-xs sm:flex" aria-label="处理进度">
      {STAGES.map((name, i) => (
        <li
          key={name}
          aria-current={i === current ? "step" : undefined}
          className={cn(
            "rounded-full px-2 py-0.5",
            i < current && "text-muted-foreground line-through",
            i === current && "bg-primary text-primary-foreground",
            i > current && "text-muted-foreground",
          )}
        >
          {name}
        </li>
      ))}
    </ol>
  )
}
