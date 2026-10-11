import { useEffect, useId, useState } from "react"
import { XIcon } from "lucide-react"

import { NoteForm, type NoteFormValues } from "@/components/NoteForm"
import { Button } from "@/components/ui/button"
import { api, type NoteRef, type Tree } from "@/lib/api"

const INPUT =
  "h-9 min-w-0 flex-1 rounded-lg border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
const EMPTY: NoteFormValues = { title: "", summary: "", aliases: [], body: "## 要点\n- \n" }

// 手写新建一条笔记。分类两格都留空 = 交给模型自动归类
export function NewNoteDialog({
  tree,
  initialCategory,
  onCreated,
  onClose,
}: {
  tree: Tree | null
  // 从某个分类里点的「新建」时，预填这个分类
  initialCategory: string | null
  onCreated: (note: NoteRef) => void
  onClose: () => void
}) {
  const [parent, setParent] = useState(initialCategory?.split("/")[0] ?? "")
  const [child, setChild] = useState(initialCategory?.split("/")[1] ?? "")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const parentList = useId()
  const childList = useId()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose()
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [busy, onClose])

  const siblings =
    tree?.categories.find((p) => p.name.toLowerCase() === parent.trim().toLowerCase())?.children ??
    []

  async function create(values: NoteFormValues) {
    const p = parent.trim()
    const c = child.trim()
    if ((p === "") !== (c === "")) {
      setError("分类要么大块和小块都填，要么都留空（留空则自动归类）")
      return
    }
    setBusy(true)
    setError(null)
    try {
      onCreated(await api.createNote({ ...values, category: p ? `${p}/${c}` : undefined }))
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="新建笔记"
        className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-xl border bg-background shadow-lg"
      >
        <header className="flex items-center gap-3 border-b p-4">
          <h2 className="flex-1 text-base font-semibold">新建笔记</h2>
          <Button variant="ghost" size="icon-sm" aria-label="关闭" disabled={busy} onClick={onClose}>
            <XIcon />
          </Button>
        </header>
        <div className="overflow-y-auto p-4">
          <NoteForm
            initial={EMPTY}
            submitLabel="新建"
            busy={busy}
            error={error}
            onSubmit={(values) => void create(values)}
            onCancel={onClose}
            extra={
              <div className="flex flex-col gap-1.5">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-sm font-medium">分类</span>
                  <span className="text-xs text-muted-foreground">两格都留空则由模型自动归类</span>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    className={INPUT}
                    aria-label="大块"
                    placeholder="大块"
                    list={parentList}
                    value={parent}
                    maxLength={12}
                    onChange={(e) => setParent(e.target.value)}
                  />
                  <datalist id={parentList}>
                    {tree?.categories.map((p) => <option key={p.name} value={p.name} />)}
                  </datalist>
                  <span className="text-muted-foreground">›</span>
                  <input
                    className={INPUT}
                    aria-label="小块"
                    placeholder="小块"
                    list={childList}
                    value={child}
                    maxLength={12}
                    onChange={(e) => setChild(e.target.value)}
                  />
                  <datalist id={childList}>
                    {siblings.map((c) => (
                      <option key={c.name} value={c.name} />
                    ))}
                  </datalist>
                </div>
              </div>
            }
          />
        </div>
      </div>
    </div>
  )
}
