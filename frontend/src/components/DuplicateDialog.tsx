import { useEffect, useState } from "react"
import { LockIcon, XIcon } from "lucide-react"

import { Alert } from "@/components/Alert"
import { NoteBody } from "@/components/NoteBody"
import { Button } from "@/components/ui/button"
import { api, categoryLabel, type NoteDetail, type NoteRef } from "@/lib/api"

interface Side {
  ref: NoteRef
  detail: NoteDetail
}

type Loaded = { existing: Side; incoming: Side } | { error: string }
// 三种会删掉一条笔记的处理，点了之后要再确认一次；「两条都留」不删东西，直接生效
type Choice = "existing" | "incoming" | "merge"

const CONFIRM: Record<Choice, { text: string; action: string; busy: string }> = {
  existing: {
    text: "只留已有的那条，删除新录入的这条。别的笔记里指向它的链接会改指留下的那条。删除后不能在应用里撤销。",
    action: "确认，保留已有的",
    busy: "处理中…",
  },
  incoming: {
    text: "只留新录入的这条，删除已有的那条。别的笔记里指向它的链接会改指留下的那条。删除后不能在应用里撤销。",
    action: "确认，保留新的",
    busy: "处理中…",
  },
  merge: {
    text: "把新录入这条的内容并进已有的那条（重复的要点由模型去掉），然后删除新录入的这条。合并后不能在应用里撤销。",
    action: "确认合并",
    busy: "合并中…",
  },
}

// 并排展示一条疑似重复的笔记和它撞上的那条已有笔记，由使用者决定怎么处理（SCHEMA 2.5）
export function DuplicateDialog({
  noteId,
  remaining,
  onResolved,
  onClose,
}: {
  // 带着疑似重复标记的那条（后录入的）
  noteId: string
  // 这一条之后还排着几条等处理
  remaining: number
  // 这一条处理完了（或者已经不需要处理）：刷新列表，轮到下一条
  onResolved: () => void
  onClose: () => void
}) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [choice, setChoice] = useState<Choice | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoaded(null)
    setChoice(null)
    setError(null)
    void (async () => {
      try {
        const incoming = await api.ref(noteId)
        if (!incoming.duplicate_of) {
          // 已经在别处处理过了
          if (!cancelled) onResolved()
          return
        }
        const existing = await api.ref(incoming.duplicate_of)
        const [existingDetail, incomingDetail] = await Promise.all([
          api.note(existing.path),
          api.note(incoming.path),
        ])
        if (cancelled) return
        setLoaded({
          existing: { ref: existing, detail: existingDetail },
          incoming: { ref: incoming, detail: incomingDetail },
        })
      } catch (e) {
        if (!cancelled) setLoaded({ error: (e as Error).message })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [noteId, onResolved])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose()
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [busy, onClose])

  const pair = loaded && "existing" in loaded ? loaded : null

  async function run(action: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await action()
      onResolved()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  function confirm() {
    if (!pair || !choice) return
    const oldId = pair.existing.ref.id
    const newId = pair.incoming.ref.id
    if (!oldId || !newId) return
    if (choice === "existing") void run(() => api.deleteNote(newId, oldId))
    else if (choice === "incoming") void run(() => api.deleteNote(oldId, newId))
    else void run(() => api.mergeNote(newId, oldId))
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={() => !busy && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="疑似重复"
        className="flex max-h-[92vh] w-full max-w-5xl flex-col rounded-xl border bg-background shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start gap-3 border-b p-4">
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <h2 className="text-base font-semibold">
              疑似重复
              {remaining > 0 && (
                <span className="ml-2 text-xs font-normal text-muted-foreground">
                  后面还有 {remaining} 条
                </span>
              )}
            </h2>
            <p className="text-sm text-muted-foreground">
              新录入的笔记和知识库里已有的一条同名。下面是它们各自的内容，由你决定怎么处理。
            </p>
          </div>
          <Button variant="ghost" size="icon-sm" aria-label="关闭" disabled={busy} onClick={onClose}>
            <XIcon />
          </Button>
        </header>

        {!loaded && <p className="p-4 text-sm text-muted-foreground">加载中…</p>}
        {loaded && "error" in loaded && (
          <div className="flex items-center gap-3 p-4">
            <p className="flex-1 text-sm text-destructive">{loaded.error}</p>
            {remaining > 0 && (
              <Button size="sm" variant="outline" onClick={onResolved}>
                跳过，看下一条
              </Button>
            )}
          </div>
        )}

        {pair && (
          <>
            {/* 两栏一起滚动，方便逐段对照 */}
            <div className="grid min-h-0 flex-1 gap-px overflow-y-auto bg-border md:grid-cols-2">
              <Column label="已有的笔记" side={pair.existing} />
              <Column label="新录入的笔记" side={pair.incoming} />
            </div>

            <footer className="flex flex-col gap-3 border-t p-4">
              {error && <Alert tone="error">{error}</Alert>}
              {choice ? (
                <Alert tone="info">
                  <span className="flex-1">{CONFIRM[choice].text}</span>
                  <Button size="sm" disabled={busy} onClick={confirm}>
                    {busy ? CONFIRM[choice].busy : CONFIRM[choice].action}
                  </Button>
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => setChoice(null)}>
                    取消
                  </Button>
                </Alert>
              ) : (
                <div className="flex flex-wrap items-center gap-2">
                  <Button variant="outline" disabled={busy} onClick={() => setChoice("existing")}>
                    保留已有的
                  </Button>
                  <Button variant="outline" disabled={busy} onClick={() => setChoice("incoming")}>
                    保留新的
                  </Button>
                  <Button
                    variant="outline"
                    disabled={busy}
                    title="它们其实是两个不同的概念：都留着，不再提示"
                    onClick={() => {
                      const id = pair.incoming.ref.id
                      if (id) void run(() => api.keepNote(id))
                    }}
                  >
                    两条都留
                  </Button>
                  <Button variant="outline" disabled={busy} onClick={() => setChoice("merge")}>
                    合并成一条
                  </Button>
                  <span className="text-xs text-muted-foreground">也可以先关掉，以后在笔记页处理</span>
                </div>
              )}
            </footer>
          </>
        )}
      </div>
    </div>
  )
}

function Column({ label, side }: { label: string; side: Side }) {
  const { frontmatter, body } = side.detail
  return (
    <section aria-label={label} className="flex min-w-0 flex-col gap-2 bg-background p-4">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <h3 className="text-base font-semibold">{frontmatter.title}</h3>
      <p className="text-sm text-muted-foreground">{frontmatter.summary}</p>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span className="flex items-center gap-1">
          {frontmatter.category
            ? (frontmatter.status === "inbox" ? "待整理 · 可能属于 " : "") +
              categoryLabel(frontmatter.category)
            : "还没有分类"}
          {frontmatter.category_locked && (
            <LockIcon className="size-3" aria-label="你手动指定的分类" />
          )}
        </span>
        <span>{frontmatter.updated.slice(0, 10)}</span>
        {frontmatter.aliases.length > 0 && <span>别名：{frontmatter.aliases.join("、")}</span>}
      </div>
      <div className="mt-2 border-t pt-3">
        <NoteBody body={body} />
      </div>
    </section>
  )
}
