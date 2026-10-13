import { useCallback, useState } from "react"

import { AskPanel } from "@/components/AskPanel"
import { DuplicateDialog } from "@/components/DuplicateDialog"
import { IngestPanel } from "@/components/IngestPanel"
import { NoteDialog } from "@/components/NoteDialog"
import { NotesPanel, type NotesView } from "@/components/NotesPanel"
import { SettingsPanel } from "@/components/SettingsPanel"
import { linkTarget, type IngestJob, type NoteRef, type NoteTarget } from "@/lib/api"
import { useIngestJobs } from "@/lib/useIngestJobs"
import { cn } from "@/lib/utils"

const TABS = [
  { id: "ingest", label: "录入" },
  { id: "ask", label: "提问" },
  { id: "notes", label: "笔记" },
  { id: "settings", label: "设置" },
] as const

type TabId = (typeof TABS)[number]["id"]

// 一份输入处理完时角落里弹出的小提示
interface Toast {
  id: string
  text: string
  failed: boolean
}

const TOAST_MS = 6000

function App() {
  const [tab, setTab] = useState<TabId>("ingest")
  const [openNote, setOpenNote] = useState<NoteTarget | null>(null)
  // 笔记在弹窗里被改动（改分类、编辑、删除、处理疑似重复）的次数：各页据此重新加载
  const [notesVersion, setNotesVersion] = useState(0)
  const noteChanged = useCallback(() => setNotesVersion((v) => v + 1), [])
  const openRef = useCallback(
    (note: Pick<NoteRef, "title" | "path">) =>
      setOpenNote({ title: note.title, paths: [note.path] }),
    [],
  )
  // 笔记页当前是列表、图谱还是导图：后两种要更宽的页面
  const [notesView, setNotesView] = useState<NotesView>("list")
  const openLink = useCallback((stem: string) => setOpenNote(linkTarget(stem)), [])
  const closeNote = useCallback(() => setOpenNote(null), [])
  // 等着并排对比的疑似重复笔记的 id，第一条是正在看的
  const [compare, setCompare] = useState<string[]>([])
  const openCompare = useCallback((ids: string[]) => {
    setOpenNote(null)
    setCompare(ids)
  }, [])
  const compareResolved = useCallback(() => {
    setNotesVersion((v) => v + 1)
    setCompare((queue) => queue.slice(1))
  }, [])
  const closeCompare = useCallback(() => setCompare([]), [])

  const [toasts, setToasts] = useState<Toast[]>([])
  const jobFinished = useCallback((job: IngestJob) => {
    // 新笔记进了知识库：笔记页的目录和列表要跟着刷新
    setNotesVersion((v) => v + 1)
    const count = job.created.length + job.inbox.length
    const name = count > 0 ? job.created.concat(job.inbox)[0].title : job.label
    const toast: Toast = {
      id: `${job.id}-${job.finished_at}`,
      text: job.status === "failed" ? `「${name}」录入失败` : `「${name}」录入完成，${count} 条笔记`,
      failed: job.status === "failed",
    }
    setToasts((list) => [...list, toast])
    setTimeout(() => setToasts((list) => list.filter((t) => t.id !== toast.id)), TOAST_MS)
  }, [])
  const ingest = useIngestJobs(notesVersion, jobFinished)

  return (
    <div
      className={cn(
        "mx-auto flex min-h-svh flex-col gap-6 px-4 py-8",
        // 笔记页左边有目录树，需要更宽；图谱和导图是画布，再宽一些
        tab !== "notes" ? "max-w-2xl" : notesView === "list" ? "max-w-5xl" : "max-w-7xl",
      )}
    >
      <header className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">note-agent</h1>
        <nav role="tablist" className="flex gap-1 rounded-lg bg-muted p-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                "rounded-md px-3 py-1 text-sm font-medium text-muted-foreground transition-colors focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
                tab === t.id && "bg-background text-foreground shadow-sm",
              )}
            >
              {t.label}
              {t.id === "ingest" && ingest.active > 0 && (
                <span
                  className="ml-1.5 rounded-full bg-primary px-1.5 text-xs text-primary-foreground tabular-nums"
                  aria-label={`${ingest.active} 份正在处理或排队`}
                >
                  {ingest.active}
                </span>
              )}
            </button>
          ))}
        </nav>
      </header>

      {/* 面板都保持挂载，切换标签不丢输入和结果 */}
      <main>
        <div hidden={tab !== "ingest"}>
          <IngestPanel
            active={tab === "ingest" && openNote === null && compare.length === 0}
            jobs={ingest.jobs}
            jobsError={ingest.error}
            onJobsChanged={ingest.refresh}
            onOpenNote={openRef}
            onCompare={openCompare}
          />
        </div>
        <div hidden={tab !== "ask"}>
          <AskPanel onOpenNote={openRef} />
        </div>
        <div hidden={tab !== "notes"}>
          <NotesPanel
            active={tab === "notes"}
            version={notesVersion}
            view={notesView}
            onViewChange={setNotesView}
            onOpenNote={openRef}
            onCompare={openCompare}
          />
        </div>
        <div hidden={tab !== "settings"}>
          <SettingsPanel active={tab === "settings"} />
        </div>
      </main>

      {openNote && (
        <NoteDialog
          target={openNote}
          onOpenLink={openLink}
          onChanged={noteChanged}
          onCompare={openCompare}
          onClose={closeNote}
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
      {tab !== "ingest" && toasts.length > 0 && (
        <div className="fixed right-4 bottom-4 z-40 flex flex-col items-end gap-2">
          {toasts.map((toast) => (
            <button
              key={toast.id}
              type="button"
              role="status"
              onClick={() => setTab("ingest")}
              className={cn(
                "max-w-xs rounded-lg border bg-background px-3 py-2 text-left text-sm shadow-lg transition-colors hover:bg-muted",
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

export default App
