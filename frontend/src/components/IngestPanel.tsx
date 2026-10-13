import { useCallback, useEffect, useRef, useState } from "react"
import { FileTextIcon, UploadIcon, XIcon } from "lucide-react"

import { Alert } from "@/components/Alert"
import { IngestQueue } from "@/components/IngestQueue"
import { Button } from "@/components/ui/button"
import { api, type IngestInput, type IngestJob, type NoteRef } from "@/lib/api"
import { cn } from "@/lib/utils"

// 不到这个长度的文字保留原结构；达到的按长资料处理（与后端 SHORT_TEXT_MAX_CHARS 一致）
const LONG_TEXT_CHARS = 2000
const FILE_TYPES = [".pdf", ".md", ".markdown", ".txt"]
const MAX_FILE_BYTES = 30 * 1024 * 1024

// 已经放进来、还没加入队列的截图或文件
interface Staged {
  key: number
  kind: "screenshot" | "file"
  name: string
  size: number
  dataUrl: string
}

function fileSize(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function IngestPanel({
  active,
  jobs,
  jobsError,
  onJobsChanged,
  onOpenNote,
  onCompare,
}: {
  active: boolean
  // 录入队列，由 App 统一轮询（别的页面也要显示还有几份没处理完）
  jobs: IngestJob[] | null
  jobsError: string | null
  onJobsChanged: () => void
  onOpenNote: (note: NoteRef) => void
  onCompare: (ids: string[]) => void
}) {
  const [staged, setStaged] = useState<Staged[]>([])
  const [text, setText] = useState("")
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const nextKey = useRef(0)

  const stage = useCallback((files: Iterable<File>) => {
    const rejected: string[] = []
    for (const file of files) {
      const isImage = file.type.startsWith("image/")
      const isDocument = FILE_TYPES.some((ext) => file.name.toLowerCase().endsWith(ext))
      if (!isImage && !isDocument) {
        rejected.push(`「${file.name}」不是支持的类型`)
        continue
      }
      if (file.size > MAX_FILE_BYTES) {
        rejected.push(`「${file.name}」超过 ${fileSize(MAX_FILE_BYTES)}`)
        continue
      }
      const reader = new FileReader()
      reader.onload = () =>
        setStaged((list) => [
          ...list,
          {
            key: nextKey.current++,
            kind: isImage ? "screenshot" : "file",
            name: file.name,
            size: file.size,
            dataUrl: reader.result as string,
          },
        ])
      reader.readAsDataURL(file)
    }
    setError(
      rejected.length > 0
        ? `${rejected.join("；")}。支持图片、PDF、Markdown 和纯文本文件。`
        : null,
    )
  }, [])

  useEffect(() => {
    if (!active) return
    const onPaste = (e: ClipboardEvent) => {
      const files = Array.from(e.clipboardData?.files ?? [])
      if (files.length > 0) {
        e.preventDefault()
        stage(files)
      }
    }
    window.addEventListener("paste", onPaste)
    return () => window.removeEventListener("paste", onPaste)
  }, [active, stage])

  const trimmed = text.trim()
  const canSubmit = !busy && (staged.length > 0 || trimmed.length > 0)

  // 加入队列只是把输入交给后端排队，马上返回；处理在后台进行，这里可以接着放下一份
  const submit = useCallback(async () => {
    const inputs: IngestInput[] = staged.map((s) =>
      s.kind === "screenshot"
        ? { kind: "screenshot", image_b64: s.dataUrl }
        : { kind: "file", file_b64: s.dataUrl, filename: s.name },
    )
    if (trimmed) inputs.push({ kind: "text", text: trimmed })
    setBusy(true)
    setError(null)
    let sent = 0
    try {
      for (const input of inputs) {
        await api.ingest(input)
        sent++
      }
      setStaged([])
      setText("")
    } catch (e) {
      setError((e as Error).message)
      // 已经交出去的不再留着，免得重复提交
      setStaged((list) => list.slice(sent))
    } finally {
      setBusy(false)
      onJobsChanged()
    }
  }, [staged, trimmed, onJobsChanged])

  useEffect(() => {
    if (!active || !canSubmit) return
    // 放好截图直接按回车就能加入队列；在文本框里用 Ctrl+Enter（回车留给换行）
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Enter" || e.isComposing) return
      const inField = e.target instanceof HTMLElement && e.target.closest("textarea, input, button")
      if (inField ? e.ctrlKey || e.metaKey : staged.length > 0) {
        e.preventDefault()
        void submit()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [active, canSubmit, staged.length, submit])

  const images = staged.filter((s) => s.kind === "screenshot")
  const documents = staged.filter((s) => s.kind === "file")
  const remove = (key: number) => setStaged((list) => list.filter((s) => s.key !== key))

  return (
    <section className="flex flex-col gap-4">
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
          stage(e.dataTransfer.files)
        }}
        className={cn(
          "flex flex-col items-center gap-2 rounded-lg border border-dashed p-6 text-sm text-muted-foreground transition-colors hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
          dragging && "border-ring bg-muted",
        )}
      >
        <UploadIcon className="size-6" />
        <span>
          按 <kbd className="rounded border bg-background px-1 font-sans">Ctrl+V</kbd>{" "}
          粘贴截图，或把截图、PDF、Markdown、文本文件拖到这里，或点击选择。可以连着放好几份
        </span>
      </button>
      <input
        ref={fileInput}
        type="file"
        accept={["image/*", ...FILE_TYPES].join(",")}
        multiple
        className="hidden"
        onChange={(e) => {
          stage(e.target.files ?? [])
          e.target.value = ""
        }}
      />

      {images.length > 0 && (
        <ul aria-label="待加入队列的截图" className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {images.map((item, i) => (
            <li key={item.key} className="relative rounded-lg border bg-muted/40 p-1">
              <img
                src={item.dataUrl}
                alt={`待录入的截图 ${i + 1}`}
                className="h-28 w-full rounded-md object-contain"
              />
              <Button
                variant="outline"
                size="icon-xs"
                aria-label={`移除截图 ${i + 1}`}
                className="absolute top-2 right-2"
                disabled={busy}
                onClick={() => remove(item.key)}
              >
                <XIcon />
              </Button>
            </li>
          ))}
        </ul>
      )}
      {documents.length > 0 && (
        <ul aria-label="待加入队列的文件" className="flex flex-col gap-2">
          {documents.map((item) => (
            <li key={item.key} className="flex items-center gap-2 rounded-lg border bg-muted/40 p-2">
              <FileTextIcon className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate text-sm">{item.name}</span>
              <span className="text-xs text-muted-foreground">{fileSize(item.size)}</span>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label={`移除 ${item.name}`}
                disabled={busy}
                onClick={() => remove(item.key)}
              >
                <XIcon />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <div>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={busy}
          rows={4}
          aria-label="要录入的文字"
          placeholder="或者在这里粘贴一段文字，长短都可以"
          className="w-full resize-y rounded-lg border bg-background p-3 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
        />
        {trimmed.length >= LONG_TEXT_CHARS && (
          <p className="mt-1 text-xs text-muted-foreground">
            {trimmed.length} 字，会按长文档处理：生成摘要、大纲和关键概念，不逐句保留原文。
          </p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button size="lg" disabled={!canSubmit} onClick={() => void submit()}>
          {busy ? "提交中…" : "加入队列"}
        </Button>
        <span className="text-xs text-muted-foreground">
          {staged.length > 0
            ? `${staged.length} 份${trimmed ? "和一段文字" : ""}待加入，按回车提交`
            : "加入后在后台依次处理，不用等，可以接着放下一份"}
        </span>
      </div>

      {error && <Alert tone="error">{error}</Alert>}
      {jobsError && <Alert tone="error">{jobsError}</Alert>}

      {jobs && jobs.length > 0 && (
        <IngestQueue
          jobs={jobs}
          onChanged={onJobsChanged}
          onOpenNote={onOpenNote}
          onCompare={onCompare}
        />
      )}
    </section>
  )
}
