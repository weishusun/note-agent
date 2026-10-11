import { useEffect, useState } from "react"
import { LockIcon, XIcon } from "lucide-react"

import { Alert } from "@/components/Alert"
import { CategoryPicker } from "@/components/CategoryPicker"
import { NoteBody } from "@/components/NoteBody"
import { NoteForm, type NoteFormValues } from "@/components/NoteForm"
import { Button } from "@/components/ui/button"
import { api, categoryLabel, type NoteDetail, type NoteTarget, type Tree } from "@/lib/api"

type Loaded = { path: string; detail: NoteDetail } | { error: string }
// view 查看；category 改分类；edit 编辑内容；delete 等确认删除
type Mode = "view" | "category" | "edit" | "delete"

export function NoteDialog({
  target,
  onOpenLink,
  onChanged,
  onClose,
}: {
  target: NoteTarget
  onOpenLink: (stem: string) => void
  // 这条笔记被改动或删除了：让目录树和列表重新加载
  onChanged: () => void
  onClose: () => void
}) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [mode, setMode] = useState<Mode>("view")
  const [tree, setTree] = useState<Tree | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoaded(null)
    setMode("view")
    setError(null)
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
    // 正在编辑时 Esc 不关窗，免得一按就丢了改到一半的内容
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && mode !== "edit" && onClose()
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [mode, onClose])

  const current = loaded && "detail" in loaded ? loaded : null
  const frontmatter = current?.detail.frontmatter
  const noteId = frontmatter?.id ?? null

  function enter(next: Mode) {
    setMode(next)
    setError(null)
    if (next === "category" && !tree) api.tree().then(setTree, () => undefined)
  }

  // 做一次改动：成功后按返回的路径重新加载这条笔记（改标题会换路径）
  async function apply(action: () => Promise<{ path: string }>) {
    setBusy(true)
    setError(null)
    try {
      const { path } = await action()
      setLoaded({ path, detail: await api.note(path) })
      setMode("view")
      setTree(null)
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!noteId) return
    setBusy(true)
    setError(null)
    try {
      await api.deleteNote(noteId)
      onChanged()
      onClose()
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  const save = (values: NoteFormValues) => noteId && void apply(() => api.editNote(noteId, values))
  const move = (category: string) => noteId && void apply(() => api.moveNote(noteId, category))

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={() => mode !== "edit" && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={frontmatter?.title ?? target.title}
        className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-xl border bg-background shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start gap-3 border-b p-4">
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <h2 className="text-base font-semibold">
              {mode === "edit" ? "编辑笔记" : (frontmatter?.title ?? target.title)}
            </h2>
            {frontmatter && mode !== "edit" && (
              <p className="text-sm text-muted-foreground">{frontmatter.summary}</p>
            )}
            {frontmatter && (mode === "view" || mode === "delete") && (
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
                <span>{frontmatter.updated.slice(0, 10)}</span>
                {frontmatter.aliases.length > 0 && (
                  <span>别名：{frontmatter.aliases.join("、")}</span>
                )}
              </div>
            )}
            {frontmatter && mode === "view" && noteId && (
              <div className="flex flex-wrap gap-1.5">
                {frontmatter.status === "inbox" && frontmatter.category && (
                  <Button
                    size="xs"
                    variant="outline"
                    disabled={busy}
                    onClick={() => move(frontmatter.category!)}
                  >
                    确认分类
                  </Button>
                )}
                <Button size="xs" variant="outline" disabled={busy} onClick={() => enter("category")}>
                  改分类
                </Button>
                <Button size="xs" variant="outline" disabled={busy} onClick={() => enter("edit")}>
                  编辑
                </Button>
                <Button size="xs" variant="outline" disabled={busy} onClick={() => enter("delete")}>
                  删除
                </Button>
              </div>
            )}
            {frontmatter && mode === "category" && (
              <CategoryPicker
                tree={tree}
                initial={frontmatter.category}
                busy={busy}
                onSubmit={move}
                onCancel={() => enter("view")}
              />
            )}
            {mode === "delete" && (
              <Alert tone="error">
                <span className="flex-1">
                  删除后不能在应用里撤销。别的笔记里指向它的链接会变成普通文字。
                </span>
                <Button size="sm" variant="destructive" disabled={busy} onClick={() => void remove()}>
                  {busy ? "删除中…" : "确认删除"}
                </Button>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => enter("view")}>
                  取消
                </Button>
              </Alert>
            )}
            {error && mode !== "edit" && <Alert tone="error">{error}</Alert>}
          </div>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="关闭"
            disabled={busy}
            onClick={onClose}
          >
            <XIcon />
          </Button>
        </header>
        <div className="overflow-y-auto p-4">
          {!loaded && <p className="text-sm text-muted-foreground">加载中…</p>}
          {loaded && "error" in loaded && (
            <p className="text-sm text-destructive">{loaded.error}</p>
          )}
          {current && frontmatter && mode === "edit" && (
            <NoteForm
              initial={{
                title: frontmatter.title,
                summary: frontmatter.summary,
                aliases: frontmatter.aliases,
                body: current.detail.body.trim() + "\n",
              }}
              submitLabel="保存"
              busy={busy}
              error={error}
              onSubmit={save}
              onCancel={() => enter("view")}
            />
          )}
          {current && mode !== "edit" && (
            <NoteBody body={current.detail.body} onOpenLink={onOpenLink} />
          )}
        </div>
      </div>
    </div>
  )
}
