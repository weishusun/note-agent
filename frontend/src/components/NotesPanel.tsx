import { useCallback, useEffect, useState } from "react"
import { RefreshCwIcon } from "lucide-react"

import { Alert } from "@/components/Alert"
import { NoteCard } from "@/components/NoteCard"
import { Button } from "@/components/ui/button"
import { api, type NoteRef } from "@/lib/api"

const SEARCH_DEBOUNCE_MS = 250

export function NotesPanel({
  active,
  onOpenNote,
}: {
  active: boolean
  onOpenNote: (note: NoteRef) => void
}) {
  const [query, setQuery] = useState("")
  const [notes, setNotes] = useState<NoteRef[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  // 递增即重新加载：切回本页、点了同步之后
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    if (active) setReloadKey((k) => k + 1)
  }, [active])

  useEffect(() => {
    if (!active) return
    let cancelled = false
    const q = query.trim()
    const timer = setTimeout(
      () => {
        ;(q ? api.search(q) : api.notes())
          .then((result) => {
            if (cancelled) return
            setNotes(result)
            setError(null)
          })
          .catch((e: Error) => !cancelled && setError(e.message))
      },
      q ? SEARCH_DEBOUNCE_MS : 0,
    )
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [active, query, reloadKey])

  const sync = useCallback(async () => {
    setSyncing(true)
    try {
      await api.reindex()
      setReloadKey((k) => k + 1)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSyncing(false)
    }
  }, [])

  const searching = query.trim().length > 0

  return (
    <section className="flex flex-col gap-4">
      <div className="flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="搜索笔记"
          placeholder="搜索标题、别名或正文"
          className="h-9 min-w-0 flex-1 rounded-lg border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
        />
        <Button
          variant="outline"
          size="lg"
          disabled={syncing}
          onClick={() => void sync()}
          title="在 Obsidian 里增删改过笔记后，点这里让列表和检索跟上"
        >
          <RefreshCwIcon className={syncing ? "animate-spin" : undefined} />
          同步
        </Button>
      </div>

      {error && <Alert tone="error">{error}</Alert>}

      {notes && (
        <p className="text-xs text-muted-foreground">
          {searching ? `找到 ${notes.length} 条` : `最近的 ${notes.length} 条笔记`}
        </p>
      )}
      {notes?.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {searching
            ? "没有匹配的笔记。搜索是按字面匹配的，换个词试试，或者到「提问」页用一句话来问。"
            : "知识库还是空的。到「录入」页粘贴一张截图，添加第一条笔记。"}
        </p>
      )}
      {notes?.map((note) => (
        <NoteCard key={note.path} note={note} onOpen={onOpenNote} />
      ))}
    </section>
  )
}
