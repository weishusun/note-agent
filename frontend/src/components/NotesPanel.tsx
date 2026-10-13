import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react"
import {
  ChevronDownIcon,
  ChevronRightIcon,
  FilePlusIcon,
  LockIcon,
  PlusIcon,
  RefreshCwIcon,
  ShuffleIcon,
} from "lucide-react"

import { Alert } from "@/components/Alert"
import { GraphView } from "@/components/GraphView"
import { NewNoteDialog } from "@/components/NewNoteDialog"
import { NoteCard } from "@/components/NoteCard"
import { Button } from "@/components/ui/button"
import { api, categoryLabel, type NoteRef, type Tree } from "@/lib/api"
import { cn } from "@/lib/utils"

// 右边怎么展示所选范围里的笔记
export type NotesView = "list" | "graph"

const VIEWS: { id: NotesView; label: string }[] = [
  { id: "list", label: "列表" },
  { id: "graph", label: "图谱" },
]

const SEARCH_DEBOUNCE_MS = 250
const JOB_POLL_MS = 600
const NAME_MAX = 12

type Selection =
  | { kind: "all" }
  | { kind: "inbox" }
  | { kind: "duplicates" }
  | { kind: "category"; path: string }
type Edit = { mode: "rename" | "create"; value: string }

const INPUT =
  "h-8 min-w-0 flex-1 rounded-lg border bg-background px-2 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"

export function NotesPanel({
  active,
  version,
  view,
  onViewChange,
  onOpenNote,
  onCompare,
}: {
  active: boolean
  // 别处改了笔记的分类（比如在笔记弹窗里）时递增，让这里重新加载
  version: number
  view: NotesView
  onViewChange: (view: NotesView) => void
  onOpenNote: (note: Pick<NoteRef, "title" | "path">) => void
  // 并排对比这些疑似重复的笔记，一条一条来
  onCompare: (ids: string[]) => void
}) {
  const [tree, setTree] = useState<Tree | null>(null)
  const [selection, setSelection] = useState<Selection>({ kind: "all" })
  const [query, setQuery] = useState("")
  const [notes, setNotes] = useState<NoteRef[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())
  const [edit, setEdit] = useState<Edit | null>(null)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const [confirmingAll, setConfirmingAll] = useState(false)
  const [creating, setCreating] = useState(false)

  const reload = useCallback(() => setReloadKey((k) => k + 1), [])

  useEffect(() => {
    if (active) reload()
  }, [active, version, reload])

  useEffect(() => {
    if (!active) return
    let cancelled = false
    api.tree().then(
      (t) => !cancelled && setTree(t),
      (e: Error) => !cancelled && setError(e.message),
    )
    return () => {
      cancelled = true
    }
  }, [active, reloadKey])

  // 待整理、疑似重复清空后那个入口会从目录里消失：别让右边停在一个已经选不到的视图上
  useEffect(() => {
    if (!tree) return
    if (tree.inbox === 0 && selection.kind === "inbox") setSelection({ kind: "all" })
    if (tree.duplicates === 0 && selection.kind === "duplicates") setSelection({ kind: "all" })
  }, [tree, selection.kind])

  const searching = query.trim().length > 0
  const selectedPath = selection.kind === "category" ? selection.path : null

  useEffect(() => {
    if (!active) return
    let cancelled = false
    const q = query.trim()
    const load = () => {
      const request = q
        ? api.search(q)
        : api.notes(
            selection.kind === "inbox"
              ? { status: "inbox" }
              : selection.kind === "duplicates"
                ? { duplicates: "true" }
                : selection.kind === "category"
                  ? { category: selection.path }
                  : {},
          )
      request.then(
        (result) => {
          if (cancelled) return
          setNotes(result)
          setError(null)
        },
        (e: Error) => !cancelled && setError(e.message),
      )
    }
    const timer = setTimeout(load, q ? SEARCH_DEBOUNCE_MS : 0)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
    // selection 是对象：用它的两个字段做依赖，避免每次渲染都重新请求
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, query, selection.kind, selectedPath, reloadKey])

  async function run(action: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
      reload()
    }
  }

  async function reclassify(scope: "unsorted" | "all") {
    setConfirmingAll(false)
    setError(null)
    setProgress({ done: 0, total: 0 })
    try {
      const { job_id } = await api.reclassify(scope)
      for (;;) {
        const state = await api.job(job_id)
        if (state.progress) setProgress(state.progress)
        if (state.status === "done") break
        if (state.status === "failed") throw new Error(state.error ?? "重新归类失败")
        await new Promise((resolve) => setTimeout(resolve, JOB_POLL_MS))
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setProgress(null)
      reload()
    }
  }

  function submitEdit(e: FormEvent) {
    e.preventDefault()
    if (!edit) return
    const value = edit.value.trim()
    if (!value) return
    if (edit.mode === "create") {
      void run(async () => {
        await api.createCategory(value)
        setSelection({ kind: "category", path: value })
        setEdit(null)
      })
    } else if (selectedPath) {
      const parent = selectedPath.includes("/") ? selectedPath.split("/")[0] : null
      void run(async () => {
        await api.renameCategory(selectedPath, value)
        setSelection({ kind: "category", path: parent ? `${parent}/${value}` : value })
        setEdit(null)
      })
    }
  }

  const selected = selectedPath ? findNode(tree, selectedPath) : null
  // 图谱只对「全部笔记」和某个分类有意义；搜索、待整理、疑似重复时只有列表
  const scoped = !searching && (selection.kind === "all" || selection.kind === "category")
  const shown: NotesView = scoped ? view : "list"
  const scopeCount = selectedPath ? (selected?.count ?? 0) : (tree?.total ?? 0)
  const reclassifying = progress !== null
  const disabled = busy || reclassifying

  return (
    <section className="grid gap-6 md:grid-cols-[15rem_1fr]">
      <nav aria-label="分类目录" className="flex flex-col gap-0.5 text-sm">
        <TreeRow
          label="全部笔记"
          count={tree?.total}
          selected={!searching && selection.kind === "all"}
          onSelect={() => setSelection({ kind: "all" })}
        />
        {!!tree?.inbox && (
          <TreeRow
            label="待整理"
            count={tree.inbox}
            selected={!searching && selection.kind === "inbox"}
            onSelect={() => setSelection({ kind: "inbox" })}
          />
        )}
        {!!tree?.duplicates && (
          <TreeRow
            label="疑似重复"
            count={tree.duplicates}
            selected={!searching && selection.kind === "duplicates"}
            onSelect={() => setSelection({ kind: "duplicates" })}
          />
        )}
        {tree?.categories.map((parent) => {
          const open = !collapsed.has(parent.name)
          return (
            <div key={parent.name} className="flex flex-col gap-0.5">
              <TreeRow
                label={parent.name}
                count={parent.count}
                locked={parent.locked}
                selected={!searching && selectedPath === parent.name}
                onSelect={() => setSelection({ kind: "category", path: parent.name })}
                toggle={
                  <button
                    type="button"
                    aria-label={open ? `收起 ${parent.name}` : `展开 ${parent.name}`}
                    aria-expanded={open}
                    className="rounded p-0.5 text-muted-foreground hover:bg-background"
                    onClick={(e) => {
                      e.stopPropagation()
                      const next = new Set(collapsed)
                      if (open) next.add(parent.name)
                      else next.delete(parent.name)
                      setCollapsed(next)
                    }}
                  >
                    {open ? (
                      <ChevronDownIcon className="size-3.5" />
                    ) : (
                      <ChevronRightIcon className="size-3.5" />
                    )}
                  </button>
                }
              />
              {open &&
                parent.children.map((child) => (
                  <TreeRow
                    key={child.path}
                    indent
                    label={child.name}
                    count={child.count}
                    locked={child.locked}
                    selected={!searching && selectedPath === child.path}
                    onSelect={() => setSelection({ kind: "category", path: child.path })}
                  />
                ))}
            </div>
          )
        })}
        {tree && tree.categories.length === 0 && (
          <p className="px-2 py-3 text-xs text-muted-foreground">
            还没有分类。录入笔记时会自动生成。
          </p>
        )}

        {edit?.mode === "create" ? (
          <form onSubmit={submitEdit} className="mt-2 flex flex-col gap-1.5">
            <input
              className={INPUT + " flex-none"}
              aria-label="新分类的路径"
              placeholder="大块，或 大块/小块"
              value={edit.value}
              maxLength={NAME_MAX * 2 + 1}
              autoFocus
              onChange={(e) => setEdit({ mode: "create", value: e.target.value })}
            />
            <div className="flex gap-1.5">
              <Button type="submit" size="sm" disabled={disabled || !edit.value.trim()}>
                新建
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setEdit(null)}>
                取消
              </Button>
            </div>
          </form>
        ) : (
          <Button
            variant="ghost"
            size="sm"
            className="mt-2 justify-start text-muted-foreground"
            disabled={disabled}
            onClick={() => setEdit({ mode: "create", value: "" })}
          >
            <PlusIcon />
            新建分类
          </Button>
        )}
      </nav>

      <div className="flex min-w-0 flex-col gap-4">
        <div className="flex flex-wrap gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="搜索笔记"
            placeholder="搜索标题、别名或正文"
            className="h-9 min-w-40 flex-1 rounded-lg border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          />
          <Button
            variant="outline"
            size="lg"
            disabled={disabled}
            onClick={() => setCreating(true)}
            title="自己写一条笔记"
          >
            <FilePlusIcon />
            新建笔记
          </Button>
          <Button
            variant="outline"
            size="lg"
            disabled={disabled}
            onClick={() => setConfirmingAll(true)}
            title="让模型把所有未锁定的笔记重新分一遍类"
          >
            <ShuffleIcon />
            全部重排
          </Button>
          <Button
            variant="outline"
            size="lg"
            disabled={disabled}
            onClick={() => void run(() => api.reindex())}
            title="在应用之外改过笔记文件后，点这里让列表和检索跟上"
          >
            <RefreshCwIcon className={busy ? "animate-spin" : undefined} />
            同步
          </Button>
        </div>

        {confirmingAll && (
          <Alert tone="info">
            <span className="flex-1">
              模型会重新设计分类并给所有笔记重新归类，目录可能大变样。你手动放置过的笔记、建过或改过名的分类不受影响。
            </span>
            <Button size="sm" onClick={() => void reclassify("all")}>
              开始重排
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmingAll(false)}>
              取消
            </Button>
          </Alert>
        )}
        {progress && (
          <Alert tone="info">
            正在归类
            {progress.total > 0 ? ` ${progress.done} / ${progress.total}` : "…"}
          </Alert>
        )}
        {error && <Alert tone="error">{error}</Alert>}

        <div className="flex min-h-8 flex-wrap items-center gap-2">
          {searching ? (
            <h2 className="text-sm font-semibold">搜索结果</h2>
          ) : edit?.mode === "rename" && selectedPath ? (
            <form onSubmit={submitEdit} className="flex flex-1 items-center gap-2">
              <input
                className={INPUT}
                aria-label="分类的新名字"
                value={edit.value}
                maxLength={NAME_MAX}
                autoFocus
                onChange={(e) => setEdit({ mode: "rename", value: e.target.value })}
              />
              <Button type="submit" size="sm" disabled={disabled || !edit.value.trim()}>
                保存
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setEdit(null)}>
                取消
              </Button>
            </form>
          ) : (
            <>
              <h2 className="flex items-center gap-1.5 text-sm font-semibold">
                {selection.kind === "all" && "全部笔记"}
                {selection.kind === "inbox" && "待整理"}
                {selection.kind === "duplicates" && "疑似重复"}
                {selectedPath && categoryLabel(selectedPath)}
                {selected?.locked && (
                  <LockIcon
                    className="size-3.5 text-muted-foreground"
                    aria-label="你建的或改过名的分类，模型不会改动它"
                  />
                )}
              </h2>
              <span className="flex-1 text-xs text-muted-foreground">
                {notes ? `${notes.length} 条` : ""}
              </span>
              {selectedPath && (
                <>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={disabled}
                    onClick={() =>
                      setEdit({ mode: "rename", value: selectedPath.split("/").pop() ?? "" })
                    }
                  >
                    改名
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={disabled || !selected || selected.count > 0 || notes?.length !== 0}
                    title="只能删除没有笔记的分类"
                    onClick={() =>
                      void run(async () => {
                        await api.deleteCategory(selectedPath)
                        setSelection({ kind: "all" })
                      })
                    }
                  >
                    删除
                  </Button>
                </>
              )}
              {scoped && (
                <div role="tablist" aria-label="展示方式" className="flex gap-0.5 rounded-lg bg-muted p-0.5">
                  {VIEWS.map((v) => (
                    <button
                      key={v.id}
                      type="button"
                      role="tab"
                      aria-selected={shown === v.id}
                      onClick={() => onViewChange(v.id)}
                      className={cn(
                        "rounded-md px-2.5 py-0.5 text-xs font-medium text-muted-foreground transition-colors focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
                        shown === v.id && "bg-background text-foreground shadow-sm",
                      )}
                    >
                      {v.label}
                    </button>
                  ))}
                </div>
              )}
              {selection.kind === "duplicates" && !!notes?.length && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={disabled}
                  onClick={() => onCompare(notes.flatMap((n) => (n.id ? [n.id] : [])))}
                >
                  逐条对比
                </Button>
              )}
              {selection.kind === "inbox" && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={disabled}
                  onClick={() => void reclassify("unsorted")}
                >
                  让模型再归类一次
                </Button>
              )}
            </>
          )}
        </div>
        {edit?.mode === "rename" && (
          <p className="-mt-2 text-xs text-muted-foreground">
            改成同一层里已有的名字，就是把这个分类并进去。
          </p>
        )}
        {!searching && selection.kind === "inbox" && (
          <p className="-mt-2 text-xs text-muted-foreground">
            这些笔记模型拿不准该放哪。点开一条可以确认它猜的分类，或者改到别处。
          </p>
        )}

        {!searching && selection.kind === "duplicates" && (
          <p className="-mt-2 text-xs text-muted-foreground">
            这些笔记和知识库里更早的某条同名。点开一条，并排看两边的内容，再决定留哪条、都留还是合并。
          </p>
        )}

        {shown === "graph" && (
          <GraphView
            category={selectedPath}
            count={scopeCount}
            version={reloadKey}
            onOpenNote={onOpenNote}
          />
        )}
        {shown === "list" && notes?.length === 0 && (
          <p className="text-sm text-muted-foreground">
            {searching
              ? "没有匹配的笔记。搜索是按字面匹配的，换个词试试，或者到「提问」页用一句话来问。"
              : selection.kind === "all"
                ? "知识库还是空的。到「录入」页粘贴一张截图，添加第一条笔记。"
                : "这里还没有笔记。"}
          </p>
        )}
        {shown === "list" &&
          notes?.map((note) => (
            <NoteCard
              key={note.path}
              note={note}
              onOpen={
                // 在「疑似重复」里点开就是要处理它：直接进并排对比
                !searching && selection.kind === "duplicates" && note.id
                  ? () => onCompare([note.id!])
                  : onOpenNote
              }
            />
          ))}
      </div>

      {creating && (
        <NewNoteDialog
          tree={tree}
          initialCategory={selectedPath?.includes("/") ? selectedPath : null}
          onClose={() => setCreating(false)}
          onCreated={(note) => {
            setCreating(false)
            reload()
            onOpenNote(note)
          }}
        />
      )}
    </section>
  )
}

function findNode(tree: Tree | null, path: string): { locked: boolean; count: number } | null {
  for (const parent of tree?.categories ?? []) {
    if (parent.name === path) return parent
    const child = parent.children.find((c) => c.path === path)
    if (child) return child
  }
  return null
}

function TreeRow({
  label,
  count,
  locked,
  selected,
  indent,
  toggle,
  onSelect,
}: {
  label: string
  count?: number
  locked?: boolean
  selected: boolean
  indent?: boolean
  toggle?: ReactNode
  onSelect: () => void
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-1 rounded-md pr-2 hover:bg-muted",
        indent ? "pl-10" : "pl-1",
        selected && "bg-muted font-medium",
      )}
    >
      {toggle ?? (!indent && <span className="w-[18px]" />)}
      <button
        type="button"
        aria-current={selected ? "true" : undefined}
        onClick={onSelect}
        className="flex min-w-0 flex-1 items-center gap-1.5 py-1.5 text-left focus-visible:outline-none"
      >
        <span className="truncate">{label}</span>
        {locked && <LockIcon className="size-3 shrink-0 text-muted-foreground" aria-label="已锁定" />}
        <span className="ml-auto pl-2 text-xs text-muted-foreground tabular-nums">{count}</span>
      </button>
    </div>
  )
}
