import { useEffect, useState } from "react"
import { XIcon } from "lucide-react"

import { NoteBody } from "@/components/NoteBody"
import { Button } from "@/components/ui/button"
import { api, type NoteDetail, type NoteTarget } from "@/lib/api"

type Loaded = { path: string; detail: NoteDetail } | { error: string }

export function NoteDialog({
  target,
  onOpenLink,
  onClose,
}: {
  target: NoteTarget
  onOpenLink: (stem: string) => void
  onClose: () => void
}) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoaded(null)
    void (async () => {
      let lastError = "笔记不存在"
      for (const path of target.paths) {
        try {
          const detail = await api.note(path)
          if (!cancelled) setLoaded({ path, detail })
          return
        } catch (e) {
          lastError = (e as Error).message
        }
      }
      if (!cancelled) setLoaded({ error: lastError })
    })()
    return () => {
      cancelled = true
    }
  }, [target])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose()
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  const detail = loaded && "detail" in loaded ? loaded.detail : null
  const frontmatter = detail?.frontmatter

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={frontmatter?.title ?? target.title}
        className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-xl border bg-background shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start gap-3 border-b p-4">
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold">{frontmatter?.title ?? target.title}</h2>
            {frontmatter && (
              <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <span>
                  {frontmatter.category ?? `待整理 · ${frontmatter.suggested_domain ?? "未知领域"}`}
                </span>
                <span>{frontmatter.updated.slice(0, 10)}</span>
                {!frontmatter.verified && <span>未核实</span>}
                {frontmatter.aliases.length > 0 && (
                  <span>别名：{frontmatter.aliases.join("、")}</span>
                )}
              </p>
            )}
          </div>
          <Button variant="ghost" size="icon-sm" aria-label="关闭" onClick={onClose}>
            <XIcon />
          </Button>
        </header>
        <div className="overflow-y-auto p-4">
          {!loaded && <p className="text-sm text-muted-foreground">加载中…</p>}
          {loaded && "error" in loaded && (
            <p className="text-sm text-destructive">{loaded.error}</p>
          )}
          {detail && <NoteBody body={detail.body} onOpenLink={onOpenLink} />}
        </div>
      </div>
    </div>
  )
}
