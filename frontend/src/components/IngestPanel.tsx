import { useCallback, useEffect, useRef, useState } from "react"
import { ImagePlusIcon, XIcon } from "lucide-react"

import { Alert } from "@/components/Alert"
import { IngestQueue } from "@/components/IngestQueue"
import { Button } from "@/components/ui/button"
import { api, type IngestInput, type IngestJob, type NoteRef } from "@/lib/api"
import { cn } from "@/lib/utils"

const TEXT_LIMIT = 2000

// 已经贴进来、还没加入队列的截图
interface Staged {
  key: number
  dataUrl: string
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
    for (const file of files) {
      if (!file.type.startsWith("image/")) continue
      const reader = new FileReader()
      reader.onload = () =>
        setStaged((list) => [...list, { key: nextKey.current++, dataUrl: reader.result as string }])
      reader.readAsDataURL(file)
    }
  }, [])

  useEffect(() => {
    if (!active) return
    const onPaste = (e: ClipboardEvent) => {
      const images = Array.from(e.clipboardData?.files ?? []).filter((f) =>
        f.type.startsWith("image/"),
      )
      if (images.length > 0) {
        e.preventDefault()
        stage(images)
      }
    }
    window.addEventListener("paste", onPaste)
    return () => window.removeEventListener("paste", onPaste)
  }, [active, stage])

  const trimmed = text.trim()
  const tooLong = trimmed.length >= TEXT_LIMIT
  const canSubmit = !busy && !tooLong && (staged.length > 0 || trimmed.length > 0)

  // 加入队列只是把输入交给后端排队，马上返回；处理在后台进行，这里可以接着贴下一份
  const submit = useCallback(async () => {
    const inputs: IngestInput[] = staged.map((s) => ({ kind: "screenshot", image_b64: s.dataUrl }))
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
    // 贴完截图直接按回车就能加入队列；在文本框里用 Ctrl+Enter（回车留给换行）
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
        <ImagePlusIcon className="size-6" />
        <span>
          按 <kbd className="rounded border bg-background px-1 font-sans">Ctrl+V</kbd>{" "}
          粘贴截图，或把图片拖到这里，或点击选择文件。可以连着放好几张
        </span>
      </button>
      <input
        ref={fileInput}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={(e) => {
          stage(e.target.files ?? [])
          e.target.value = ""
        }}
      />

      {staged.length > 0 && (
        <ul aria-label="待加入队列的截图" className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {staged.map((item, i) => (
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
                onClick={() => setStaged((list) => list.filter((s) => s.key !== item.key))}
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
          placeholder="或者在这里粘贴一段文字"
          className="w-full resize-y rounded-lg border bg-background p-3 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
        />
        {tooLong && (
          <p className="mt-1 text-xs text-destructive">
            目前只支持 {TEXT_LIMIT} 字以内的短文本，当前 {trimmed.length} 字
          </p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button size="lg" disabled={!canSubmit} onClick={() => void submit()}>
          {busy ? "提交中…" : "加入队列"}
        </Button>
        <span className="text-xs text-muted-foreground">
          {staged.length > 0
            ? `${staged.length} 张截图${trimmed ? "和一段文字" : ""}待加入，按回车提交`
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
