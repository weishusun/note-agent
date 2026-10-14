import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react"
import {
  FilePlusIcon,
  FileTextIcon,
  LockIcon,
  PencilIcon,
  RefreshCwIcon,
  ShuffleIcon,
  SparklesIcon,
  Trash2Icon,
} from "lucide-react"

import { Alert } from "@/components/Alert"
import { NewNoteDialog } from "@/components/NewNoteDialog"
import { NoteReader } from "@/components/NoteReader"
import { Button } from "@/components/ui/button"
import { api, categoryLabel, linkTarget, type NoteRef, type NoteTarget, type Tree } from "@/lib/api"
import { categoryColor, findCategory, type Selection } from "@/lib/library"
import { cn } from "@/lib/utils"

const JOB_POLL_MS = 600
const NAME_MAX = 12

type TypeFilter = "all" | "topic" | "concept"

// 笔记页：中间是所选范围的笔记列表，右边直接读选中的那条
export function NotesPage({
  tree,
  selection,
  onSelect,
  version,
  target,
  onTarget,
  onChanged,
  onCompare,
}: {
  tree: Tree | null
  selection: Selection
  onSelect: (selection: Selection) => void
  // 笔记在别处被改动时递增：列表跟着重新加载
  version: number
  // 右栏正在读的笔记；App 持有它，Ctrl K 里点开的笔记也落在这里
  target: NoteTarget | null
  onTarget: (target: NoteTarget | null) => void
  onChanged: () => void
  onCompare: (ids: string[]) => void
}) {
  const [notes, setNotes] = useState<NoteRef[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<TypeFilter>("all")
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const [confirmingAll, setConfirmingAll] = useState(false)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const reload = useCallback(() => setReloadKey((k) => k + 1), [])
  const selectedPath = selection.kind === "category" ? selection.path : null

  useEffect(() => {
    let cancelled = false
    api
      .notes(
        selection.kind === "inbox"
          ? { status: "inbox" }
          : selection.kind === "duplicates"
            ? { duplicates: "true" }
            : selectedPath
              ? { category: selectedPath }
              : {},
      )
      .then(
        (result) => {
          if (cancelled) return
          setNotes(result)
          setError(null)
        },
        (e: Error) => !cancelled && setError(e.message),
      )
    return () => {
      cancelled = true
    }
  }, [selection.kind, selectedPath, version, reloadKey])

  useEffect(() => {
    setFilter("all")
    setRenaming(null)
    setConfirmingAll(false)
  }, [selection.kind, selectedPath])

  async function run(action: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
      onChanged()
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
      onChanged()
      reload()
    }
  }

  function submitRename(e: FormEvent) {
    e.preventDefault()
    const value = renaming?.trim()
    if (!value || !selectedPath) return
    const parent = selectedPath.includes("/") ? selectedPath.split("/")[0] : null
    void run(async () => {
      await api.renameCategory(selectedPath, value)
      onSelect({ kind: "category", path: parent ? `${parent}/${value}` : value })
      setRenaming(null)
    })
  }

  const selected = selectedPath ? findCategory(tree, selectedPath) : null
  const disabled = busy || progress !== null
  const shown = (notes ?? []).filter((n) => filter === "all" || n.type === filter)
  const topics = notes?.filter((n) => n.type === "topic").length ?? 0
  const title =
    selection.kind === "all"
      ? "全部笔记"
      : selection.kind === "inbox"
        ? "待整理"
        : selection.kind === "duplicates"
          ? "疑似重复"
          : (selectedPath?.split("/").pop() ?? "")
  const crumb = selectedPath?.includes("/") ? selectedPath.split("/")[0] : "知识库"

  const open = (note: NoteRef) =>
    selection.kind === "duplicates" && note.id
      ? onCompare([note.id])
      : onTarget({ title: note.title, paths: [note.path] })

  return (
    <div className="flex h-full min-h-0">
      <section className="flex w-[340px] flex-none flex-col border-r bg-pane" aria-label="笔记列表">
        <header className="px-5 pt-5 pb-3">
          <div className="flex items-center gap-1.5 text-[11px] tracking-[0.14em] text-muted-foreground/80">
            {selectedPath && !selectedPath.includes("/") && (
              <span className="size-1.5 rounded-full" style={{ background: categoryColor(selectedPath) }} />
            )}
            {crumb}
          </div>
          {renaming !== null && selectedPath ? (
            <form onSubmit={submitRename} className="mt-1.5 flex flex-col gap-1.5">
              <input
                autoFocus
                value={renaming}
                maxLength={NAME_MAX}
                onChange={(e) => setRenaming(e.target.value)}
                onKeyDown={(e) => e.key === "Escape" && setRenaming(null)}
                aria-label="分类的新名字"
                className="h-9 rounded-md border bg-background px-2 font-heading text-lg outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/40"
              />
              <p className="text-xs text-muted-foreground">改成同一层里已有的名字，就是把这个分类并进去。回车保存，Esc 取消。</p>
            </form>
          ) : (
            <div className="mt-0.5 flex items-center gap-2">
              <h1 className="min-w-0 flex-1 truncate font-heading text-xl">{title}</h1>
              {selected?.locked && (
                <LockIcon className="size-3.5 text-muted-foreground" aria-label="你建的或改过名的分类，模型不会改动它" />
              )}
            </div>
          )}

          <div className="mt-3 flex items-center gap-1">
            <div className="flex flex-1 gap-1.5">
              <Chip on={filter === "all"} onClick={() => setFilter("all")}>全部 {notes?.length ?? ""}</Chip>
              {topics > 0 && (
                <>
                  <Chip on={filter === "topic"} onClick={() => setFilter("topic")}>主题 {topics}</Chip>
                  <Chip on={filter === "concept"} onClick={() => setFilter("concept")}>概念 {(notes?.length ?? 0) - topics}</Chip>
                </>
              )}
            </div>
            <Tool label="新建笔记" disabled={disabled} onClick={() => setCreating(true)}>
              <FilePlusIcon />
            </Tool>
            {selectedPath && (
              <>
                <Tool label="给分类改名" disabled={disabled} onClick={() => setRenaming(selectedPath.split("/").pop() ?? "")}>
                  <PencilIcon />
                </Tool>
                <Tool
                  label={selected && selected.count > 0 ? "只能删除没有笔记的分类" : "删除这个分类"}
                  disabled={disabled || !selected || selected.count > 0 || notes?.length !== 0}
                  onClick={() =>
                    void run(async () => {
                      await api.deleteCategory(selectedPath)
                      onSelect({ kind: "all" })
                    })
                  }
                >
                  <Trash2Icon />
                </Tool>
              </>
            )}
            {selection.kind === "all" && (
              <Tool label="全部重排：让模型重新设计分类" disabled={disabled} onClick={() => setConfirmingAll(true)}>
                <ShuffleIcon />
              </Tool>
            )}
            <Tool label="同步：在应用之外改过笔记文件后点这里" disabled={disabled} onClick={() => void run(() => api.reindex())}>
              <RefreshCwIcon className={busy ? "animate-spin" : undefined} />
            </Tool>
          </div>

          <div className="mt-3 flex flex-col gap-2 empty:hidden">
            {selection.kind === "inbox" && (
              <Alert tone="info">
                <span className="flex-1 text-xs">模型拿不准这些该放哪。点开一条确认它猜的分类，或者</span>
                <Button size="xs" variant="outline" disabled={disabled} onClick={() => void reclassify("unsorted")}>
                  <SparklesIcon />
                  再归类一次
                </Button>
              </Alert>
            )}
            {selection.kind === "duplicates" && (
              <Alert tone="info">
                <span className="flex-1 text-xs">和更早的某条同名或内容很接近。点开一条并排对比。</span>
                {!!notes?.length && (
                  <Button size="xs" variant="outline" onClick={() => onCompare(notes.flatMap((n) => (n.id ? [n.id] : [])))}>
                    逐条对比
                  </Button>
                )}
              </Alert>
            )}
            {confirmingAll && (
              <Alert tone="info">
                <span className="flex-1 text-xs">模型会重新设计分类并给所有未锁定的笔记重新归类，目录可能大变样。</span>
                <Button size="xs" onClick={() => void reclassify("all")}>开始</Button>
                <Button size="xs" variant="ghost" onClick={() => setConfirmingAll(false)}>取消</Button>
              </Alert>
            )}
            {progress && (
              <Alert tone="info">
                正在归类{progress.total > 0 ? ` ${progress.done} / ${progress.total}` : "…"}
              </Alert>
            )}
            {error && <Alert tone="error">{error}</Alert>}
          </div>
        </header>

        <ul className="min-h-0 flex-1 overflow-y-auto border-t">
          {notes?.length === 0 && (
            <li className="px-5 py-8 text-sm text-muted-foreground">
              {selection.kind === "all" ? "知识库还是空的。到「录入」粘贴一张截图，添加第一条笔记。" : "这里还没有笔记。"}
            </li>
          )}
          {shown.map((note) => (
            <NoteListItem
              key={note.path}
              note={note}
              showCategory={!selectedPath || !selectedPath.includes("/")}
              selected={target?.paths[0] === note.path}
              onOpen={() => open(note)}
            />
          ))}
        </ul>
      </section>

      <div className="min-w-0 flex-1 overflow-y-auto bg-background">
        {target ? (
          <NoteReader
            key={target.paths.join("|")}
            target={target}
            onOpenLink={(stem) => onTarget(linkTarget(stem))}
            onChanged={() => {
              onChanged()
              reload()
            }}
            onCompare={onCompare}
            onDeleted={() => onTarget(null)}
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
            <FileTextIcon className="size-8 opacity-40" />
            选一条笔记来阅读
          </div>
        )}
      </div>

      {creating && (
        <NewNoteDialog
          tree={tree}
          initialCategory={selectedPath?.includes("/") ? selectedPath : null}
          onClose={() => setCreating(false)}
          onCreated={(note) => {
            setCreating(false)
            onChanged()
            reload()
            onTarget({ title: note.title, paths: [note.path] })
          }}
        />
      )}
    </div>
  )
}

function NoteListItem({
  note,
  selected,
  showCategory,
  onOpen,
}: {
  note: NoteRef
  selected: boolean
  showCategory: boolean
  onOpen: () => void
}) {
  return (
    <li className="border-b">
      <button
        type="button"
        onClick={onOpen}
        aria-current={selected ? "true" : undefined}
        className={cn(
          "relative flex w-full gap-3 px-5 py-3.5 text-left outline-none hover:bg-muted/60 focus-visible:bg-muted/60",
          selected && "bg-background hover:bg-background",
        )}
      >
        {selected && <span className="absolute top-3 bottom-3 left-0 w-0.5 rounded-full bg-primary" />}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-semibold">{note.title}</span>
          <span className="mt-0.5 line-clamp-2 text-[12.5px] leading-relaxed text-muted-foreground">
            {note.summary}
          </span>
          <span className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground/80">
            {note.type === "topic" && <Pill>主题</Pill>}
            {note.duplicate_of && <Pill tone="accent">疑似重复</Pill>}
            {note.status === "inbox" && <Pill>待整理</Pill>}
            {showCategory && note.category && <span>{categoryLabel(note.category)}</span>}
            <span>{note.updated.slice(5, 10)}</span>
          </span>
        </span>
      </button>
    </li>
  )
}

function Pill({ tone, children }: { tone?: "accent"; children: ReactNode }) {
  return (
    <span
      className={cn(
        "rounded px-1.5 text-[10.5px] leading-4 ring-1 ring-input ring-inset",
        tone === "accent" && "bg-accent text-accent-foreground ring-primary/40",
      )}
    >
      {children}
    </span>
  )
}

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className={cn(
        "rounded-full px-2.5 py-0.5 text-xs whitespace-nowrap text-muted-foreground ring-1 ring-input ring-inset hover:text-foreground",
        on && "bg-foreground text-background ring-0 hover:text-background",
      )}
    >
      {children}
    </button>
  )
}

function Tool({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string
  disabled?: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="text-muted-foreground hover:text-foreground"
    >
      {children}
    </Button>
  )
}
