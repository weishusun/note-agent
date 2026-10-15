import { useEffect, useState } from "react"
import { FolderInputIcon, LockIcon, PencilLineIcon, SparklesIcon, Trash2Icon, XIcon } from "lucide-react"

import { Alert } from "@/components/Alert"
import { CategoryPicker } from "@/components/CategoryPicker"
import { NoteBody } from "@/components/NoteBody"
import { NoteForm, type NoteFormValues } from "@/components/NoteForm"
import { Button } from "@/components/ui/button"
import { api, categoryLabel, type NoteDetail, type NoteTarget, type Tree } from "@/lib/api"
import { cn } from "@/lib/utils"

type Loaded = { path: string; detail: NoteDetail } | { error: string }
// view 查看；category 改分类；edit 编辑内容；delete 等确认删除
type Mode = "view" | "category" | "edit" | "delete"

const SOURCE_LABEL: Record<string, string> = {
  screenshot: "截图",
  text: "文本",
  file: "文件",
  url: "网页",
  manual: "手写",
  generated: "生成",
}

// 读一条笔记，并在原地编辑、改分类、删除。笔记页右栏和别处弹出的侧边面板共用它
export function NoteReader({
  target,
  onOpenLink,
  onChanged,
  onCompare,
  onDeleted,
  onClose,
  onEditingChange,
}: {
  target: NoteTarget
  onOpenLink: (stem: string) => void
  // 这条笔记被改动或删除了：让目录树和列表重新加载
  onChanged: () => void
  // 这条笔记疑似重复时，去并排对比它和撞上的那条
  onCompare: (ids: string[]) => void
  onDeleted: () => void
  // 给了就在右上角显示关闭按钮（侧边面板用）
  onClose?: () => void
  // 进出编辑状态时通知外面：编辑到一半时侧边面板不该被 Esc 或点空白处关掉
  onEditingChange?: (editing: boolean) => void
}) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [duplicateTitle, setDuplicateTitle] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>("view")

  useEffect(() => {
    onEditingChange?.(mode === "edit")
  }, [mode, onEditingChange])
  const [tree, setTree] = useState<Tree | null>(null)
  const [busy, setBusy] = useState(false)
  const [supplementing, setSupplementing] = useState(false)
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

  const current = loaded && "detail" in loaded ? loaded : null
  const frontmatter = current?.detail.frontmatter
  const noteId = frontmatter?.id ?? null
  const duplicateOf = frontmatter?.duplicate_of ?? null

  useEffect(() => {
    setDuplicateTitle(null)
    if (!duplicateOf) return
    let cancelled = false
    api.ref(duplicateOf).then(
      (ref) => !cancelled && setDuplicateTitle(ref.title),
      () => undefined,
    )
    return () => {
      cancelled = true
    }
  }, [duplicateOf])

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
      onDeleted()
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  const save = (values: NoteFormValues) => noteId && void apply(() => api.editNote(noteId, values))
  // 模型补充：让模型（重新）写一段，整段换掉原来的
  async function supplement() {
    if (!noteId) return
    setSupplementing(true)
    await apply(() => api.supplementNote(noteId))
    setSupplementing(false)
  }
  const hasSupplement = current !== null && /^## 模型补充\s*$/m.test(current.detail.body)
  const move = (category: string) => noteId && void apply(() => api.moveNote(noteId, category))

  return (
    <article
      aria-label={frontmatter?.title ?? target.title}
      className="relative mx-auto w-full max-w-3xl px-8 pt-9 pb-16 sm:px-12"
    >
      <div className="absolute top-6 right-6 flex gap-0.5">
        {frontmatter && mode === "view" && noteId && (
          <>
            <ToolButton label="编辑" onClick={() => enter("edit")} disabled={busy}>
              <PencilLineIcon />
            </ToolButton>
            <ToolButton
              label={hasSupplement ? "重新写模型补充" : "让模型补充"}
              onClick={() => void supplement()}
              disabled={busy}
            >
              <SparklesIcon />
            </ToolButton>
            <ToolButton label="改分类" onClick={() => enter("category")} disabled={busy}>
              <FolderInputIcon />
            </ToolButton>
            <ToolButton label="删除" onClick={() => enter("delete")} disabled={busy}>
              <Trash2Icon />
            </ToolButton>
          </>
        )}
        {onClose && (
          <ToolButton label="关闭" onClick={onClose} disabled={busy}>
            <XIcon />
          </ToolButton>
        )}
      </div>

      {!loaded && <p className="text-sm text-muted-foreground">加载中…</p>}
      {loaded && "error" in loaded && <p className="text-sm text-destructive">{loaded.error}</p>}

      {frontmatter && mode === "edit" && current && (
        <>
          <h1 className="mb-6 font-heading text-2xl">编辑笔记</h1>
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
        </>
      )}

      {frontmatter && current && mode !== "edit" && (
        <>
          <div className="flex items-center gap-1.5 pr-28 text-xs text-muted-foreground">
            {frontmatter.category ? (
              <>
                {frontmatter.status === "inbox" && "可能属于 "}
                {categoryLabel(frontmatter.category)}
              </>
            ) : (
              "还没有分类"
            )}
            {frontmatter.category_locked && (
              <LockIcon className="size-3" aria-label="你手动指定的分类" />
            )}
          </div>
          <h1 className="mt-2.5 mb-3 pr-10 font-heading text-[2rem] leading-tight tracking-wide">
            {frontmatter.title}
          </h1>
          <p className="mb-6 border-l-2 border-input pl-3.5 text-[15px] text-muted-foreground">
            {frontmatter.summary}
          </p>
          <div className="mb-7 flex flex-wrap gap-x-5 gap-y-1 border-b pb-4 text-xs text-muted-foreground/80">
            {frontmatter.type === "topic" && <Meta label="类型" value="主题笔记" />}
            <Meta
              label="来源"
              value={SOURCE_LABEL[frontmatter.source.kind] ?? frontmatter.source.kind}
            />
            <Meta label="更新" value={frontmatter.updated.slice(0, 10)} />
            {frontmatter.aliases.length > 0 && (
              <Meta label="别名" value={frontmatter.aliases.join("、")} />
            )}
          </div>

          <div className="mb-6 flex flex-col gap-3 empty:hidden">
            {mode === "view" && frontmatter.status === "inbox" && noteId && (
              <Alert tone="info">
                <span className="flex-1">
                  {frontmatter.category
                    ? `模型拿不准该放哪，猜的是「${categoryLabel(frontmatter.category)}」。`
                    : "这条笔记还没有分类。"}
                </span>
                {frontmatter.category && (
                  <Button size="sm" disabled={busy} onClick={() => move(frontmatter.category!)}>
                    确认分类
                  </Button>
                )}
                <Button size="sm" variant="outline" disabled={busy} onClick={() => enter("category")}>
                  改到别处
                </Button>
              </Alert>
            )}
            {mode === "view" && noteId && duplicateOf && (
              <Alert tone="info">
                <span className="flex-1">
                  和知识库里已有的笔记{duplicateTitle ? `「${duplicateTitle}」` : ""}疑似重复。
                </span>
                <Button size="sm" disabled={busy} onClick={() => onCompare([noteId])}>
                  对比并处理
                </Button>
              </Alert>
            )}
            {mode === "category" && (
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
            {supplementing && (
              <Alert tone="info">
                正在让模型{hasSupplement ? "重新" : ""}写补充，大约要 10–30 秒。写好后会整段换掉原来的模型补充。
              </Alert>
            )}
            {error && <Alert tone="error">{error}</Alert>}
          </div>

          <NoteBody body={current.detail.body} onOpenLink={onOpenLink} />
        </>
      )}
    </article>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <span>
      {label} <span className="font-medium text-muted-foreground">{value}</span>
    </span>
  )
}

function ToolButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string
  disabled?: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className={cn("text-muted-foreground hover:text-foreground")}
    >
      {children}
    </Button>
  )
}
