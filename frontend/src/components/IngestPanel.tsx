import { useCallback, useEffect, useRef, useState } from "react"
import { ImagePlusIcon, XIcon } from "lucide-react"

import { Alert } from "@/components/Alert"
import { NoteCard } from "@/components/NoteCard"
import { Button } from "@/components/ui/button"
import { api, STAGES, type IngestInput, type JobState, type NoteRef } from "@/lib/api"
import { cn } from "@/lib/utils"

const POLL_INTERVAL_MS = 700
const TEXT_LIMIT = 2000

export function IngestPanel({
  active,
  onOpenNote,
}: {
  active: boolean
  onOpenNote: (note: NoteRef) => void
}) {
  const [image, setImage] = useState<string | null>(null)
  const [text, setText] = useState("")
  const [dragging, setDragging] = useState(false)
  const [job, setJob] = useState<JobState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 上一次提交的内容，「重新处理」时原样带 force 重发
  const lastInput = useRef<IngestInput | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const loadImage = useCallback((file: File | null | undefined) => {
    if (!file || !file.type.startsWith("image/")) return
    const reader = new FileReader()
    reader.onload = () => setImage(reader.result as string)
    reader.readAsDataURL(file)
  }, [])

  useEffect(() => {
    if (!active) return
    const onPaste = (e: ClipboardEvent) => {
      const file = Array.from(e.clipboardData?.files ?? []).find((f) =>
        f.type.startsWith("image/"),
      )
      if (file) {
        e.preventDefault()
        loadImage(file)
      }
    }
    window.addEventListener("paste", onPaste)
    return () => window.removeEventListener("paste", onPaste)
  }, [active, loadImage])

  async function submit(input: IngestInput) {
    lastInput.current = input
    setBusy(true)
    setError(null)
    setJob(null)
    try {
      const { job_id } = await api.ingest(input)
      for (;;) {
        const state = await api.job(job_id)
        if (!mounted.current) return
        setJob(state)
        if (state.status === "done" || state.status === "failed") break
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
      }
    } catch (e) {
      if (mounted.current) setError((e as Error).message)
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  const trimmed = text.trim()
  const tooLong = trimmed.length >= TEXT_LIMIT
  const canSubmit = !busy && (image !== null || (trimmed.length > 0 && !tooLong))

  function onSubmit() {
    if (image !== null) void submit({ kind: "screenshot", image_b64: image })
    else void submit({ kind: "text", text: trimmed })
  }

  const done = job?.status === "done" ? job : null
  const notes = done ? [...done.created, ...done.inbox] : []

  return (
    <section className="flex flex-col gap-4">
      {image ? (
        <div className="relative rounded-lg border bg-muted/40 p-2">
          <img src={image} alt="待录入的截图" className="mx-auto max-h-80 rounded-md" />
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="移除截图"
            className="absolute top-3 right-3"
            disabled={busy}
            onClick={() => setImage(null)}
          >
            <XIcon />
          </Button>
        </div>
      ) : (
        <>
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragging(false)
              loadImage(e.dataTransfer.files[0])
            }}
            className={cn(
              "flex flex-col items-center gap-2 rounded-lg border border-dashed p-8 text-sm text-muted-foreground transition-colors hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
              dragging && "border-ring bg-muted",
            )}
          >
            <ImagePlusIcon className="size-6" />
            <span>
              按 <kbd className="rounded border bg-background px-1 font-sans">Ctrl+V</kbd>{" "}
              粘贴截图，或把图片拖到这里，或点击选择文件
            </span>
          </button>
          <input
            ref={fileInput}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              loadImage(e.target.files?.[0])
              e.target.value = ""
            }}
          />
          <div>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={busy}
              rows={5}
              aria-label="要录入的文字"
              placeholder="或者在这里粘贴一段文字"
              className="w-full resize-y rounded-lg border bg-background p-3 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
            />
            {tooLong && (
              <p className="mt-1 text-xs text-destructive">
                目前只支持 {TEXT_LIMIT} 字以内的短文本，当前 {trimmed.length} 字
              </p>
            )}
          </div>
        </>
      )}

      <div className="flex items-center gap-3">
        <Button size="lg" disabled={!canSubmit} onClick={onSubmit}>
          {busy ? "处理中…" : "录入知识库"}
        </Button>
        {busy && <StageProgress stage={job?.stage ?? "解析"} />}
      </div>

      {error && <Alert tone="error">{error}</Alert>}
      {job?.status === "failed" && <Alert tone="error">录入失败：{job.error}</Alert>}

      {done && (
        <div className="flex flex-col gap-3">
          {done.duplicate ? (
            <Alert tone="info">
              <span className="flex-1">这份内容之前录入过，下面是当时生成的笔记。</span>
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() =>
                  lastInput.current && void submit({ ...lastInput.current, force: true })
                }
              >
                重新处理
              </Button>
            </Alert>
          ) : (
            <p className="text-sm text-muted-foreground">
              新建了 {notes.length} 条笔记
              {done.inbox.length > 0 && `，其中 ${done.inbox.length} 条待整理`}
            </p>
          )}
          {notes.map((note) => (
            <NoteCard key={note.path} note={note} onOpen={onOpenNote} />
          ))}
        </div>
      )}
    </section>
  )
}

function StageProgress({ stage }: { stage: JobState["stage"] }) {
  const current = STAGES.indexOf(stage)
  return (
    <ol className="flex items-center gap-1.5 text-xs" aria-label="处理进度">
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
