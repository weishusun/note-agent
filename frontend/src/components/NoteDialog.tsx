import { useEffect, useState } from "react"
import { LockIcon, XIcon } from "lucide-react"

import { Alert } from "@/components/Alert"
import { CategoryPicker } from "@/components/CategoryPicker"
import { NoteBody } from "@/components/NoteBody"
import { Button } from "@/components/ui/button"
import { api, categoryLabel, type NoteDetail, type NoteTarget, type Tree } from "@/lib/api"

type Loaded = { path: string; detail: NoteDetail } | { error: string }

export function NoteDialog({
  target,
  onOpenLink,
  onChanged,
  onClose,
}: {
  target: NoteTarget
  onOpenLink: (stem: string) => void
  // 这条笔记的分类被改了：让目录树和列表重新加载
  onChanged: () => void
  onClose: () => void
}) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [editing, setEditing] = useState(false)
  const [tree, setTree] = useState<Tree | null>(null)
  const [saving, setSaving] = useState(false)
  const [moveError, setMoveError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoaded(null)
    setEditing(false)
    setMoveError(null)
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

  const current = loaded && "detail" in loaded ? loaded : null
  const frontmatter = current?.detail.frontmatter

  function startEditing() {
    setEditing(true)
    setMoveError(null)
    if (!tree) api.tree().then(setTree, () => undefined)
  }

  async function move(category: string) {
    if (!current || !frontmatter?.id) return
    setSaving(true)
    setMoveError(null)
    try {
      await api.moveNote(frontmatter.id, category)
      setLoaded({ path: current.path, detail: await api.note(current.path) })
      setEditing(false)
      setTree(null)
      onChanged()
    } catch (e) {
      setMoveError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

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
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <h2 className="text-base font-semibold">{frontmatter?.title ?? target.title}</h2>
            {frontmatter && (
              <p className="text-sm text-muted-foreground">{frontmatter.summary}</p>
            )}
            {frontmatter && !editing && (
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                {frontmatter.type === "topic" && (
                  <span className="rounded bg-muted px-1.5 py-0.5">主题笔记</span>
                )}
                {frontmatter.status === "inbox" && (
                  <span className="rounded bg-muted px-1.5 py-0.5 font-medium text-foreground">
                    待整理
                  </span>
                )}
                <span className="flex items-center gap-1">
                  {frontmatter.category
                    ? (frontmatter.status === "inbox" ? "可能属于 " : "") +
                      categoryLabel(frontmatter.category)
                    : "还没有分类"}
                  {frontmatter.category_locked && (
                    <LockIcon className="size-3" aria-label="你手动指定的分类" />
                  )}
                </span>
                {frontmatter.id && (
                  <>
                    {frontmatter.status === "inbox" && frontmatter.category && (
                      <Button
                        size="xs"
                        variant="outline"
                        disabled={saving}
                        onClick={() => void move(frontmatter.category!)}
                      >
                        确认
                      </Button>
                    )}
                    <Button size="xs" variant="ghost" disabled={saving} onClick={startEditing}>
                      改分类
                    </Button>
                  </>
                )}
                <span>{frontmatter.updated.slice(0, 10)}</span>
                {frontmatter.aliases.length > 0 && (
                  <span>别名：{frontmatter.aliases.join("、")}</span>
                )}
              </div>
            )}
            {frontmatter && editing && (
              <CategoryPicker
                tree={tree}
                initial={frontmatter.category}
                busy={saving}
                onSubmit={(category) => void move(category)}
                onCancel={() => setEditing(false)}
              />
            )}
            {moveError && <Alert tone="error">{moveError}</Alert>}
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
          {current && <NoteBody body={current.detail.body} onOpenLink={onOpenLink} />}
        </div>
      </div>
    </div>
  )
}
