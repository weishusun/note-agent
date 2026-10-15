import { useCallback, useEffect, useState, type ReactNode } from "react"
import { SearchIcon } from "lucide-react"

import { AskPanel } from "@/components/AskPanel"
import { CommandPalette } from "@/components/CommandPalette"
import { DuplicateDialog } from "@/components/DuplicateDialog"
import { GraphView } from "@/components/GraphView"
import { IngestPanel } from "@/components/IngestPanel"
import { MindMapView } from "@/components/MindMapView"
import { NoteDrawer } from "@/components/NoteDrawer"
import { NotesPage } from "@/components/NotesPage"
import { SettingsPanel } from "@/components/SettingsPanel"
import { Sidebar } from "@/components/Sidebar"
import { api, categoryLabel, linkTarget, type IngestJob, type NoteRef, type NoteTarget, type Tree } from "@/lib/api"
import { countOf, scopeOf, type Page, type Selection } from "@/lib/library"
import { useIngestJobs } from "@/lib/useIngestJobs"
import { cn } from "@/lib/utils"

// 一份输入处理完时角落里弹出的小提示
interface Toast {
  id: string
  text: string
  failed: boolean
}

const TOAST_MS = 6000

function App() {
  const [page, setPage] = useState<Page>("notes")
  const [selection, setSelection] = useState<Selection>({ kind: "all" })
  const [tree, setTree] = useState<Tree | null>(null)
  // 笔记有改动（编辑、删除、改分类、录入完成、处理疑似重复）的次数：各页据此重新加载
  const [notesVersion, setNotesVersion] = useState(0)
  const noteChanged = useCallback(() => setNotesVersion((v) => v + 1), [])
  // 笔记页右栏正在读的笔记；别的页面点开的笔记在侧边面板里读
  const [reading, setReading] = useState<NoteTarget | null>(null)
  const [drawer, setDrawer] = useState<NoteTarget | null>(null)
  // 等着并排对比的疑似重复笔记的 id，第一条是正在看的
  const [compare, setCompare] = useState<string[]>([])
  const [palette, setPalette] = useState(false)
  // 从 Ctrl K 面板交给提问页的问题；id 每次递增，同一个问题问两遍也会触发
  const [askRequest, setAskRequest] = useState<{ id: number; question: string } | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault()
        setPalette((open) => !open)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  useEffect(() => {
    let cancelled = false
    api.tree().then(
      (t) => !cancelled && setTree(t),
      () => undefined,
    )
    return () => {
      cancelled = true
    }
  }, [notesVersion])

  // 待整理、疑似重复清空后，那个入口会从左栏消失：别让笔记页停在一个已经选不到的范围上
  useEffect(() => {
    if (!tree) return
    if (
      (selection.kind === "inbox" && tree.inbox === 0) ||
      (selection.kind === "duplicates" && tree.duplicates === 0)
    ) {
      setSelection({ kind: "all" })
    }
  }, [tree, selection.kind])

  const openNote = useCallback(
    (note: Pick<NoteRef, "title" | "path">) => {
      const target = { title: note.title, paths: [note.path] }
      if (page === "notes") setReading(target)
      else setDrawer(target)
    },
    [page],
  )
  const closeDrawer = useCallback(() => setDrawer(null), [])
  const openCompare = useCallback((ids: string[]) => {
    setDrawer(null)
    setCompare(ids)
  }, [])
  const compareResolved = useCallback(() => {
    setNotesVersion((v) => v + 1)
    setCompare((queue) => queue.slice(1))
  }, [])
  const closeCompare = useCallback(() => setCompare([]), [])

  const createCategory = useCallback(async (path: string) => {
    await api.createCategory(path)
    setNotesVersion((v) => v + 1)
    setSelection({ kind: "category", path })
    setPage("notes")
  }, [])

  const [toasts, setToasts] = useState<Toast[]>([])
  const jobFinished = useCallback((job: IngestJob) => {
    // 新笔记进了知识库：目录和列表要跟着刷新
    setNotesVersion((v) => v + 1)
    const count = job.created.length + job.inbox.length
    const name = count > 0 ? job.created.concat(job.inbox)[0].title : job.label
    const toast: Toast = {
      id: `${job.id}-${job.finished_at}`,
      text:
        job.kind === "supplement"
          ? job.status === "failed"
            ? `${job.label}没写成，可以在录入页重试`
            : `${count} 条笔记的模型补充写好了`
          : job.status === "failed"
            ? `「${name}」录入失败`
            : `「${name}」录入完成，${count} 条笔记`,
      failed: job.status === "failed",
    }
    setToasts((list) => [...list, toast])
    setTimeout(() => setToasts((list) => list.filter((t) => t.id !== toast.id)), TOAST_MS)
  }, [])
  const ingest = useIngestJobs(notesVersion, jobFinished)
  const ingested = useCallback(
    (label: string) => {
      ingest.refresh()
      const toast: Toast = { id: `queued-${Date.now()}`, text: `${label}已加入录入队列`, failed: false }
      setToasts((list) => [...list, toast])
      setTimeout(() => setToasts((list) => list.filter((t) => t.id !== toast.id)), TOAST_MS)
    },
    [ingest],
  )

  const scope = scopeOf(selection)
  const scopeLabel = scope ? categoryLabel(scope) : "全部笔记"
  const overlay = drawer !== null || compare.length > 0

  return (
    <div className="flex h-svh flex-col bg-background text-foreground">
      <header className="flex h-12 flex-none items-center border-b bg-sidebar">
        <div className="flex w-60 flex-none items-center gap-2.5 px-5">
          <span className="grid size-7 place-items-center rounded-lg bg-foreground font-heading text-sm text-background">
            知
          </span>
          <span className="font-heading text-[15px]">知识库</span>
        </div>
        <div className="flex flex-1 justify-center px-4">
          <button
            type="button"
            onClick={() => setPalette(true)}
            className="flex h-8 w-full max-w-xl items-center gap-2.5 rounded-lg bg-background px-3 text-[13px] text-muted-foreground shadow-[inset_0_0_0_1px_var(--border)] hover:text-foreground"
          >
            <SearchIcon className="size-4" />
            搜索笔记、提问，或粘贴截图 / 网址录入
            <span className="ml-auto flex gap-1">
              <Kbd>Ctrl</Kbd>
              <Kbd>K</Kbd>
            </span>
          </button>
        </div>
        <div className="flex w-48 flex-none justify-end px-5">
          {ingest.active > 0 && (
            <button
              type="button"
              onClick={() => setPage("ingest")}
              className="flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground"
            >
              <span className="size-1.5 animate-pulse rounded-full bg-primary" />
              后台处理中 · {ingest.active}
            </button>
          )}
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <Sidebar
          page={page}
          onPage={setPage}
          tree={tree}
          selection={selection}
          onSelect={setSelection}
          activeJobs={ingest.active}
          onCreateCategory={createCategory}
        />

        {/* 录入、提问保持挂载：切换页面不丢输入、对话和队列 */}
        <main className="min-w-0 flex-1">
          <div hidden={page !== "notes"} className="h-full">
            <NotesPage
              tree={tree}
              selection={selection}
              onSelect={setSelection}
              version={notesVersion}
              target={reading}
              onTarget={setReading}
              onChanged={noteChanged}
              onCompare={openCompare}
            />
          </div>
          <div hidden={page !== "ingest"} className="h-full">
            <Column title="录入" lead="截图、文字、PDF、网址都可以。加入队列后在后台依次处理，不用等。">
              <IngestPanel
                active={page === "ingest" && !overlay && !palette}
                jobs={ingest.jobs}
                jobsError={ingest.error}
                onJobsChanged={ingest.refresh}
                onOpenNote={openNote}
                onCompare={openCompare}
              />
            </Column>
          </div>
          <div hidden={page !== "ask"} className="h-full">
            <Column title="提问" lead="先在你的知识库里找相关笔记，再基于它们回答并标出引用。答完可以接着追问。">
              <AskPanel onOpenNote={openNote} request={askRequest} />
            </Column>
          </div>
          {page === "graph" && (
            <Canvas title="图谱" scope={scopeLabel}>
              <GraphView
                category={scope}
                count={scope ? countOf(tree, selection) : (tree?.total ?? 0)}
                version={notesVersion}
                onOpenNote={openNote}
              />
            </Canvas>
          )}
          {page === "map" && (
            <Canvas title="导图" scope={scopeLabel}>
              <MindMapView tree={tree} category={scope} version={notesVersion} onOpenNote={openNote} />
            </Canvas>
          )}
          {page === "settings" && (
            <Column title="设置" lead="模型服务商、密钥和各环节用的模型。">
              <SettingsPanel />
            </Column>
          )}
        </main>
      </div>

      {palette && (
        <CommandPalette
          onClose={() => setPalette(false)}
          onAsk={(question) => {
            setAskRequest((prev) => ({ id: (prev?.id ?? 0) + 1, question }))
            setPage("ask")
          }}
          onOpenNote={(note) => {
            setReading({ title: note.title, paths: [note.path] })
            setPage("notes")
          }}
          onIngested={ingested}
        />
      )}
      {drawer && (
        <NoteDrawer
          target={drawer}
          onOpenLink={(stem) => setDrawer(linkTarget(stem))}
          onChanged={noteChanged}
          onCompare={openCompare}
          onClose={closeDrawer}
        />
      )}
      {compare.length > 0 && (
        <DuplicateDialog
          noteId={compare[0]}
          remaining={compare.length - 1}
          onResolved={compareResolved}
          onClose={closeCompare}
        />
      )}

      {/* 在录入页时队列本身就看得到，不用再弹 */}
      {page !== "ingest" && toasts.length > 0 && (
        <div className="fixed right-5 bottom-5 z-30 flex flex-col items-end gap-2">
          {toasts.map((toast) => (
            <button
              key={toast.id}
              type="button"
              role="status"
              onClick={() => setPage("ingest")}
              className={cn(
                "max-w-xs rounded-xl border bg-background px-4 py-2.5 text-left text-sm shadow-[0_12px_30px_-12px_rgba(16,32,44,0.35)] transition-colors hover:bg-muted",
                toast.failed && "border-destructive/40 text-destructive",
              )}
            >
              {toast.text}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded-[5px] bg-sidebar px-1.5 font-sans text-[11px] text-muted-foreground shadow-[inset_0_0_0_1px_var(--input)]">
      {children}
    </kbd>
  )
}

// 录入、提问、设置：居中的一栏，可以上下滚动
function Column({ title, lead, children }: { title: string; lead: string; children: ReactNode }) {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-6 px-10 py-10">
        <div>
          <h1 className="font-heading text-[26px]">{title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{lead}</p>
        </div>
        {children}
      </div>
    </div>
  )
}

// 图谱、导图：画布占满整页
function Canvas({ title, scope, children }: { title: string; scope: string; children: ReactNode }) {
  return (
    <div className="flex h-full flex-col gap-4 px-8 pt-7 pb-6">
      <div className="flex items-baseline gap-3">
        <h1 className="font-heading text-[26px]">{title}</h1>
        <span className="text-sm text-muted-foreground">{scope} · 范围跟着左边目录的选择走</span>
      </div>
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  )
}

export default App
