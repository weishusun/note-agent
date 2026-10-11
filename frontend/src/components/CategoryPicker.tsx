import { useId, useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import type { Tree } from "@/lib/api"

const INPUT =
  "h-8 min-w-0 flex-1 rounded-lg border bg-background px-2 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"

// 给一条笔记选「大块 / 小块」：可以从已有的里选，也可以直接输入新的名字
export function CategoryPicker({
  tree,
  initial,
  busy,
  onSubmit,
  onCancel,
}: {
  tree: Tree | null
  initial: string | null
  busy: boolean
  onSubmit: (category: string) => void
  onCancel: () => void
}) {
  const [parent, setParent] = useState(initial?.split("/")[0] ?? "")
  const [child, setChild] = useState(initial?.split("/")[1] ?? "")
  const parentList = useId()
  const childList = useId()

  const siblings =
    tree?.categories.find((p) => p.name.toLowerCase() === parent.trim().toLowerCase())?.children ??
    []
  const ready = parent.trim() !== "" && child.trim() !== ""

  function submit(e: FormEvent) {
    e.preventDefault()
    if (ready) onSubmit(`${parent.trim()}/${child.trim()}`)
  }

  return (
    <form onSubmit={submit} className="flex flex-wrap items-center gap-2">
      <input
        className={INPUT}
        aria-label="大块"
        placeholder="大块，如 人工智能"
        list={parentList}
        value={parent}
        maxLength={12}
        autoFocus
        onChange={(e) => setParent(e.target.value)}
      />
      <datalist id={parentList}>
        {tree?.categories.map((p) => <option key={p.name} value={p.name} />)}
      </datalist>
      <span className="text-muted-foreground">›</span>
      <input
        className={INPUT}
        aria-label="小块"
        placeholder="小块，如 模型训练"
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
      <Button type="submit" size="sm" disabled={!ready || busy}>
        {busy ? "保存中…" : "保存"}
      </Button>
      <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
        取消
      </Button>
    </form>
  )
}
