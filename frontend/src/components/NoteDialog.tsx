import { useEffect, useState } from "react"
import { XIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { api, type NoteDetail, type NoteRef } from "@/lib/api"

export function NoteDialog({ note, onClose }: { note: NoteRef; onClose: () => void }) {
  const [detail, setDetail] = useState<NoteDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .note(note.path)
      .then((d) => !cancelled && setDetail(d))
      .catch((e: Error) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [note.path])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose()
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={note.title}
        className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-xl border bg-background shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start gap-3 border-b p-4">
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold">{note.title}</h2>
            <p className="mt-0.5 truncate text-xs text-muted-foreground">{note.path}</p>
          </div>
          <Button variant="ghost" size="icon-sm" aria-label="关闭" onClick={onClose}>
            <XIcon />
          </Button>
        </header>
        <div className="overflow-y-auto p-4">
          {error && <p className="text-sm text-destructive">{error}</p>}
          {!error && !detail && <p className="text-sm text-muted-foreground">加载中…</p>}
          {detail && (
            <pre className="font-sans text-sm leading-relaxed whitespace-pre-wrap">
              {detail.body.trim()}
            </pre>
          )}
        </div>
      </div>
    </div>
  )
}
